import asyncio
import hashlib
import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
import httpx
from .models import cost_micros, pack


class Unavailable(Exception):
    pass


@dataclass
class Circuit:
    failures: int = 0
    open_until: float = 0


class Provider:
    async def complete(self, model, prompt, output_tokens, response_format="text"):
        if model.protocol == "fixture":
            return {
                "text": json.dumps({"fixture": True})
                if response_format == "json"
                else "Fixture response: provider connectivity and routing demonstration only.",
                "input_tokens": len(prompt.split()),
                "output_tokens": min(output_tokens, 12),
                "fixture": True,
            }
        headers = {}
        if model.api_key_env:
            headers["Authorization"] = "Bearer " + os.environ[model.api_key_env]
        async with httpx.AsyncClient(
            timeout=model.timeout_seconds, follow_redirects=False
        ) as client:
            response = await client.post(
                model.endpoint.rstrip("/") + "/api/generate",
                headers=headers,
                json={
                    "model": model.model,
                    "prompt": prompt,
                    "stream": False,
                    "raw": True,
                    **({"format": "json"} if response_format == "json" else {}),
                    "options": {
                        "num_predict": output_tokens,
                        "num_ctx": model.context_tokens,
                        "temperature": 0,
                    },
                },
            )
            response.raise_for_status()
            data = response.json()
        if not data.get("done") or not isinstance(data.get("response"), str):
            raise ValueError("Malformed provider output")
        return {
            "text": data["response"],
            "input_tokens": data["prompt_eval_count"],
            "output_tokens": data["eval_count"],
            "fixture": False,
        }


class Router:
    def __init__(self, models, ledger, provider=None, concurrency=4, cache_ttl=60):
        self.models = models
        self.ledger = ledger
        self.provider = provider or Provider()
        self.capacity = asyncio.Semaphore(concurrency)
        self.cache = OrderedDict()
        self.cache_ttl = cache_ttl
        self.circuits = {m.name: Circuit() for m in models}

    async def complete(self, actor, request):
        key = hashlib.sha256(
            json.dumps(
                [
                    actor.tenant,
                    actor.user,
                    actor.role,
                    request.model_dump(),
                    [m.model_dump() for m in self.models],
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        cached = self.cache.get(key)
        if cached and cached[0] > time.monotonic():
            return {**cached[1], "cached": True, "charged_usd": 0.0}
        candidates = []
        for model in self.models:
            if model.quality < request.minimum_quality:
                continue
            if request.data_class == "confidential" and not model.approved_for_confidential:
                continue
            if self.circuits[model.name].open_until > time.monotonic():
                continue
            try:
                prompt, sources, input_bound = pack(request, model)
            except ValueError:
                continue
            estimate = cost_micros(model, input_bound, request.max_output_tokens)
            candidates.append((estimate, model.name, model, prompt, sources, input_bound))
        candidates.sort(key=lambda item: (item[0], item[1]))
        spent = 0
        attempted = []
        try:
            await asyncio.wait_for(self.capacity.acquire(), timeout=1)
        except TimeoutError:
            raise Unavailable("Router is at capacity") from None
        try:
            for estimate, _, model, prompt, sources, input_bound in candidates[:3]:
                if spent + estimate > int(request.budget_usd * 1_000_000):
                    continue
                day = self.ledger.reserve(actor.tenant, estimate)
                spent += estimate
                attempted.append(model.name)
                started = time.perf_counter()
                circuit = self.circuits[model.name]
                try:
                    result = await asyncio.wait_for(
                        self.provider.complete(
                            model, prompt, request.max_output_tokens, request.response_format
                        ),
                        timeout=model.timeout_seconds,
                    )
                    it, ot = result["input_tokens"], result["output_tokens"]
                    if (
                        not isinstance(it, int)
                        or not isinstance(ot, int)
                        or not 0 <= it <= input_bound
                        or not 0 <= ot <= request.max_output_tokens
                        or not isinstance(result["text"], str)
                        or len(result["text"].encode()) > 1_000_000
                    ):
                        raise ValueError("Provider usage or output violated contract")
                    if request.response_format == "json":
                        json.loads(result["text"])
                    actual = cost_micros(model, it, ot)
                    self.ledger.refund(actor.tenant, day, estimate - actual)
                    spent -= estimate - actual
                    circuit.failures = 0
                    circuit.open_until = 0
                    response = {
                        **result,
                        "model": model.name,
                        "cached": False,
                        "charged_usd": spent / 1_000_000,
                        "successful_call_usd": actual / 1_000_000,
                        "latency_ms": (time.perf_counter() - started) * 1000,
                        "sources": sources,
                        "attempted": attempted,
                    }
                    self.cache[key] = (time.monotonic() + self.cache_ttl, response)
                    self.cache.move_to_end(key)
                    while len(self.cache) > 128:
                        self.cache.popitem(last=False)
                    return response
                except (httpx.HTTPError, TimeoutError, ValueError, KeyError):
                    # Keep the reservation: an interrupted provider may still have billed the request.
                    circuit.failures += 1
                    if circuit.failures >= 2:
                        circuit.open_until = time.monotonic() + 30
            raise Unavailable("No permitted model completed within the request budget")
        finally:
            self.capacity.release()
