from __future__ import annotations

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

TURNS_TOTAL = Counter(
    "voiceforge_turns_total",
    "Total conversation turns processed",
    ["channel", "outcome"],
)
TURN_LATENCY = Histogram(
    "voiceforge_turn_latency_seconds",
    "End-to-end turn latency",
    ["stage"],
    buckets=(0.05, 0.1, 0.25, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0),
)
SESSIONS_TOTAL = Counter(
    "voiceforge_sessions_total",
    "Sessions created",
    ["channel", "status"],
)
HANDOFFS_TOTAL = Counter(
    "voiceforge_handoffs_total",
    "Human handoffs",
    ["reason"],
)


def setup_telemetry(app: object, settings: Settings) -> None:
    if settings.otel_enabled:
        resource = Resource.create(
            {
                "service.name": settings.app_name,
                "service.version": "0.1.0",
                "deployment.environment": settings.app_env,
            }
        )
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]
        logger.info("otel_enabled", endpoint=settings.otel_exporter_otlp_endpoint)
    else:
        logger.info("otel_disabled")


def get_tracer(name: str = "voiceforge"):
    return trace.get_tracer(name)
