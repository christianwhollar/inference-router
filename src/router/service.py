import asyncio
import hashlib
import json
import os
import time
import uuid
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
    probing: bool = False


def generation_schema(schema):
    """Omit string bounds from Ollama's grammar; validate the full contract afterward.

    The tested Ollama sampler rejects some bounded-string grammars (including a
    2000-character summary). Token/output limits still bound generation, and the
    router's JSON Schema validator retains every requested constraint.
    """
    if isinstance(schema, bool):
        return schema
    result = {k: v for k, v in schema.items() if k not in {"minLength", "maxLength"}}
    for key in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
        if key in result:
            result[key] = {name: generation_schema(value) for name, value in result[key].items()}
    for key in (
        "items",
        "contains",
        "additionalProperties",
        "not",
        "if",
        "then",
        "else",
        "propertyNames",
    ):
        if isinstance(result.get(key), dict):
            result[key] = generation_schema(result[key])
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        if key in result:
            result[key] = [generation_schema(value) for value in result[key]]
    return result


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
        grammar = (
            generation_schema(response_format)
            if isinstance(response_format, dict)
            else response_format
        )
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
                    "raw": False,
                    **({"format": grammar} if response_format != "text" else {}),
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

    def explain(self, request):
        routes = []
        for model in self.models:
            reasons = []
            profile = model.task_scores.get(request.task)
            quality = profile.accuracy if profile else model.quality
            if quality < request.minimum_quality:
                reasons.append("quality_below_threshold")
            if request.data_class == "confidential" and not model.approved_for_confidential:
                reasons.append("privacy_policy")
            if self.circuits[model.name].open_until > time.monotonic():
                reasons.append("circuit_open")
            estimate, sources = None, []
            try:
                _, sources, bound = pack(request, model)
                estimate = cost_micros(model, bound, request.max_output_tokens)
                if estimate > int(request.budget_usd * 1e6):
                    reasons.append("request_budget")
            except ValueError:
                reasons.append("context_capacity")
            routes.append(
                {
                    "model": model.name,
                    "eligible": not reasons,
                    "reasons": reasons,
                    "quality": quality,
                    "quality_source": "task_calibration" if profile else "operator_rating",
                    "calibration_samples": profile.samples if profile else None,
                    "estimated_latency_ms": profile.p50_ms if profile else None,
                    "reserved_bound_usd": estimate / 1e6 if estimate is not None else None,
                    "included_sources": sources,
                }
            )
        return routes

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
            profile = model.task_scores.get(request.task)
            quality = profile.accuracy if profile else model.quality
            if quality < request.minimum_quality:
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
        candidates.sort(
            key=lambda item: (
                item[0],
                item[2].task_scores[request.task].p50_ms
                if request.task in item[2].task_scores
                else float("inf"),
                item[1],
            )
        )
        spent = 0
        request_id = str(uuid.uuid4())
        attempted = []
        try:
            await asyncio.wait_for(self.capacity.acquire(), timeout=1)
        except TimeoutError:
            raise Unavailable("Router is at capacity") from None
        try:
            for estimate, _, model, prompt, sources, input_bound in candidates[:3]:
                if spent + estimate > int(request.budget_usd * 1_000_000):
                    continue
                circuit = self.circuits[model.name]
                if circuit.open_until > time.monotonic() or circuit.probing:
                    continue
                if circuit.failures >= 2:
                    circuit.probing = True
                try:
                    reservation = self.ledger.begin(actor.tenant, model.name, estimate, request_id)
                except Exception:
                    circuit.probing = False
                    raise
                spent += estimate
                attempted.append(model.name)
                started = time.perf_counter()
                try:
                    result = await asyncio.wait_for(
                        self.provider.complete(
                            model,
                            prompt,
                            request.max_output_tokens,
                            request.output_schema or request.response_format,
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
                        parsed = json.loads(result["text"])
                        if request.output_schema:
                            from jsonschema import Draft202012Validator

                            if not Draft202012Validator(request.output_schema).is_valid(parsed):
                                raise ValueError("Provider output failed the requested JSON schema")
                    actual = cost_micros(model, it, ot)
                    self.ledger.settle(actor.tenant, reservation, actual)
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
                        "request_id": request_id,
                        "reservation_id": reservation,
                        "quality_source": "task_calibration"
                        if request.task in model.task_scores
                        else "operator_rating",
                    }
                    self.cache[key] = (time.monotonic() + self.cache_ttl, response)
                    self.cache.move_to_end(key)
                    while len(self.cache) > 128:
                        self.cache.popitem(last=False)
                    return response
                except (httpx.HTTPError, TimeoutError, ValueError, KeyError):
                    # Keep the reservation: an interrupted provider may still have billed the request.
                    circuit.failures += 1
                    self.ledger.uncertain(
                        actor.tenant, reservation, "provider failed or usage could not be verified"
                    )
                    if circuit.failures >= 2:
                        circuit.open_until = time.monotonic() + 30
                except asyncio.CancelledError:
                    self.ledger.uncertain(
                        actor.tenant,
                        reservation,
                        "request cancelled while provider usage was unknown",
                    )
                    raise
                finally:
                    circuit.probing = False
            raise Unavailable("No permitted model completed within the request budget")
        finally:
            self.capacity.release()
