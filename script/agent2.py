from dotenv import load_dotenv
import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from langchain_community.vectorstores import FAISS
# from langchain_groq import ChatGroq
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain_community.tools.tavily_search import TavilySearchResults
from typing import Literal, List, Dict, Any
from typing_extensions import TypedDict
from database import DatabaseManager
import logging_config

logger = logging.getLogger(__name__)
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from pydantic import BaseModel, Field
from langgraph.graph import END, StateGraph, START
from langchain_core.output_parsers import StrOutputParser
from abc import ABC, abstractmethod
from ratelimit import limits, sleep_and_retry
from tenacity import retry, wait_exponential, stop_after_attempt
# from IPython.display import Image, display

# Load environment variables
load_dotenv()
os.environ['GOOGLE_API_KEY'] = os.getenv('GOOGLE_API_KEY')
os.environ['TAVILY_API_KEY'] = os.getenv("TAVILY_API_KEY")
# os.environ['GROQ_API_KEY'] = os.getenv("GROQ_API_KEY")

# Rate limiting constants
ONE_MINUTE = 60
MAX_CALLS_PER_MINUTE = 15

class BaseAgent(ABC):
    """Abstract base class for all agents"""
    def __init__(self):
        self.llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash", 
                                          temperature=0,
                                          retry_on_failure=True,
                                          retry_on_quota=True,
                                          max_retries=3,
                                          initial_delay=2,  # Start with 2 second delay
                                          exponential_base=2,  # Double the delay with each retry
                                          max_delay=10  # Maximum delay between retries
                                          )
        # # Configure model kwargs properly to avoid warnings
        # model_kwargs = {
        #     "temperature": 0,
        #     "retry_on_failure": True,
        #     "retry_on_quota": True,
        #     "initial_delay": 2,
        #     "exponential_base": 2,
        #     "max_delay": 10
        # }
        
        # self.llm = ChatGroq(
        #     model="llama-3.3-70b-versatile",
        #     model_kwargs=model_kwargs,
        #     max_retries=3  # This is a valid top-level parameter
        # )
        
    @abstractmethod
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        pass

class QueryAgent(BaseAgent):
    """Agent responsible for understanding and transforming queries"""
    def __init__(self, db_manager: DatabaseManager):
        super().__init__()
        self.db = db_manager

    def _load_chat_history(self, session_id, max_messages=5):
        """Load chat history from database"""
        messages = self.db.get_chat_messages(session_id)
        if not messages:
            return []
        context = messages[-max_messages:-1]
        return [{"user": msg["username"], "message": msg["message"]} 
                for msg in context]

    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=20),
        stop=stop_after_attempt(5)
    )
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        prompt = """Task:
        Rewrite the given query to make it clear and precise. Use the conversation history if the query depends on prior context.

        Instructions:
        Analyze the query to determine if it refers to previous context.
        If context is required:
        Refer to the conversation history to resolve ambiguities and rewrite the query.
        If the query is standalone and no need for improvement, return the query as-is.
        
        Inputs:
        Original Query: {question}
        Conversation History: {history} (formatted as username:message)
        """
        query_build_prompt = ChatPromptTemplate.from_template(prompt)
        question_build = query_build_prompt | self.llm | StrOutputParser()
        logger.info("---BUILD QUERY---")
        question = state["question"]
        session_id = state.get("session_id")
        history = self._load_chat_history(session_id) if session_id else []
        better_question = question_build.invoke({"question": question, "history": history})
        
        return {"history": history, "question": better_question, "session_id": session_id}

class RouterAgent(BaseAgent):
    """Agent responsible for routing queries to appropriate data sources"""
    
    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=20),
        stop=stop_after_attempt(5)
    )
    def process(self, state: Dict[str, Any]) -> str:
        class RouteQuery(BaseModel):
            datasource: Literal["vectorstore", "web-search", "llm"] = Field(
                description="Route queries to vectorstore, web search, or llm"
            )

        structured_llm_router = self.llm.with_structured_output(RouteQuery)
        system = """You are an expert at routing user questions.
        The vectorstore contains documents about agents, prompt engineering, and adversarial attacks.
        Use vectorstore for these topics. Otherwise, use web-search.
        For generic conversation choose llm"""
        
        route_prompt = ChatPromptTemplate.from_messages([
            ("system", system),
            ("human", "{question}"),
        ])

        question_router = route_prompt | structured_llm_router
        logger.info("---ROUTE QUERY---")
        source = question_router.invoke({"question": state["question"]})
        if source.datasource == "web-search":
            logger.info("---ROUTE QUESTION TO WEB SEARCH---")
        elif source.datasource == "vectorstore":
            logger.info("---ROUTE QUESTION TO RAG---")
        else:
            logger.info("---ROUTE QUESTION TO LLM---")
        return source.datasource

class RetrievalAgent(BaseAgent):
    """Agent responsible for retrieving information from vector store"""
    def __init__(self):
        super().__init__()
        self.embedding = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
        self.vector_store = FAISS.load_local('faiss_index', self.embedding, allow_dangerous_deserialization=True,normalize_L2=True)
        self.retriever = self.vector_store.as_retriever()

    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("---RETRIEVAL---")
        documents = self.retriever.invoke(state["question"])
        return {"documents": documents, "question": state["question"]}

class WebSearchAgent(BaseAgent):
    """Agent responsible for web search operations"""
    def __init__(self):
        super().__init__()
        self.web_search_tool = TavilySearchResults(k=3)

    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("---WEB SEARCH---")
        docs = self.web_search_tool.invoke({"query": state["question"]})
        web_results = "\n".join([d["content"] for d in docs])
        web_results = Document(page_content=web_results)
        return {"documents": web_results, "question": state["question"]}
    
class DirectLLMAgent(BaseAgent):
    """Agent responsible for direct LLM interactions without retrieval"""
    
    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=20),
        stop=stop_after_attempt(5)
    )
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        prompt_template = """
        Answer the question as detailed as possible based on your knowledge, make sure to provide all the details,
        if you don't know the answer just say, 'answer is not available in my knowledge base', don't provide
        the wrong answer. If it is generic text, answer accordingly.
        
        Question:\n{question}\n
        Answer:
        """
        logger.info("---LLM---")
        response = self.llm.invoke(prompt_template.format(question=state["question"]))
        return {
            "documents": [],
            "question": state["question"],
            "generation": response.content
        }

class DocumentGradingAgent(BaseAgent):
    """Agent responsible for grading document relevance"""
    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=20),
        stop=stop_after_attempt(5)
    )
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        class GradeDocuments(BaseModel):
            binary_score: str = Field(
                description="Documents are relevant to the question, 'yes' or 'no'"
            )

        structured_llm_grader = self.llm.with_structured_output(GradeDocuments)
        system = """You are a grader assessing relevance of a retrieved document to a user question. \n 
            If the document contains keyword(s) or semantic meaning related to the user question, grade it as relevant. \n
            It does not need to be a stringent test. The goal is to filter out erroneous retrievals. \n
            Give a binary score 'yes' or 'no' score to indicate whether the document is relevant to the question."""
        
        grade_prompt = ChatPromptTemplate.from_messages([
            ("system", system),
            ("human", "Retrieved document: \n\n {document} \n\n User question: {question}"),
        ])

        retrieval_grader = grade_prompt | structured_llm_grader
        logger.info("---CHECK DOCUMENT RELEVANCE TO QUESTION---")
        filtered_docs = []
        for doc in state["documents"]:
            score = retrieval_grader.invoke({
                "question": state["question"], 
                "document": doc.page_content
            })
            if score.binary_score == "yes":
                logger.info("---GRADE: DOCUMENT RELEVANT---")
                filtered_docs.append(doc)
            else:
                logger.info("---GRADE: DOCUMENT NOT RELEVANT---")
                continue
                
        return {"documents": filtered_docs, "question": state["question"]}

class GenerationAgent(BaseAgent):
    """Agent responsible for generating answers"""
    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=20),
        stop=stop_after_attempt(5)
    )
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        prompt = """
        You are an assistant in question answering task. Provide detailed answer to the question based on the provided context. 
        If the answer is not in the provided context, respond with 'answer is not available in the context', 
        don't provide the wrong answer.
        Context:\n{context}\n
        Question:\n{question}\n

        Answer:
        """
        prompt_template = ChatPromptTemplate.from_template(prompt)
        rag_chain = prompt_template | self.llm | StrOutputParser()
        
        logger.info("---GENERATE---")
        generation = rag_chain.invoke({
            "context": state["documents"], 
            "question": state["question"]
        })
        return {
            "documents": state["documents"],
            "question": state["question"],
            "generation": generation
        }

class QualityCheckAgent(BaseAgent):
    """Agent responsible for checking generation quality"""
    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=20),
        stop=stop_after_attempt(5)
    )
    def process(self, state: Dict[str, Any]) -> str:
        # Check for hallucinations
        class GradeHallucinations(BaseModel):
            binary_score: str = Field(
                description="Answer is grounded in facts, 'yes' or 'no'"
            )

        structured_hallucination_grader = self.llm.with_structured_output(GradeHallucinations)
        # Prompt
        system = """You are a grader assessing whether an LLM generation is grounded in / supported by a set of retrieved facts. \n 
            Give a binary score 'yes' or 'no'. 'Yes' means that the answer is grounded in / supported by the set of facts."""
        hallucination_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system),
                ("human", "Set of facts: \n\n {documents} \n\n LLM generation: {generation}"),
            ]
        )
        
        hallucination_grader = hallucination_prompt | structured_hallucination_grader
        
        # Check if answer addresses question
        class GradeAnswer(BaseModel):
            binary_score: str = Field(
                description="Answer addresses question, 'yes' or 'no'"
            )

        structured_answer_grader = self.llm.with_structured_output(GradeAnswer)
        # Prompt
        system = """You are a grader assessing whether an answer addresses / resolves a question \n 
            Give a binary score 'yes' or 'no'. Yes' means that the answer resolves the question."""
        answer_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system),
                ("human", "User question: \n\n {question} \n\n LLM generation: {generation}"),
            ]
        )
        
        answer_grader = answer_prompt | structured_answer_grader
        
        logger.info("---CHECK HALLUCINATIONS---")
        # Process checks
        hallucination_score = hallucination_grader.invoke({
            "documents": state["documents"],
            "generation": state["generation"]
        })
        
        if hallucination_score.binary_score == "yes":
            logger.info("---DECISION: GENERATION IS GROUNDED IN DOCUMENTS---")
            logger.info("---GRADE GENERATION vs QUESTION---")
            answer_score = answer_grader.invoke({
                "question": state["question"],
                "generation": state["generation"]
            })
            if answer_score.binary_score == "yes":
                logger.info("---DECISION: GENERATION ADDRESSES QUESTION---")
                return "useful" 
            else:
                logger.info("---DECISION: GENERATION DOES NOT ADDRESS QUESTION---")
                return "not useful"
        else:
            logger.info("---DECISION: GENERATION IS NOT GROUNDED IN DOCUMENTS, RE-TRY---")
            return "not supported"

class QueryTransformationAgent(BaseAgent):
    """Agent responsible for transforming queries when initial search fails"""
    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=20),
        stop=stop_after_attempt(5)
    )
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        system = """You are a question re-writer that converts an input question to a better version optimized \n 
            for vectorstore retrieval. Look at the input and try to reason about the underlying semantic intent / meaning."""
        re_write_prompt = ChatPromptTemplate.from_messages([
            ("system", system),
            ("human", "Here is the initial question: \n\n {question} \n Formulate an improved question."),
        ])

        question_rewriter = re_write_prompt | self.llm | StrOutputParser()
        logger.info("---TRANSFORM QUERY---")
        better_question = question_rewriter.invoke({"question": state["question"]})
        
        return {
            "documents": state["documents"], 
            "question": better_question
        }

class MultiAgentSystem:
    """Orchestrates multiple agents in a workflow"""
    def __init__(self, username: str):
        self.db = DatabaseManager()
        self.username = username
        
        # Initialize agents
        self.query_agent = QueryAgent(self.db)
        self.router_agent = RouterAgent()
        self.retrieval_agent = RetrievalAgent()
        self.web_search_agent = WebSearchAgent()
        self.direct_llm_agent = DirectLLMAgent()
        self.document_grading_agent = DocumentGradingAgent()
        self.generation_agent = GenerationAgent()
        self.quality_check_agent = QualityCheckAgent()
        self.query_transformation_agent = QueryTransformationAgent()
        
        # Build workflow
        self.app = self._build_workflow()

    def _build_workflow(self) -> StateGraph:
        """Build and return the workflow graph"""
        class GraphState(TypedDict):
            question: str
            generation: str
            documents: List[str]
            history: List[dict]
            session_id: str
            
        workflow = StateGraph(GraphState)

        # Add nodes for each agent
        workflow.add_node("build_query", self.query_agent.process)
        workflow.add_node("web_search", self.web_search_agent.process)
        workflow.add_node("call_llm", self.direct_llm_agent.process)
        workflow.add_node("retrieve", self.retrieval_agent.process)
        workflow.add_node("grade_documents", self.document_grading_agent.process)
        workflow.add_node("generate", self.generation_agent.process)
        workflow.add_node("transform_query", self.query_transformation_agent.process)

        # Helper function to determine next step after document grading
        def decide_to_generate(state):
            if not state["documents"]:
                logger.info("---DECISION: ALL DOCUMENTS ARE NOT RELEVANT TO QUESTION, TRANSFORM QUERY---")
                return "no relevant document"
            else:
                logger.info("---DECISION: FOUND DOCUMENT THAT IS RELEVANT TO QUESTION, GENERATE ANSWER---")
                return "found relevant document"

        # Define workflow edges
        workflow.add_edge(START, "build_query")
        workflow.add_conditional_edges(
            "build_query",
            self.router_agent.process,
            {
                "web-search": "web_search",
                "vectorstore": "retrieve",
                "llm": "call_llm"
            }
        )
        workflow.add_edge("web_search", "generate")
        workflow.add_edge("retrieve", "grade_documents")
        
        # Add conditional edges for document grading outcomes
        workflow.add_conditional_edges(
            "grade_documents",
            decide_to_generate,
            {
                "no relevant document": "transform_query",
                "found relevant document": "generate",
            }
        )
        
        # Add edges for query transformation
        workflow.add_conditional_edges(
            "transform_query",
            self.router_agent.process,
            {
                "web-search": "web_search",
                "vectorstore": "retrieve",
                "llm": "call_llm"
            }
        )
        
        # Add conditional edges for generation quality check
        workflow.add_conditional_edges(
            "generate",
            self.quality_check_agent.process,
            {
                "useful": END,
                "not useful": "transform_query",
                "not supported": "generate"
            }
        )
        workflow.add_edge("call_llm",END)
        # app = workflow.compile()
        # try:
        #     # Generate the image
        #     img = Image(app.get_graph().draw_mermaid_png())
            
        #     # Display the image
        #     display(img)
            
        #     # Save the image to a file
        #     with open("graph_image.png", "wb") as file:
        #         file.write(img.data)
        # except Exception as e:
        #     # Handle exceptions if any
        #     print(f"An error occurred: {e}")
        return workflow.compile()

    @sleep_and_retry
    @limits(calls=MAX_CALLS_PER_MINUTE, period=ONE_MINUTE)
    @retry(
        wait=wait_exponential(multiplier=1, min=4, max=20),
        stop=stop_after_attempt(5)
    )
    def generate_bot_response(self, session_id: str, user_message: str) -> str:
        """Generate response using the multi-agent system"""
        try:
            inputs = {
                "question": user_message,
                "session_id": session_id
            }
            
            final_output = None
            for output in self.app.stream(inputs):
                final_output = output
                
            if final_output and 'generation' in final_output.get(list(final_output.keys())[-1], {}):
                return final_output[list(final_output.keys())[-1]]['generation']
            return "I apologize, but I wasn't able to generate a response."
                
        except Exception as e:
            return f"Error generating response: {str(e)}"