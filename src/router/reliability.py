"""Controlled concurrency/failure study of gateway accounting, not model throughput."""

import argparse
import asyncio
import json
from pathlib import Path
import tempfile
import time

import httpx
import numpy as np
from .auth import Identity
from .ledger import Ledger, BudgetExceeded
from .models import Completion, Model
from .service import Provider, Router, Unavailable


class FaultProvider(Provider):
    async def complete(self, model, prompt, output_tokens, response_format="text"):
        await asyncio.sleep(0.01)
        if model.name == "faulty":
            raise httpx.ReadTimeout("controlled lost response")
        return {"text": "measured fixture", "input_tokens": 8, "output_tokens": 4, "fixture": True}


async def study():
    with tempfile.TemporaryDirectory() as folder:
        ledger = Ledger(Path(folder) / "usage.db", daily_limit_micros=10_000_000)
        models = [
            Model(name="faulty", quality=0.9, input_per_million=1, output_per_million=1),
            Model(name="healthy", quality=0.9, input_per_million=2, output_per_million=2),
        ]
        router = Router(models, ledger, FaultProvider(), concurrency=4, cache_ttl=0)
        actor = Identity("load-study", "analyst", "analyst")
        clients = asyncio.Semaphore(24)
        rows = []

        async def request(i):
            async with clients:
                started = time.perf_counter()
                try:
                    result = await router.complete(
                        actor, Completion(prompt=f"Independent request {i}", max_output_tokens=32)
                    )
                    status, attempted = "succeeded", result["attempted"]
                except (Unavailable, BudgetExceeded) as exc:
                    status, attempted = type(exc).__name__, []
                rows.append(
                    {
                        "id": i,
                        "status": status,
                        "attempted": attempted,
                        "latency_ms": (time.perf_counter() - started) * 1000,
                    }
                )

        before = time.perf_counter()
        await asyncio.gather(*(request(i) for i in range(240)))
        elapsed = time.perf_counter() - before
        reservations = ledger.reservations(actor.tenant, limit=10000)
        uncertain = [r for r in reservations if r["state"] == "uncertain"]
        spent_before = ledger.spent(actor.tenant)
        for row in uncertain:
            ledger.settle(
                actor.tenant,
                row["id"],
                0,
                "study-reviewer",
                "Controlled provider never completed: zero charge verified",
            )
            ledger.settle(
                actor.tenant,
                row["id"],
                0,
                "study-reviewer",
                "Duplicate settlement: must not refund twice",
            )
        expected = sum(r["actual"] or 0 for r in reservations if r["state"] == "settled")
        after = ledger.spent(actor.tenant)
        if after != expected:
            raise AssertionError("Reservation settlement did not conserve known usage")
        # Reopen the database as a separate service instance; journal and quota survive.
        reopened = Ledger(Path(folder) / "usage.db", daily_limit_micros=10_000_000)
        assert reopened.spent(actor.tenant) == after
        return {
            "scope": "Local gateway, synthetic 10ms provider, injected timeouts; no real-model throughput claim",
            "requests": len(rows),
            "client_concurrency": 24,
            "provider_concurrency": 4,
            "successes": sum(r["status"] == "succeeded" for r in rows),
            "wall_seconds": elapsed,
            "requests_per_second": len(rows) / elapsed,
            "p50_ms": float(np.median([r["latency_ms"] for r in rows])),
            "p95_ms": float(np.quantile([r["latency_ms"] for r in rows], 0.95)),
            "uncertain_reservations": len(uncertain),
            "held_or_spent_micros_before": spent_before,
            "spent_micros_after_reconciliation": after,
            "known_successful_usage_micros": expected,
            "duplicate_settlement_preserved_balance": True,
            "restart_preserved_balance": True,
            "rows": rows,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    path = Path(args.output)
    if path.exists():
        parser.error("Choose a new report path")
    result = asyncio.run(study())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
