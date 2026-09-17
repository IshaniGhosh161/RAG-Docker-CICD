import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps

from fastapi import FastAPI
from pymongo import MongoClient
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExportResult,
    SpanExporter,
)
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

logger = logging.getLogger(__name__)


def _serialize_value(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    return str(value)


class MongoDBSpanExporter(SpanExporter):
    def __init__(self, mongo_uri: str, database_name: str = "chatbot"):
        self.client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        self.collection = self.client[database_name]["telemetry_spans"]
        self.collection.create_index([("trace_id", 1), ("start_time", 1)])
        self.collection.create_index("start_time")

    def export(self, spans) -> SpanExportResult:
        documents = []
        for span in spans:
            context = span.get_span_context()
            documents.append(
                {
                    "trace_id": format(context.trace_id, "032x"),
                    "span_id": format(context.span_id, "016x"),
                    "parent_span_id": (
                        format(span.parent.span_id, "016x")
                        if span.parent
                        else None
                    ),
                    "name": span.name,
                    "kind": span.kind.name,
                    "start_time": datetime.fromtimestamp(
                        span.start_time / 1_000_000_000, tz=timezone.utc
                    ),
                    "end_time": datetime.fromtimestamp(
                        span.end_time / 1_000_000_000, tz=timezone.utc
                    ) if span.end_time else None,
                    "duration_ms": (
                        (span.end_time - span.start_time) / 1_000_000
                        if span.end_time
                        else None
                    ),
                    "status": span.status.status_code.name,
                    "status_description": span.status.description,
                    "attributes": _serialize_value(dict(span.attributes)),
                    "events": [
                        {
                            "name": event.name,
                            "timestamp": datetime.fromtimestamp(
                                event.timestamp / 1_000_000_000,
                                tz=timezone.utc,
                            ),
                            "attributes": _serialize_value(dict(event.attributes)),
                        }
                        for event in span.events
                    ],
                    "resource": _serialize_value(dict(span.resource.attributes)),
                    "instrumentation_scope": (
                        span.instrumentation_scope.name
                        if span.instrumentation_scope
                        else None
                    ),
                    "recorded_at": datetime.now(timezone.utc),
                }
            )

        if documents:
            try:
                self.collection.insert_many(documents, ordered=False)
            except Exception:
                logger.exception("Failed to persist OpenTelemetry spans in MongoDB")
                return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self):
        self.client.close()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


_telemetry_event_store = None


def record_telemetry_event(event_type: str, payload: dict) -> None:
    if _telemetry_event_store is None:
        return
    _, collection = _telemetry_event_store
    try:
        collection.insert_one(
            {
                "event_type": event_type,
                "payload": _serialize_value(payload),
                "recorded_at": datetime.now(timezone.utc),
            }
        )
    except Exception:
        logger.exception("Failed to persist telemetry event in MongoDB")

RAG_QUESTIONS_TOTAL = Counter(
    "rag_questions_total",
    "Total number of questions processed by the RAG API",
)
RAG_RESPONSE_LATENCY_SECONDS = Histogram(
    "rag_response_latency_seconds",
    "Time taken to answer a question in the RAG API",
    buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 30, 60, 120),
)
RAG_TOKENS_PER_QUESTION = Histogram(
    "rag_tokens_per_question",
    "Estimated tokens used per RAG question",
    buckets=(50, 100, 250, 500, 750, 1000, 1500, 2500, 5000, 10000),
)
RAG_TOKEN_COST_PER_QUESTION = Histogram(
    "rag_token_cost_per_question",
    "Estimated token cost per RAG question in USD",
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2),
)
RAG_TOKENS_TOTAL = Counter(
    "rag_tokens_total",
    "Estimated total tokens processed by the RAG API",
)
RAG_TOKEN_COST_TOTAL = Counter(
    "rag_token_cost_total",
    "Estimated cumulative token cost in USD for the RAG API",
)
RAG_WEB_SEARCH_TOTAL = Counter(
    "rag_web_search_total",
    "Number of web-search requests triggered by the RAG API",
)
RAG_LLM_CALLS_TOTAL = Counter(
    "rag_llm_calls_total",
    "Number of times the LLM was invoked by the RAG API",
)
RAG_ROUTE_DECISIONS_TOTAL = Counter(
    "rag_route_decisions_total",
    "Number of times the RAG router chose a datasource",
    labelnames=("route",),
)
RAG_RETRIEVAL_DOCUMENTS_COUNT = Histogram(
    "rag_retrieval_documents_count",
    "Number of documents returned by FAISS retrieval for a query",
    buckets=(0, 1, 2, 3, 5, 10, 20, 50),
)
RAG_RERANKED_DOCUMENTS_COUNT = Histogram(
    "rag_reranked_documents_count",
    "Number of documents remaining after reranking for a query",
    buckets=(0, 1, 2, 3, 5, 10, 20, 50),
)
RAG_RETRIEVAL_RELEVANCE_SCORE = Histogram(
    "rag_retrieval_relevance_score",
    "Number of relevant documents kept after grading for a query",
    buckets=(0, 1, 2, 3, 5, 10, 20, 50),
)
RAG_STAGE_LATENCY_SECONDS = Histogram(
    "rag_stage_latency_seconds",
    "Time spent in each RAG workflow stage",
    labelnames=("stage",),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)
RAG_RERANKED_DOCUMENTS_COUNT.observe(0)
for stage_name in (
    "build_query",
    "route",
    "web_search",
    "llm",
    "retrieval",
    "rerank",
    "grade_documents",
    "generate",
    "hallucination",
    "relevance",
):
    RAG_STAGE_LATENCY_SECONDS.labels(stage=stage_name)
TOKEN_COST_PER_1K_TOKENS = 0.005


def observe_stage_latency(stage: str):
    def decorator(function):
        @wraps(function)
        def timed_function(*args, **kwargs):
            started_at = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                RAG_STAGE_LATENCY_SECONDS.labels(stage=stage).observe(
                    time.perf_counter() - started_at
                )

        return timed_function

    return decorator


@contextmanager
def measure_stage_latency(stage: str):
    started_at = time.perf_counter()
    try:
        yield
    finally:
        RAG_STAGE_LATENCY_SECONDS.labels(stage=stage).observe(
            time.perf_counter() - started_at
        )


def estimate_tokens(text: str) -> int:
    cleaned = (text or "").strip()
    if not cleaned:
        return 0
    return max(1, len(cleaned.split()))


def record_llm_usage(prompt: str | None, response_text: str | None = None) -> None:
    prompt_tokens = estimate_tokens(prompt or "")
    response_tokens = estimate_tokens(response_text or "")
    total_tokens = prompt_tokens + response_tokens
    if not total_tokens:
        return

    total_cost = (total_tokens / 1000) * TOKEN_COST_PER_1K_TOKENS
    RAG_TOKENS_TOTAL.inc(total_tokens)
    RAG_TOKEN_COST_TOTAL.inc(total_cost)
    RAG_LLM_CALLS_TOTAL.inc()
    record_telemetry_event(
        "llm_usage",
        {
            "prompt_tokens": prompt_tokens,
            "response_tokens": response_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": total_cost,
        },
    )


def record_rag_metrics(question: str, response: str, latency_seconds: float) -> None:
    if not question:
        return

    total_tokens = estimate_tokens(question) + estimate_tokens(response)
    per_question_cost = (total_tokens / 1000) * TOKEN_COST_PER_1K_TOKENS
    RAG_QUESTIONS_TOTAL.inc()
    RAG_RESPONSE_LATENCY_SECONDS.observe(latency_seconds)
    RAG_TOKENS_PER_QUESTION.observe(total_tokens)
    RAG_TOKEN_COST_PER_QUESTION.observe(per_question_cost)
    record_telemetry_event(
        "rag_request",
        {
            "question": question,
            "response": response,
            "latency_seconds": latency_seconds,
            "total_tokens": total_tokens,
            "estimated_cost_usd": per_question_cost,
        },
    )


def configure_observability(app: FastAPI) -> None:
    global _telemetry_event_store

    service_name = os.getenv("OTEL_SERVICE_NAME", "rag-chat-api")
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

    if otlp_endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
        )
        logger.info("OpenTelemetry traces configured for %s", otlp_endpoint)
    else:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        logger.info("OpenTelemetry traces configured for console output")

    mongo_uri = os.getenv("MONGO_URI")
    if mongo_uri:
        try:
            telemetry_database = os.getenv("MONGO_TELEMETRY_DATABASE", "chatbot")
            mongo_exporter = MongoDBSpanExporter(mongo_uri, telemetry_database)
            provider.add_span_processor(BatchSpanProcessor(mongo_exporter))

            event_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
            event_collection = event_client[telemetry_database]["telemetry_events"]
            event_collection.create_index("recorded_at")
            event_collection.create_index("event_type")
            _telemetry_event_store = (event_client, event_collection)
            logger.info(
                "OpenTelemetry spans and events configured for MongoDB database '%s'",
                telemetry_database,
            )
        except Exception:
            logger.exception("MongoDB telemetry persistence could not be configured")

    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)