import os
import time
import uuid
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from fastapi import Depends, Response
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from .auth import identity


def instrument(app, name):
    registry = CollectorRegistry()
    requests = Counter(
        "http_requests_total", "HTTP requests", ["method", "route", "status"], registry=registry
    )
    duration = Histogram("http_request_seconds", "Request latency", ["route"], registry=registry)
    provider = TracerProvider()
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    app.state.tracing = provider
    tracer = provider.get_tracer(name)

    @app.middleware("http")
    async def telemetry(request, call_next):
        start = time.perf_counter()
        request_id = str(uuid.uuid4())
        with tracer.start_as_current_span("http.request") as span:
            span.set_attribute("request.id", request_id)
            try:
                response = await call_next(request)
            except Exception:
                span.set_attribute("request.failed", True)
                raise
            route = getattr(request.scope.get("route"), "path", "unmatched")
            span.set_attribute("http.route", route)
            span.set_attribute("http.status_code", response.status_code)
            requests.labels(request.method, route, response.status_code).inc()
            duration.labels(route).observe(time.perf_counter() - start)
            response.headers["X-Request-ID"] = request_id
            return response

    @app.get("/metrics", include_in_schema=False)
    def metrics(_=Depends(identity)):
        return Response(generate_latest(registry), media_type="text/plain; version=0.0.4")
