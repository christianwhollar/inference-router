import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from .auth import configured_keys, identity
from .ledger import BudgetExceeded, Ledger, ReservationConflict
from .models import Completion, model_config
from .postgres import PostgresLedger
from .observe import instrument
from .service import Router, Unavailable


class Reconciliation(BaseModel):
    actual_micros: int = Field(ge=0)
    note: str = Field(min_length=10, max_length=500)


def create_app(path=None, keys=None, models=None, provider=None):
    @asynccontextmanager
    async def lifespan(app):
        configured_models = model_config() if models is None else models
        if not app.state.keys or not configured_models:
            raise RuntimeError("Configure API_KEYS_JSON and MODELS_CONFIG, or APP_DEMO=1")
        # A profile belongs to a particular model build, not an arbitrary reused tag.
        import httpx

        for model in configured_models:
            if model.calibration_model_digest:
                response = httpx.get(model.endpoint.rstrip("/") + "/api/tags", timeout=10)
                response.raise_for_status()
                installed = {m["name"]: m["digest"] for m in response.json()["models"]}
                if installed.get(model.model) != model.calibration_model_digest:
                    raise RuntimeError(
                        f"Calibration model digest mismatch for {model.name}; recalibrate before routing"
                    )
        limit = int(float(os.getenv("DAILY_BUDGET_USD", "1")) * 1_000_000)
        if os.getenv("ROUTER_DATABASE_URL"):
            ledger = PostgresLedger(os.environ["ROUTER_DATABASE_URL"], limit)
        else:
            ledger = Ledger(
                path or Path(os.getenv("DATA_DIR", "runtime")) / "router.db",
                daily_limit_micros=limit,
            )
        app.state.router = Router(configured_models, ledger, provider)
        yield
        app.state.tracing.shutdown()

    app = FastAPI(title="Inference router", lifespan=lifespan)
    app.state.keys = configured_keys() if keys is None else keys
    instrument(app, "inference-router")

    @app.get("/health")
    def health():
        return {"status": "ok", "models": len(app.state.router.models)}

    @app.get("/ready")
    def ready():
        try:
            app.state.router.ledger.spent("_readiness")
        except Exception:
            raise HTTPException(503, "Quota store unavailable") from None
        return {"status": "ready"}

    @app.post("/v1/complete")
    async def complete(request: Completion, actor=Depends(identity)):
        try:
            return await app.state.router.complete(actor, request)
        except BudgetExceeded as exc:
            raise HTTPException(429, str(exc)) from None
        except Unavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @app.get("/usage")
    def usage(actor=Depends(identity)):
        return {
            "tenant": actor.tenant,
            "reserved_or_spent_usd": app.state.router.ledger.spent(actor.tenant) / 1_000_000,
            "daily_limit_usd": app.state.router.ledger.limit / 1_000_000,
        }

    @app.post("/v1/route")
    def explain(request: Completion, actor=Depends(identity)):
        return {"routes": app.state.router.explain(request), "task": request.task}

    @app.get("/models")
    def models_view(actor=Depends(identity)):
        return {
            "items": [
                {
                    "name": m.name,
                    "model": m.model,
                    "protocol": m.protocol,
                    "quality": m.quality,
                    "task_scores": {k: v.model_dump() for k, v in m.task_scores.items()},
                    "approved_for_confidential": m.approved_for_confidential,
                    "context_tokens": m.context_tokens,
                    "input_per_million": m.input_per_million,
                    "output_per_million": m.output_per_million,
                    "circuit": {
                        "failures": app.state.router.circuits[m.name].failures,
                        "probing": app.state.router.circuits[m.name].probing,
                    },
                }
                for m in app.state.router.models
            ]
        }

    @app.get("/reservations")
    def reservations(actor=Depends(identity)):
        return {"items": app.state.router.ledger.reservations(actor.tenant)}

    @app.post("/reservations/{reservation_id}/reconcile")
    def reconcile(reservation_id: str, value: Reconciliation, actor=Depends(identity)):
        if actor.role != "reviewer":
            raise HTTPException(403, "Reviewer required for usage reconciliation")
        try:
            return app.state.router.ledger.settle(
                actor.tenant, reservation_id, value.actual_micros, actor.user, value.note
            )
        except KeyError:
            raise HTTPException(404, "Reservation not found") from None
        except ReservationConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    from .webapp import mount

    mount(app)

    return app


app = create_app()
