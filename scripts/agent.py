import os
import sys
from typing import Literal, List
from typing_extensions import TypedDict
import requests
from dotenv import load_dotenv
import logging
import opentelemetry
import opentelemetry.trace as trace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import DatabaseManager
from backend import logging_config
from backend.config import (
    ENABLE_RERANKER, 
    RETRIEVAL_K, 
    RERANK_TOP_N,
    OLLAMA_HOST,
    OLLAMA_MODEL,
    OLLAMA_API_KEY,
    FAST_MODE,
    HF_EMBEDDING_MODEL,
    HF_EMBEDDING_DEVICE,
    HF_EMBEDDING_BATCH_SIZE,
    TAVILY_API_KEY
)
from backend.observability import (
    RAG_WEB_SEARCH_TOTAL,
    RAG_ROUTE_DECISIONS_TOTAL,
    RAG_RETRIEVAL_DOCUMENTS_COUNT,
    RAG_RETRIEVAL_RELEVANCE_SCORE,
    measure_stage_latency,
    observe_stage_latency,
    record_llm_usage,
)
from backend.embeddings import NomicEmbeddings
logger = logging.getLogger(__name__)

from langchain_community.vectorstores import FAISS
from langchain_ollama import ChatOllama
from langchain_tavily import TavilySearch
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from pydantic import BaseModel, Field
from langgraph.graph import END, StateGraph, START
from opentelemetry import trace
from ratelimit import limits, sleep_and_retry
from tenacity import retry, wait_exponential, stop_after_attempt

load_dotenv()

# Set Tavily API Key for the tool to use
os.environ["TAVILY_API_KEY"] = TAVILY_API_KEY

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
        self.tracer = trace.get_tracer(__name__)
        self.username = username
        self.prompts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'prompts')
        self.embedding = NomicEmbeddings(
            model_name=HF_EMBEDDING_MODEL,
            device=HF_EMBEDDING_DEVICE,
            batch_size=HF_EMBEDDING_BATCH_SIZE
        )
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
            self.retriever = (self.vector_store.as_retriever(
                search_kwargs={"k": RETRIEVAL_K}
            ))
        else:
            logger.warning(
                "FAISS index not found at: %s",
                faiss_path,
            )
            self.retriever = None
            self.vector_store = None

        self.llm = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_HOST, max_retries=3,temperature=0,keep_alive=True)
        self.fast_llm = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_HOST, max_retries=3,temperature=0,keep_alive=True)
        self.web_search_tool = TavilySearch(k=3)
        self.db = DatabaseManager()
        self.app = self._build_workflow()

        logger.info("Ollama LLM configured")

        logger.info("Ollama host: %s",OLLAMA_HOST)

        logger.info("Ollama model: %s", OLLAMA_MODEL)


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
    
    def _load_prompt(self, filename: str) -> str:
        path = os.path.join(self.prompts_dir, filename)
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()

    @staticmethod
    def _record_llm_call(prompt: str | None = None, response_text: str | None = None) -> None:
        record_llm_usage(prompt, response_text)

    @observe_stage_latency("build_query")
    def _build_query(self, state: GraphState):
        with self.tracer.start_as_current_span("build_query") as span:
            prompt = self._load_prompt('build_query.txt')
            query_build_prompt = ChatPromptTemplate.from_template(prompt)
            question_build = query_build_prompt | self.fast_llm | StrOutputParser()

            question = state["question"]
            session_id = state.get("session_id", "")
            history = self._load_chat_history(session_id) if session_id else []
            history_text = self._format_chat_history(history)
            prompt_text = prompt.format(question=question, history=history_text)
            better_question = question_build.invoke({
                "question": question,
                "history": history_text,
            })
            self._record_llm_call(prompt=prompt_text, response_text=str(better_question))
            logger.info("Built query: %s", better_question)
            span.set_attribute("rewritten_query", str(better_question))
            return {
                "history": history, 
                "question": better_question, 
                "session_id": session_id,
                "retry_count": state.get("retry_count", 0)
            }

    @observe_stage_latency("route")
    def _route_question(self, state: GraphState):
        with self.tracer.start_as_current_span("route_question") as span:
            class RouteQuery(BaseModel):
                datasource: Literal["vectorstore", "web-search", "llm"] = Field(
                    ..., description="Route user question to web search, vectorstore, or direct LLM."
                )

            structured_llm_router = self.llm.with_structured_output(RouteQuery, method="json_mode")
            system = self._load_prompt('route_query.txt')
            
            route_prompt = ChatPromptTemplate.from_messages([
                ("system", system),
                ("human", "{question}")
            ])
            
            question_router = route_prompt | structured_llm_router
            route_prompt_text = route_prompt.format(question=state["question"])
            try:
                source = question_router.invoke({"question": state["question"]})
            except Exception as e:
                logger.error("Routing failed with error: %s. Defaulting to llm.", e)
                span.set_attribute("routed_datasource", "llm (fallback)")
                return "llm"
            
            self._record_llm_call(prompt=route_prompt_text, response_text=str(source.datasource))
            logger.info("Routing decision: %s", source.datasource)
            span.set_attribute("routed_datasource", source.datasource)
            if state.get("retry_count", 0) > 0 and source.datasource != "vectorstore":
                logger.info("Rewritten query is not a vectorstore query; using web search")
                span.set_attribute("routed_datasource", "web-search")
                RAG_ROUTE_DECISIONS_TOTAL.labels(route="web-search").inc()
                return "web-search"
            if source.datasource == "vectorstore" and not self.retriever:
                RAG_ROUTE_DECISIONS_TOTAL.labels(route="web-search").inc()
                return "web-search"

            RAG_ROUTE_DECISIONS_TOTAL.labels(route=source.datasource).inc()
            return source.datasource

    @observe_stage_latency("web_search")
    def _web_search(self, state: GraphState):
        with self.tracer.start_as_current_span("web_search"):
            question = state["question"]
            RAG_WEB_SEARCH_TOTAL.inc()
            docs = self.web_search_tool.invoke({"query": question})

            web_content = "\n".join([d.get("content", "") for d in docs]) if isinstance(docs, list) else str(docs)
            logger.info("Web search returned %d characters", len(web_content))
            web_documents = [Document(page_content=web_content)]

            return {
            "documents": web_documents,
            "question": question,
            "source": "web-search"
        }

    @observe_stage_latency("llm")
    def _call_llm(self, state: GraphState):
        with self.tracer.start_as_current_span("call_llm"):
            question = state["question"]
            prompt = f"Answer the user query concisely:\nQuestion: {question}\nAnswer:"
            response = self.llm.invoke(prompt)
            self._record_llm_call(prompt=prompt, response_text=str(response.content))
            return {"question": question, "generation": response.content, "source": "llm"}

    @observe_stage_latency("retrieval")
    def _retrieve(self, state: GraphState):
        with self.tracer.start_as_current_span("retrieve"):
            question = state["question"]
            if not self.retriever:
                RAG_RETRIEVAL_DOCUMENTS_COUNT.observe(0)
                return {"documents": [], "question": question, "source": "vectorstore"}
            documents = self.retriever.invoke(question)
            RAG_RETRIEVAL_DOCUMENTS_COUNT.observe(len(documents))
            logger.info("Retrieved %d documents from vectorstore for question: %s", len(documents), question)
            return {"documents": documents, "question": question, "source": "vectorstore","retry_count": state.get("retry_count",0)}

    @observe_stage_latency("rerank")
    def _rerank_documents(self, state: GraphState):
        with self.tracer.start_as_current_span("rerank") as span:
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

            span.set_attribute("reranked_docs_count", len(documents))
            span.set_attribute(
                "reranked_docs_content",
                "\n\n".join(
                    f"Document {index}: {document.page_content[:500]}"
                    for index, document in enumerate(documents)
                ),
            )
            span.set_attribute(
                "reranked_docs_metadata",
                "\n\n".join(
                    f"Document {index}: {document.metadata}"
                    for index, document in enumerate(documents)
                ),
            )

            return {
                "documents": documents,
                "question": question,
                "source": "vectorstore",
                "retry_count": state.get("retry_count", 0),
            }

    @observe_stage_latency("grade_documents")
    def _grade_documents(self, state: GraphState):
        with self.tracer.start_as_current_span("grade_documents") as span:
            class GradeDocument(BaseModel):
                document_index: int
                binary_score: Literal["yes", "no"]

            class GradeDocumentsList(BaseModel):
                scores: List[GradeDocument] = Field(..., description="List of binary scores for each document")

            def get_structured_output(prompt_input):
                try:
                    # Create the chain inside the helper to ensure it uses the template
                    chain = grade_prompt | self.llm.with_structured_output(GradeDocumentsList, method="json_mode")
                    return chain.invoke(prompt_input)
                except Exception:
                    # Manual fallback to handle raw JSON lists or markdown
                    raw_res = (grade_prompt | self.llm).invoke(prompt_input)
                    import json
                    content = raw_res.content if hasattr(raw_res, 'content') else str(raw_res)
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0].strip()
                    elif "```" in content:
                        content = content.split("```")[1].split("```")[0].strip()
                    
                    data = json.loads(content)
                    if isinstance(data, list):
                        return GradeDocumentsList(scores=data)
                    return GradeDocumentsList(scores=[data])

            system = self._load_prompt('grade_documents.txt')
            
            grade_prompt = ChatPromptTemplate.from_messages([
                ("system", system),
                ("human", "User question: {question}\n\nDocuments:\n{documents}")
            ])
            
            question = state["question"]
            documents = state.get("documents", [])

            if not documents:
                return {"documents": [], "question": question}

            docs_text = "\n\n".join([f"Document {i}: {d.page_content}" for i, d in enumerate(documents)])
            
            try:
                result = get_structured_output({"question": question, "documents": docs_text})
                scores = result.scores if hasattr(result, 'scores') else []
                
                self._record_llm_call(
                    prompt=f"Question: {question}\nDocuments: {docs_text}", 
                    response_text=str(scores)
                )
                
                filtered_docs = []
                for i, doc in enumerate(documents):
                    score_obj = next(
                        (
                            score
                            for score in scores
                            if (
                                score.document_index == i
                                if isinstance(score, GradeDocument)
                                else score.get("document_index") == i
                            )
                        ),
                        None,
                    )
                    binary_score = ""
                    if score_obj:
                        binary_score = (
                            score_obj.binary_score
                            if isinstance(score_obj, GradeDocument)
                            else score_obj.get("binary_score", "")
                        )
                    if binary_score and binary_score.lower() == "yes":
                        filtered_docs.append(doc)
                
                logger.info("Graded %d documents, %d passed", len(documents), len(filtered_docs))
                RAG_RETRIEVAL_RELEVANCE_SCORE.observe(len(filtered_docs))
                span.set_attribute("grading.documents_count", len(documents))
                span.set_attribute("grading.passed_count", len(filtered_docs))
                span.set_attribute(
                    "grading.document_scores",
                    ", ".join(
                        f"{score.document_index}:{score.binary_score}"
                        if isinstance(score, GradeDocument)
                        else f"{score.get('document_index')}:{score.get('binary_score')}"
                        for score in scores
                    ),
                )
                return {"documents": filtered_docs, "question": question}
            except Exception as e:
                logger.error("Bulk grading failed: %s. Falling back to all documents.", e)
                return {"documents": documents, "question": question}

    def _decide_to_generate(self, state: GraphState):
        retry_count = state.get("retry_count", 0)

        if not state.get("documents"):
            if retry_count >= 1:
                return "no relevant document"

            return "transform"

        return "found relevant document"

    @observe_stage_latency("generate")
    def _generate(self, state: GraphState):
        with self.tracer.start_as_current_span("generate"):
            prompt_text = self._load_prompt('generate_answer.txt')
            prompt = ChatPromptTemplate.from_template(template=prompt_text)
            
            def format_docs(docs):
                return "\n\n".join(doc.page_content for doc in docs)

            rag_chain = prompt | self.llm | StrOutputParser()
            context_str = format_docs(state.get("documents", []))
            formatted_prompt  = prompt_text.format(context=context_str, question=state["question"])
            generation = rag_chain.invoke({"context": context_str, "question": state["question"]})
            self._record_llm_call(prompt=formatted_prompt, response_text=str(generation))
            logger.info("Generated answer with %d characters", len(generation))
            return {"documents": state["documents"], "question": state["question"], "generation": generation, "source": state.get("source", "")}
    
    def _after_generate(self, state: GraphState):
        if FAST_MODE or state.get("source") == "web-search":
            return "final"

        return "grade"

    def _grade_generation_node(self, state: GraphState):
        with self.tracer.start_as_current_span("grade_generation_node"):
            return {
                "question": state["question"],
                "documents": state.get("documents", []),
                "generation": state.get("generation", ""),
                "source": state.get("source", "")
            }
    def _grade_generation_v_documents_and_question(self, state: GraphState):
        with self.tracer.start_as_current_span("grade_generation_v_documents_and_question") as span:
            class GradeHallucinations(BaseModel):
                binary_score: Literal["yes", "no"]

            class GradeAnswer(BaseModel):
                binary_score: Literal["yes", "no"]

            hallucination_grader = (
                ChatPromptTemplate.from_messages([
                    ("system", self._load_prompt('grade_hallucination.txt')),
                    ("human", "Facts: {documents}\nAnswer: {generation}")
                ]) 
                | self.llm.with_structured_output(GradeHallucinations, method="json_mode")
            )

            answer_grader = (
                ChatPromptTemplate.from_messages([
                    ("system", self._load_prompt('grade_answer.txt')),
                    ("human", "Question: {question}\nAnswer: {generation}")
                ]) 
                | self.llm.with_structured_output(GradeAnswer, method="json_mode")
            )

            documents = "\n".join([d.page_content for d in state.get("documents", [])])
            generation = state.get("generation", "")
            question = state["question"]

            with measure_stage_latency("hallucination"):
                h_score = hallucination_grader.invoke({"documents": documents, "generation": generation})
            self._record_llm_call(
                prompt=f"Facts: {documents}\nAnswer: {generation}",
                response_text=str(h_score.binary_score),
            )
            logger.info("Hallucination check: %s", h_score.binary_score)
            span.set_attribute("grading.hallucination", str(h_score.binary_score))
            if h_score.binary_score.lower() == "yes":
                with measure_stage_latency("relevance"):
                    a_score = answer_grader.invoke({"question": question, "generation": generation})
                self._record_llm_call(
                    prompt=f"Question: {question}\nAnswer: {generation}",
                    response_text=str(a_score.binary_score),
                )
                logger.info("Answer relevance check: %s", a_score.binary_score)
                span.set_attribute("grading.relevancy", str(a_score.binary_score))
                if a_score.binary_score.lower() == "yes":
                    return "useful"
                return "not useful"
            return "not supported"

    def _transform_query(self, state: GraphState):
        with self.tracer.start_as_current_span("transform_query") as span:
            system = self._load_prompt('transform_query.txt')
            re_write_prompt = ChatPromptTemplate.from_messages([
                ("system", system),
                ("human", "Question: {question}")
            ])
            question_rewriter = re_write_prompt | self.llm | StrOutputParser()
            prompt_text = re_write_prompt.format(question=state["question"])
            better_question = question_rewriter.invoke({"question": state["question"]})
            self._record_llm_call(prompt=prompt_text, response_text=str(better_question))
            retry_count = state.get("retry_count", 0) + 1

            logger.info("Transformed query attempt %d: %s", retry_count, better_question)
            span.set_attribute("transformed_query", str(better_question))
            span.set_attribute("retry_count", retry_count)
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
        streamed_content = []
        for chunk in self.llm.stream(prompt):
            content = getattr(chunk, "content", "")
            if content:
                chunk_count += 1
                streamed_content.append(content)
                yield content
        if streamed_content:
            self._record_llm_call(prompt=prompt, response_text="".join(streamed_content))
        logger.info("Agent stream finished for session %s with %d chunks", session_id, chunk_count)