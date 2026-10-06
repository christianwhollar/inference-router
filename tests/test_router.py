import asyncio
from concurrent.futures import ThreadPoolExecutor
import pytest
import httpx
from router.auth import Identity
from router.ledger import BudgetExceeded, Ledger
from router.models import Completion, Context, Model, pack
from router.service import Provider, Router, Unavailable

ACTOR = Identity("alpha", "analyst", "analyst")


def models():
    return [
        Model(
            name="small",
            protocol="fixture",
            quality=0.6,
            input_per_million=1,
            output_per_million=1,
            approved_for_confidential=True,
        ),
        Model(
            name="large",
            protocol="fixture",
            quality=0.95,
            input_per_million=2,
            output_per_million=2,
        ),
    ]


def test_context_packing_preserves_question_and_deduplicates():
    request = Completion(
        prompt="Question?",
        max_output_tokens=10,
        contexts=[
            Context(source="a", text="x" * 20, priority=10),
            Context(source="b", text="x" * 20),
            Context(source="c", text="z" * 1000),
        ],
    )
    prompt, included, bound = pack(request, Model(name="tiny", quality=1, context_tokens=256))
    assert prompt.startswith("Question?") and included == ["a"]
    assert bound + request.max_output_tokens <= 256


async def test_route_quality_privacy_and_cache(tmp_path):
    service = Router(models(), Ledger(tmp_path / "usage.db"))
    normal = await service.complete(ACTOR, Completion(prompt="hello"))
    assert normal["model"] == "small"
    cached = await service.complete(ACTOR, Completion(prompt="hello"))
    assert cached["cached"] and cached["charged_usd"] == 0
    strong = await service.complete(ACTOR, Completion(prompt="hello", minimum_quality=0.9))
    assert strong["model"] == "large"
    with pytest.raises(Unavailable):
        await service.complete(
            ACTOR, Completion(prompt="hello", minimum_quality=0.9, data_class="confidential")
        )
    other = await service.complete(
        Identity("beta", "analyst", "analyst"), Completion(prompt="hello")
    )
    assert not other["cached"]


class BrokenSmall(Provider):
    async def complete(self, model, prompt, output_tokens, response_format="text"):
        if model.name == "small":
            raise httpx.ReadTimeout("simulated timeout")
        return await super().complete(model, prompt, output_tokens, response_format)


async def test_failover_charges_unknown_usage_and_opens_circuit(tmp_path):
    ledger = Ledger(tmp_path / "usage.db")
    service = Router(models(), ledger, BrokenSmall())
    for prompt in ("one", "two"):
        result = await service.complete(ACTOR, Completion(prompt=prompt))
        assert result["attempted"] == ["small", "large"]
        assert result["charged_usd"] > result["successful_call_usd"]
    result = await service.complete(ACTOR, Completion(prompt="three"))
    assert result["attempted"] == ["large"]


async def test_failed_attempt_cannot_bypass_request_budget(tmp_path):
    service = Router(models(), Ledger(tmp_path / "usage.db"), BrokenSmall())
    with pytest.raises(Unavailable):
        await service.complete(
            ACTOR, Completion(prompt="hello", max_output_tokens=20, budget_usd=0.0001)
        )


def test_atomic_tenant_budget(tmp_path):
    ledger = Ledger(tmp_path / "usage.db", daily_limit_micros=100)

    def reserve(_):
        try:
            ledger.reserve("alpha", 30)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(20))) == 3
    assert ledger.spent("alpha") == 90


class Slow(Provider):
    async def complete(self, model, prompt, output_tokens, response_format="text"):
        await asyncio.sleep(0.03)
        return await super().complete(model, prompt, output_tokens, response_format)


async def test_actual_timeout(tmp_path):
    model = models()[0].model_copy(update={"timeout_seconds": 0.001})
    service = Router([model], Ledger(tmp_path / "usage.db"), Slow())
    with pytest.raises(Unavailable):
        await service.complete(ACTOR, Completion(prompt="hello"))
