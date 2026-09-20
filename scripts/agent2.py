from __future__ import annotations

import logging
import os
import sys
from abc import ABC, abstractmethod
from typing import Any, Dict, List

from dotenv import load_dotenv
from typing_extensions import TypedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import FAST_MODE
from backend.database import DatabaseManager
from langchain_core.documents import Document
from langgraph.graph import END, START, StateGraph

from scripts.agent import Agent

logger = logging.getLogger(__name__)
load_dotenv()


class GraphState(TypedDict):
    question: str
    original_question: str
    generation: str
    documents: List[Document]
    history: List[dict]
    session_id: str
    retry_count: int
    source: str


class BaseAgent(ABC):
    """
    Abstract Base Class for specialized agents. 
    Each agent delegates execution to the core runtime to preserve OpenTelemetry spans 
    and Prometheus metrics exactly as Grafana expects.
    """
    def __init__(self, runtime: Agent, name: str):
        self.runtime = runtime
        self.name = name

    @abstractmethod
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the agent's primary capability."""
        raise NotImplementedError


class QueryTransformationAgent(BaseAgent):
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"[{self.name}] Analyzing and rebuilding query...")
        updated = self.runtime._build_query(state)
        state.update(updated)
        return state

    def transform_for_retry(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"[{self.name}] Transforming query for fallback retry...")
        updated = self.runtime._transform_query(state)
        state.update(updated)
        return state


class RoutingAgent(BaseAgent):
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        # The router agent sets the source but does not mutate the core state payload
        existing_route = state.get("source")
        if not existing_route:
            logger.info(f"[{self.name}] Determining optimal data source...")
            route = self.runtime._route_question(state)
            state["source"] = route
        return state

    def resolve_route(self, state: Dict[str, Any]) -> str:
        return state.get("source") or self.runtime._route_question(state)


class ResearchAgent(BaseAgent):
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Default fallback to satisfy BaseAgent ABC."""
        return state
    
    def retrieve_vector(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"[{self.name}] Searching internal knowledge base...")
        updated = self.runtime._retrieve(state)
        state.update(updated)
        return state

    def retrieve_web(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"[{self.name}] Searching the web for real-time data...")
        updated = self.runtime._web_search(state)
        state.update(updated)
        return state


class EvaluationAgent(BaseAgent):
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return state
    
    def rank_documents(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"[{self.name}] Reranking retrieved documents...")
        updated = self.runtime._rerank_documents(state)
        state.update(updated)
        return state

    def grade_documents(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"[{self.name}] Grading document relevance...")
        updated = self.runtime._grade_documents(state)
        state.update(updated)
        return state

    def check_hallucinations(self, state: Dict[str, Any]) -> str:
        logger.info(f"[{self.name}] Verifying generation against source facts...")
        return self.runtime._grade_generation_v_documents_and_question(state)


class GenerationAgent(BaseAgent):
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return state
    
    def generate_rag(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"[{self.name}] Synthesizing final RAG response...")
        updated = self.runtime._generate(state)
        state.update(updated)
        return state

    def generate_direct(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"[{self.name}] Generating direct LLM response...")
        updated = self.runtime._call_llm(state)
        state.update(updated)
        return state


class MultiAgentSystem:
    """
    Choreographed multi-agent system. 
    Agents act as distinct functional blocks, preserving the state machine 
    and strict telemetry required by the observability layer.
    """
    def __init__(self, username: str):
        self.db = DatabaseManager()
        self.username = username
        self.runtime = Agent(username)

        # Initialize distinct agents
        self.query_agent = QueryTransformationAgent(self.runtime, "Query_Agent")
        self.router_agent = RoutingAgent(self.runtime, "Router_Agent")
        self.research_agent = ResearchAgent(self.runtime, "Research_Agent")
        self.eval_agent = EvaluationAgent(self.runtime, "Evaluation_Agent")
        self.gen_agent = GenerationAgent(self.runtime, "Generation_Agent")

        self.app = self._build_workflow()

    def _build_workflow(self):
        workflow = StateGraph(GraphState)

        # Define Nodes mapping to Agent capabilities
        workflow.add_node("build_query", self.query_agent.process)
        workflow.add_node("route", self.router_agent.process)
        workflow.add_node("retrieve", self.research_agent.retrieve_vector)
        workflow.add_node("web_search", self.research_agent.retrieve_web)
        workflow.add_node("call_llm", self.gen_agent.generate_direct)
        workflow.add_node("rerank", self.eval_agent.rank_documents)
        workflow.add_node("grade_documents", self.eval_agent.grade_documents)
        workflow.add_node("transform_query", self.query_agent.transform_for_retry)
        workflow.add_node("generate", self.gen_agent.generate_rag)
        workflow.add_node("grade_generation", self.runtime._grade_generation_node)

        # Graph Construction
        workflow.add_edge(START, "build_query")
        workflow.add_edge("build_query", "route")

        workflow.add_conditional_edges(
            "route",
            self.router_agent.resolve_route,
            {
                "web-search": "web_search",
                "vectorstore": "retrieve",
                "llm": "call_llm",
            },
        )

        workflow.add_edge("web_search", "generate")
        workflow.add_edge("retrieve", "rerank")
        workflow.add_edge("rerank", "grade_documents")

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
            self.router_agent.resolve_route,
            {
                "web-search": "web_search",
                "vectorstore": "retrieve",
                "llm": "call_llm",
            },
        )

        workflow.add_conditional_edges(
            "generate",
            self._after_generate,
            {
                "final": END,
                "grade": "grade_generation",
            },
        )

        workflow.add_conditional_edges(
            "grade_generation",
            self.eval_agent.check_hallucinations,
            {
                "not supported": "call_llm",
                "useful": END,
                "not useful": "call_llm",
            },
        )

        workflow.add_edge("call_llm", END)
        return workflow.compile()

    @staticmethod
    def _decide_to_generate(state: Dict[str, Any]) -> str:
        retry_count = state.get("retry_count", 0)
        if not state.get("documents"):
            if retry_count >= 1:
                return "no relevant document"
            return "transform"
        return "found relevant document"

    @staticmethod
    def _after_generate(state: Dict[str, Any]) -> str:
        # Re-introduced FAST_MODE logic missing from previous agent2.py
        if FAST_MODE or state.get("source") == "web-search":
            return "final"
        return "grade"

    def generate_response(self, session_id: str, user_message: str) -> str:
        state = {
            "question": user_message,
            "original_question": user_message,
            "session_id": session_id,
            "retry_count": 0,
        }

        final_generation = None
        for output in self.app.stream(state):
            for node_state in output.values():
                if isinstance(node_state, dict) and "generation" in node_state:
                    final_generation = node_state["generation"]

        if final_generation:
            return final_generation
        return "I was unable to find an appropriate response."

    def generate_bot_response(self, session_id: str, user_message: str) -> str:
        return self.generate_response(session_id, user_message)

    def stream_response(self, session_id: str, user_message: str):
        # Defers to agent.py's stream_bot_response to ensure exact telemetry propagation during stream
        yield from self.runtime.stream_bot_response(session_id, user_message)

    def stream_bot_response(self, session_id: str, user_message: str):
        yield from self.stream_response(session_id, user_message)