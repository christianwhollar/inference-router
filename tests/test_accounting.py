import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from router.api import create_app
from router.auth import DEMO_KEYS, Identity
from router.ledger import Ledger, ReservationConflict
from router.models import Completion, Model, TaskScore
from router.service import Router, Provider


def test_exactly_once_settlement_under_concurrent_review(tmp_path):
    ledger = Ledger(tmp_path / "usage.db", 1000)
    reservation = ledger.begin("alpha", "model", 100, "request")
    ledger.uncertain("alpha", reservation, "controlled lost response")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda _: ledger.settle("alpha", reservation, 30, "reviewer", "invoice verified"),
                range(20),
            )
        )
    assert ledger.spent("alpha") == 30
    assert Ledger(tmp_path / "usage.db", 1000).reservations("alpha")[0]["actual"] == 30
    with pytest.raises(ReservationConflict):
        ledger.settle("alpha", reservation, 0, "reviewer", "different usage")
    with pytest.raises(KeyError):
        ledger.settle("beta", reservation, 0, "reviewer", "other tenant")


def test_crashed_active_reservation_requires_timeout_before_manual_settlement(tmp_path):
    ledger = Ledger(tmp_path / "usage.db")
    r = ledger.begin("alpha", "model", 100, "request")
    with pytest.raises(ReservationConflict):
        ledger.settle("alpha", r, 0, "reviewer", "confirmed no billing")
    with ledger.connect() as db:
        db.execute("UPDATE router_reservations SET created_at=? WHERE id=?", (time.time() - 121, r))
    ledger.settle("alpha", r, 0, "reviewer", "confirmed no billing")
    assert ledger.spent("alpha") == 0


class Cancellable(Provider):
    def __init__(self):
        self.started = asyncio.Event()

    async def complete(self, *args):
        self.started.set()
        await asyncio.sleep(10)


async def test_client_cancellation_retains_uncertain_usage(tmp_path):
    provider = Cancellable()
    ledger = Ledger(tmp_path / "usage.db")
    model = Model(name="m", quality=1, input_per_million=1, output_per_million=1)
    router = Router([model], ledger, provider)
    task = asyncio.create_task(
        router.complete(Identity("alpha", "u", "analyst"), Completion(prompt="hello"))
    )
    await provider.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ledger.reservations("alpha")[0]["state"] == "uncertain"
    assert ledger.spent("alpha") > 0
    assert not router.capacity.locked()


def test_task_calibration_overrides_operator_rating_and_privacy(tmp_path):
    profile = TaskScore(accuracy=0.95, samples=20, p50_ms=10, dataset_sha256="a" * 64)
    model = Model(name="small", quality=0.3, task_scores={"extraction": profile})
    router = Router([model], Ledger(tmp_path / "usage.db"))
    assert router.explain(Completion(prompt="extract", task="extraction", minimum_quality=0.9))[0][
        "eligible"
    ]
    assert not router.explain(Completion(prompt="extract", minimum_quality=0.9))[0]["eligible"]
    assert not router.explain(
        Completion(prompt="extract", task="extraction", data_class="confidential")
    )[0]["eligible"]


def test_reconciliation_requires_reviewer_and_is_tenant_scoped(tmp_path):
    model = Model(name="fixture", quality=1, protocol="fixture")
    with TestClient(create_app(tmp_path / "api.db", DEMO_KEYS, [model])) as c:
        ledger = c.app.state.router.ledger
        r = ledger.begin("alpha", "m", 100, "r")
        ledger.uncertain("alpha", r, "timeout")
        body = {"actual_micros": 25, "note": "Verified provider statement"}
        url = "/reservations/" + r + "/reconcile"
        assert (
            c.post(url, json=body, headers={"Authorization": "Bearer demo-analyst"}).status_code
            == 403
        )
        assert (
            c.get("/reservations", headers={"Authorization": "Bearer demo-beta"}).json()["items"]
            == []
        )
        assert (
            c.post(url, json=body, headers={"Authorization": "Bearer demo-reviewer"}).status_code
            == 200
        )
        assert ledger.spent("alpha") == 25


class WrongShape(Provider):
    async def complete(self, model, prompt, output_tokens, response_format="text"):
        return {
            "text": '{"unexpected": true}',
            "input_tokens": 1,
            "output_tokens": 5,
            "fixture": False,
        }


async def test_structured_output_failure_keeps_uncertain_charge(tmp_path):
    from router.service import Unavailable

    ledger = Ledger(tmp_path / "schema.db")
    router = Router(
        [Model(name="schema-model", quality=1, input_per_million=1, output_per_million=1)],
        ledger,
        WrongShape(),
    )
    request = Completion(
        prompt="Give a summary",
        response_format="json",
        output_schema={
            "type": "object",
            "required": ["summary"],
            "properties": {"summary": {"type": "string"}},
        },
    )
    with pytest.raises(Unavailable):
        await router.complete(Identity("alpha", "u", "analyst"), request)
    assert ledger.reservations("alpha")[0]["state"] == "uncertain"


def test_schema_references_are_not_resolved_remotely():
    with pytest.raises(ValueError):
        Completion(
            prompt="x", response_format="json", output_schema={"$ref": "https://example.com/schema"}
        )


async def test_sampler_compatibility_preserves_server_string_bounds(tmp_path, monkeypatch):
    import json
    import httpx
    from router.service import Unavailable

    schema = {
        "type": "object",
        "properties": {"maxLength": {"type": "string", "maxLength": 2}},
        "required": ["maxLength"],
    }
    seen = []

    def respond(request):
        seen.append(json.loads(request.content)["format"])
        return httpx.Response(
            200,
            json={
                "done": True,
                "response": '{"maxLength": "too long"}',
                "prompt_eval_count": 1,
                "eval_count": 10,
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw)
    )
    ledger = Ledger(tmp_path / "grammar.db")
    router = Router([Model(name="m", quality=1)], ledger)
    request = Completion(prompt="Summarize", response_format="json", output_schema=schema)
    with pytest.raises(Unavailable):
        await router.complete(Identity("alpha", "u", "analyst"), request)
    assert seen[0]["properties"]["maxLength"] == {"type": "string"}
    assert request.output_schema["properties"]["maxLength"]["maxLength"] == 2
    assert ledger.reservations("alpha")[0]["state"] == "uncertain"
