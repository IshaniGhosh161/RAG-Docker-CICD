import logging
import os

from fastapi import FastAPI
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

logger = logging.getLogger(__name__)

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
TOKEN_COST_PER_1K_TOKENS = 0.005


def estimate_tokens(text: str) -> int:
    cleaned = (text or "").strip()
    if not cleaned:
        return 0
    return max(1, len(cleaned.split()))


def record_rag_metrics(question: str, response: str, latency_seconds: float) -> None:
    if not question:
        return

    total_tokens = estimate_tokens(question) + estimate_tokens(response)
    per_question_cost = (total_tokens / 1000) * TOKEN_COST_PER_1K_TOKENS
    RAG_QUESTIONS_TOTAL.inc()
    RAG_RESPONSE_LATENCY_SECONDS.observe(latency_seconds)
    RAG_TOKENS_PER_QUESTION.observe(total_tokens)
    RAG_TOKEN_COST_PER_QUESTION.observe(per_question_cost)

    if total_tokens:
        RAG_TOKENS_TOTAL.inc(total_tokens)
        RAG_TOKEN_COST_TOTAL.inc(per_question_cost)


def configure_observability(app: FastAPI) -> None:
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

    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)