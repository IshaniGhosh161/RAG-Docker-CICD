import os
import sys
from typing import Literal, List
from typing_extensions import TypedDict
import requests
from dotenv import load_dotenv
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager
import logging_config
from config import ENABLE_RERANKER, FAST_MODE, RETRIEVAL_K, RERANK_TOP_N

logger = logging.getLogger(__name__)

from langchain_community.vectorstores import FAISS
from langchain_ollama import OllamaEmbeddings, ChatOllama
from langchain_groq import ChatGroq
from langchain_tavily import TavilySearch
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from pydantic import BaseModel, Field
from langgraph.graph import END, StateGraph, START
from ratelimit import limits, sleep_and_retry
from tenacity import retry, wait_exponential, stop_after_attempt

load_dotenv()

os.environ['OLLAMA_HOST'] = os.getenv('OLLAMA_HOST', 'http://localhost:11434')
os.environ["OLLAMA_API_KEY"] = os.getenv("OLLAMA_API_KEY")
os.environ['TAVILY_API_KEY'] = os.getenv("TAVILY_API_KEY", '')
# os.environ['GROQ_API_KEY'] = os.getenv("GROQ_API_KEY", '')

ONE_MINUTE = 60
MAX_CALLS_PER_MINUTE = 15

class GraphState(TypedDict):
    question: str
    generation: str
    documents: List[Document]
    history: List[dict]
    session_id: str
    retry_count: int
    source: str

class SentenceTransformerReranker:
    def __init__(self, model_name="cross-encoder/ms-marco-MiniLM-L-6-v2"):
        import torch
        from sentence_transformers import CrossEncoder

        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        self.model = CrossEncoder(
            model_name,
            device=self.device,
            max_length=512
        )

        logger.info("Loaded reranker: %s on %s", model_name, self.device)

    def rerank(self, query: str, documents: list, top_n: int = 5):
        if not documents:
            return []

        pairs = [(query, doc.page_content) for doc in documents]

        scores = self.model.predict(pairs, batch_size=16,show_progress_bar=False)

        ranked_docs = sorted(
            zip(documents, scores),
            key=lambda x: x[1],
            reverse=True
        )

        return [doc for doc, score in ranked_docs[:top_n]]
    
class Agent:
    def __init__(self, username: str):
        self.username = username
        self.embedding = OllamaEmbeddings(model="nomic-embed-text")
        self.reranker = (
            SentenceTransformerReranker(model_name="cross-encoder/ms-marco-MiniLM-L-6-v2")
            if ENABLE_RERANKER
            else None
        )
        
        # Safe loading of FAISS index
        faiss_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'index', 'faiss_index')
        if os.path.exists(faiss_path):
            self.vector_store = FAISS.load_local(
                faiss_path, 
                self.embedding, 
                allow_dangerous_deserialization=True,
                normalize_L2=True
            )
            self.retriever = self.vector_store.as_retriever(
                search_kwargs={"k": RETRIEVAL_K}
            )
        else:
            self.retriever = None

        # self.llm = ChatGoogleGenerativeAI(
        #     model="gemini-1.5-flash", 
        #     temperature=0,
        #     retry_on_failure=True,
        #     max_retries=3
        # )
        self.llm = ChatOllama(model="gpt-oss:20b-cloud", max_retries=3,temperature=0,keep_alive=True)
        self.fast_llm = ChatOllama(model="gpt-oss:20b-cloud", max_retries=3,temperature=0,keep_alive=True)
        self.web_search_tool = TavilySearch(k=3)
        self.db = DatabaseManager()
        self.app = self._build_workflow()

    def _load_chat_history(self, session_id: str, max_messages: int = 5):
        messages = self.db.get_chat_messages(session_id)
        if not messages:
            return []
        # The API saves the current question before starting the agent.
        context = messages[-max_messages:]
        return [{"user": msg["username"], "message": msg["message"]} for msg in context]

    def _format_chat_history(self, history: List[dict]) -> str:
        return "\n".join(
            f"{item['user']}: {item['message']}" for item in history
        ) or "No previous conversation."

    def _build_workflow(self):
        workflow = StateGraph(GraphState)

        workflow.add_node("build_query", self._build_query)
        workflow.add_node("web_search", self._web_search)
        workflow.add_node("call_llm", self._call_llm)
        workflow.add_node("retrieve", self._retrieve)
        workflow.add_node("rerank", self._rerank_documents)
        workflow.add_node("grade_documents", self._grade_documents)
        workflow.add_node("generate", self._generate)
        workflow.add_node("transform_query", self._transform_query)

        workflow.add_edge(START, "build_query")
        
        workflow.add_conditional_edges(
            "build_query",
            self._route_question,
            {
                "web-search": "web_search",
                "vectorstore": "retrieve",
                "llm": "call_llm"
            },
        )
        
        workflow.add_edge("web_search", "generate")
        workflow.add_edge("retrieve","rerank")
        workflow.add_edge("rerank","grade_documents")
        
        workflow.add_conditional_edges(
            "grade_documents",
            self._decide_to_generate,
            {
                "transform": "transform_query",
                "no relevant document": "web_search",
                "found relevant document": "generate",
            },
        )
        
        workflow.add_conditional_edges(
            "transform_query",
            self._route_question,
            {
                "web-search": "web_search",
                "vectorstore": "retrieve",
                "llm": "call_llm"
            },
        )
        
        workflow.add_conditional_edges(
            "generate",
            self._after_generate,
            {
                "final": END,
                "grade": "grade_generation"
            },
        )
        
        workflow.add_node(
            "grade_generation",
            self._grade_generation_node
        )
        
        workflow.add_conditional_edges(
            "grade_generation",
            self._grade_generation_v_documents_and_question,
            {
                "not supported": "call_llm",
                "useful": END,
                "not useful": "call_llm",
            },
        )
        
        workflow.add_edge("call_llm", END)
        
        graph = workflow.compile()
        
        # graph_png = graph.get_graph()

        # png_bytes = graph_png.draw_mermaid_png()

        # with open("graph_image.png", "wb") as f:
        #     f.write(png_bytes)

        # print("Graph saved as graph_image.png")

        return graph

    def _build_query(self, state: GraphState):
        prompt = """
You are an expert query rewriting assistant for a Retrieval-Augmented Generation (RAG) system.

Your goal is to rewrite the user's latest question into a single, self-contained search query.

Instructions:
- Use the conversation history only if the current question depends on previous messages.
- Resolve pronouns such as "it", "they", "that", "those", "he", "she", etc.
- Preserve the user's original intent.
- Do NOT answer the question.
- Do NOT add new information or assumptions.
- Do NOT change technical terms.
- If the question is already complete and unambiguous, return it unchanged.
- Return ONLY the rewritten query.

Conversation History:
{history}

Latest User Question:
{question}

Rewritten Query:
"""
        query_build_prompt = ChatPromptTemplate.from_template(prompt)
        question_build = query_build_prompt | self.fast_llm | StrOutputParser()

        question = state["question"]
        session_id = state.get("session_id", "")
        history = self._load_chat_history(session_id) if session_id else []
        better_question = question_build.invoke({
            "question": question,
            "history": self._format_chat_history(history),
        })
        logger.info("Built query: %s", better_question)
        return {
            "history": history, 
            "question": better_question, 
            "session_id": session_id,
            "retry_count": state.get("retry_count", 0)
        }

    def _route_question(self, state: GraphState):
        class RouteQuery(BaseModel):
            datasource: Literal["vectorstore", "web-search", "llm"] = Field(
                ..., description="Route user question to web search, vectorstore, or direct LLM."
            )

        structured_llm_router = self.llm.with_structured_output(RouteQuery, method="json_mode")
        system = """
You are a routing assistant for a Retrieval-Augmented Generation (RAG) application.

Your job is to choose exactly ONE datasource.

Available datasources:

1. vectorstore
Use when the question is related to the following topics:
- LangChain
- LangGraph
- AI Agents
- Prompt Engineering

2. web-search
Use when the question requires:
- Current events
- Recent news
- Live information
- Sports
- Weather
- Stock prices
- Internet facts
- Information after the knowledge base cutoff
- Information unlikely to exist in the vector database

3. llm
Use when no retrieval is required.
Examples:
- Greetings
- General conversation
- Creative writing
- Summarization
- Translation
- Grammar correction
- Coding help
- Math
- Explanations based on general knowledge

Rules:
- Prefer vectorstore when question is related to the above mentioned topics.
- Use web-search ONLY if fresh or external information is required.
- Use llm for generic query.

Return ONLY valid JSON.

The JSON must exactly follow this format:

{{
  "datasource": "vectorstore"
}}

Allowed values:
- vectorstore
- web-search
- llm

Do not return explanations.
Do not return markdown.
Do not return plain text.

"""
        
        route_prompt = ChatPromptTemplate.from_messages([
            ("system", system),
            ("human", "{question}")
        ])
        
        question_router = route_prompt | structured_llm_router
        source = question_router.invoke({"question": state["question"]})
        logger.info("Routing decision: %s", source.datasource)
        if source.datasource == "vectorstore" and not self.retriever:
            return "web-search"
            
        return source.datasource

    def _web_search(self, state: GraphState):
        question = state["question"]
        docs = self.web_search_tool.invoke({"query": question})
        
        web_content = "\n".join([d.get("content", "") for d in docs]) if isinstance(docs, list) else str(docs)
        logger.info("Web search returned %d characters", len(web_content))
        web_documents = [Document(page_content=web_content)]
        
        return {
        "documents": web_documents,
        "question": question,
        "source": "web-search"
    }

    def _call_llm(self, state: GraphState):
        question = state["question"]
        prompt = f"Answer the user query concisely:\nQuestion: {question}\nAnswer:"
        response = self.llm.invoke(prompt)
        return {"question": question, "generation": response.content, "source": "llm"}

    def _retrieve(self, state: GraphState):
        question = state["question"]
        if not self.retriever:
            return {"documents": [], "question": question, "source": "vectorstore"}
        documents = self.retriever.invoke(question)
        logger.info("Retrieved %d documents from vectorstore for question: %s", len(documents), question)
        return {"documents": documents, "question": question, "source": "vectorstore","retry_count": state.get("retry_count",0)}

    def _rerank_documents(self, state: GraphState):
        question = state["question"]
        documents = state.get("documents", [])

        if documents and self.reranker:
            try:
                documents = self.reranker.rerank(
                    query=question,
                    documents=documents,
                    top_n=RERANK_TOP_N,
                )
                logger.info("Reranked %d documents", len(documents))
            except Exception as e:
                logger.exception("Rerank failed: %s", e)

        return {
            "documents": documents,
            "question": question,
            "source": "vectorstore",
            "retry_count": state.get("retry_count", 0),
        }

    def _grade_documents(self, state: GraphState):
        class GradeDocuments(BaseModel):
            binary_score: Literal["yes", "no"]

        structured_llm_grader = self.llm.with_structured_output(GradeDocuments, method="json_mode")
        system = """
You are a document relevance grader.

Return ONLY JSON:

{{
  "binary_score": "yes"
}}

or

{{
  "binary_score": "no"
}}

No explanation.
"""
        
        grade_prompt = ChatPromptTemplate.from_messages([
            ("system", system),
            ("human", "Retrieved document: {document}\nUser question: {question}")
        ])
        
        retrieval_grader = grade_prompt | structured_llm_grader
        question = state["question"]
        documents = state.get("documents", [])

        filtered_docs = []
        for d in documents:
            score = retrieval_grader.invoke({"question": question, "document": d.page_content})
            logger.info("Relevance check: %s", score.binary_score)
            if score.binary_score.lower() == "yes":
                filtered_docs.append(d)

        return {"documents": filtered_docs, "question": question}

    def _decide_to_generate(self, state: GraphState):
        retry_count = state.get("retry_count", 0)

        if not state.get("documents"):
            if retry_count >= 2:
                return "no relevant document"

            return "transform"

        return "found relevant document"

    def _generate(self, state: GraphState):
        prompt_text = """Context: {context}
    Question: {question}
Answer contextually:"""
        prompt = ChatPromptTemplate.from_template(template=prompt_text)
        
        def format_docs(docs):
            return "\n\n".join(doc.page_content for doc in docs)

        rag_chain = prompt | self.llm | StrOutputParser()
        context_str = format_docs(state.get("documents", []))
        generation = rag_chain.invoke({"context": context_str, "question": state["question"]})
        logger.info("Generated answer with %d characters", len(generation))
        return {"documents": state["documents"], "question": state["question"], "generation": generation, "source": state.get("source", "")}
    
    def _after_generate(self, state: GraphState):
        if FAST_MODE or state.get("source") == "web-search":
            return "final"

        return "grade"

    def _grade_generation_node(self, state: GraphState):
        return {
            "question": state["question"],
            "documents": state.get("documents", []),
            "generation": state.get("generation", ""),
            "source": state.get("source", "")
        }
    def _grade_generation_v_documents_and_question(self, state: GraphState):
        class GradeHallucinations(BaseModel):
            binary_score: Literal["yes", "no"]

        class GradeAnswer(BaseModel):
            binary_score: Literal["yes", "no"]

        hallucination_grader = (
            ChatPromptTemplate.from_messages([
                ("system", """Is the answer grounded in the facts? Return ONLY JSON:

{{
  "binary_score": "yes"
}}

or

{{
  "binary_score": "no"
}}
"""),
                ("human", "Facts: {documents}\nAnswer: {generation}")
            ]) 
            | self.llm.with_structured_output(GradeHallucinations, method="json_mode")
        )

        answer_grader = (
            ChatPromptTemplate.from_messages([
                ("system", """Does the answer address the question? Return ONLY JSON:
{{
  "binary_score": "yes"
}}

or

{{
  "binary_score": "no"
}}
"""),
                ("human", "Question: {question}\nAnswer: {generation}")
            ]) 
            | self.llm.with_structured_output(GradeAnswer, method="json_mode")
        )

        documents = "\n".join([d.page_content for d in state.get("documents", [])])
        generation = state.get("generation", "")
        question = state["question"]

        h_score = hallucination_grader.invoke({"documents": documents, "generation": generation})
        logger.info("Hallucination check: %s", h_score.binary_score)
        if h_score.binary_score.lower() == "yes":
            a_score = answer_grader.invoke({"question": question, "generation": generation})
            logger.info("Answer relevance check: %s", a_score.binary_score)
            if a_score.binary_score.lower() == "yes":
                return "useful"
            return "not useful"
        return "not supported"

    def _transform_query(self, state: GraphState):
        system = """
You are an expert search query optimizer.

Rewrite the user's question to maximize retrieval quality.

Rules:
- Preserve the original meaning.
- Expand abbreviations where appropriate.
- Include important keywords.
- Remove unnecessary conversational words.
- Make the query concise and specific.
- Do not answer the question.
- Return only the rewritten search query.
"""
        re_write_prompt = ChatPromptTemplate.from_messages([
            ("system", system),
            ("human", "Question: {question}")
        ])
        question_rewriter = re_write_prompt | self.llm | StrOutputParser()
        better_question = question_rewriter.invoke({"question": state["question"]})
        retry_count = state.get("retry_count", 0) + 1

        logger.info("Transformed query attempt %d: %s", retry_count, better_question)
        return {
            "documents": [],
            "question": better_question,
            "retry_count": retry_count,
            "source": "vectorstore"
        }

    def generate_bot_response(self, session_id: str, user_message: str):
        try:
            inputs = {
                "question": user_message,
                "session_id": session_id,
                "retry_count": 0
            }
            
            final_generation = None
            
            for output in self.app.stream(inputs):
                for node_name, node_state in output.items():
                    if isinstance(node_state, dict) and "generation" in node_state:
                        final_generation = node_state["generation"]
            
            if final_generation:
                return final_generation
            return "I was unable to find an appropriate response."

        except Exception as e:
            logger.exception("Error in generate_bot_response: %s", e)
            return f"Error generating response: {str(e)}"

    def stream_bot_response(self, session_id: str, user_message: str):
        logger.info("Streaming agent response for session %s", session_id)
        if not FAST_MODE:
            logger.info("FAST_MODE disabled; generating one complete response")
            yield self.generate_bot_response(session_id, user_message)
            return

        state = {
            "question": user_message,
            "session_id": session_id,
            "retry_count": 0,
        }
        state.update(self._build_query(state))
        route = self._route_question(state)

        if route == "llm":
            prompt = f"Answer the user query concisely:\nQuestion: {state['question']}\nAnswer:"
        else:
            if route == "web-search":
                state.update(self._web_search(state))
            else:
                state.update(self._retrieve(state))
                state.update(self._rerank_documents(state))
                state.update(self._grade_documents(state))
                if not state.get("documents"):
                    state.update(self._web_search(state))

            prompt = ChatPromptTemplate.from_template(
                "Context: {context}\nQuestion: {question}\nAnswer contextually:"
            ).format(
                context="\n\n".join(
                    document.page_content for document in state.get("documents", [])
                ),
                question=state["question"],
            )

        chunk_count = 0
        for chunk in self.llm.stream(prompt):
            content = getattr(chunk, "content", "")
            if content:
                chunk_count += 1
                yield content
        logger.info("Agent stream finished for session %s with %d chunks", session_id, chunk_count)