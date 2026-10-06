import hmac
import json
import os
from dataclasses import dataclass
from fastapi import Header, HTTPException, Request


@dataclass(frozen=True)
class Identity:
    tenant: str
    user: str
    role: str


DEMO_KEYS = {
    "demo-analyst": {"tenant": "alpha", "user": "analyst", "role": "analyst"},
    "demo-reviewer": {"tenant": "alpha", "user": "reviewer", "role": "reviewer"},
    "demo-beta": {"tenant": "beta", "user": "beta-analyst", "role": "analyst"},
}


def configured_keys():
    raw = os.getenv("API_KEYS_JSON")
    if raw:
        keys = json.loads(raw)
        if not keys or any(len(k) < 24 for k in keys):
            raise ValueError("Configure API keys with at least 24 characters")
        for value in keys.values():
            Identity(**value)
        return keys
    if os.getenv("APP_DEMO") == "1":
        return DEMO_KEYS
    return {}


def identity(request: Request, authorization: str = Header(default="")):
    token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    for candidate, claims in request.app.state.keys.items():
        if hmac.compare_digest(token, candidate):
            return Identity(**claims)
    raise HTTPException(401, "Valid bearer credentials required")
