import json
import os
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Context(BaseModel):
    source: str = Field(min_length=1, max_length=100)
    text: str = Field(max_length=20000)
    priority: float = Field(default=1, ge=0, le=100, allow_inf_nan=False)


class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=20000)
    contexts: list[Context] = Field(default_factory=list, max_length=30)
    max_output_tokens: int = Field(default=256, ge=1, le=4096)
    budget_usd: float = Field(default=0.02, gt=0, le=10, allow_inf_nan=False)
    minimum_quality: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    data_class: Literal["public", "confidential"] = "public"
    response_format: Literal["text", "json"] = "text"
    task: Literal["general", "extraction", "arithmetic", "policy"] = "general"
    output_schema: dict | None = None

    @model_validator(mode="after")
    def validate_output_schema(self):
        if self.output_schema is not None:
            from jsonschema import Draft202012Validator

            encoded = json.dumps(self.output_schema)
            if len(encoded) > 12000 or '"$ref"' in encoded:
                raise ValueError(
                    "Output schema must be self-contained and at most 12000 characters"
                )
            try:
                Draft202012Validator.check_schema(self.output_schema)
            except Exception as exc:
                raise ValueError("Invalid output JSON schema") from exc
            if self.response_format != "json":
                raise ValueError("output_schema requires JSON response format")
        return self


class TaskScore(BaseModel):
    accuracy: float = Field(ge=0, le=1)
    samples: int = Field(ge=20)
    p50_ms: float = Field(ge=0)
    dataset_sha256: str = Field(min_length=64, max_length=64)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    protocol: Literal["fixture", "ollama"] = "ollama"
    endpoint: str = "http://127.0.0.1:11434"
    model: str = ""
    quality: float = Field(ge=0, le=1, allow_inf_nan=False)
    input_per_million: float = Field(default=0, ge=0, allow_inf_nan=False)
    output_per_million: float = Field(default=0, ge=0, allow_inf_nan=False)
    context_tokens: int = Field(default=8192, ge=128)
    approved_for_confidential: bool = False
    timeout_seconds: float = Field(default=10, gt=0, le=30)
    api_key_env: str | None = None
    task_scores: dict[str, TaskScore] = Field(default_factory=dict)
    calibration_model_digest: str | None = None

    @model_validator(mode="after")
    def valid_endpoint(self):
        if not self.endpoint.startswith(("https://", "http://")):
            raise ValueError("Endpoint must be HTTP(S)")
        return self


def model_config():
    path = os.getenv("MODELS_CONFIG")
    raw = os.getenv("MODELS_CONFIG_JSON")
    if path or raw:
        if path:
            with open(path) as handle:
                data = json.load(handle)
        else:
            data = json.loads(raw)
        models = [Model(**item) for item in data]
        if len({m.name for m in models}) != len(models):
            raise ValueError("Model names must be unique")
        return models
    if os.getenv("APP_DEMO") == "1":
        return [
            Model(
                name="fixture-small",
                protocol="fixture",
                quality=0.6,
                input_per_million=0.1,
                output_per_million=0.2,
                approved_for_confidential=True,
            ),
            Model(
                name="fixture-large",
                protocol="fixture",
                quality=0.9,
                input_per_million=1,
                output_per_million=2,
                approved_for_confidential=True,
            ),
        ]
    return []


def pack(request, model):
    # Conservative UTF-8 byte budget, plus fixed framing allowance. This is not a tokenizer.
    allowance = model.context_tokens - request.max_output_tokens - 64
    prompt = request.prompt
    if len(prompt.encode()) > allowance:
        raise ValueError("Prompt cannot fit model context")
    included = []
    seen = set()
    for context in sorted(request.contexts, key=lambda c: -c.priority):
        if context.text in seen:
            continue
        seen.add(context.text)
        block = "\n\nUNTRUSTED EVIDENCE " + json.dumps(
            {"source": context.source, "text": context.text}
        )
        if len((prompt + block).encode()) <= allowance:
            prompt += block
            included.append(context.source)
    return prompt, included, len(prompt.encode()) + 64


def cost_micros(model, input_tokens, output_tokens):
    import math

    # One USD micro-unit per token at $1 per million tokens; round reservations up.
    return math.ceil(
        input_tokens * model.input_per_million + output_tokens * model.output_per_million
    )
