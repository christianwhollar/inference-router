import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException
from .auth import configured_keys, identity
from .ledger import BudgetExceeded, Ledger
from .models import Completion, model_config
from .postgres import PostgresLedger
from .observe import instrument
from .service import Router, Unavailable


def create_app(path=None, keys=None, models=None, provider=None):
    @asynccontextmanager
    async def lifespan(app):
        configured_models = model_config() if models is None else models
        if not app.state.keys or not configured_models:
            raise RuntimeError("Configure API_KEYS_JSON and MODELS_CONFIG, or APP_DEMO=1")
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
        }

    return app


app = create_app()
