"""Wonder AI 2API gateway — OpenAI-compatible in front of api.wonderai.app.

Run:  python server.py            (defaults 0.0.0.0:8960)
Env:  WONDER_UIDS=uid1,uid2      pre-seed the account pool (optional)
      WONDER_CONCURRENCY=4       max in-flight upstream generations
      WONDER_PORT=8950
"""

from __future__ import annotations

import base64
import itertools
import os
import threading
import time
import uuid
from typing import Any

import requests
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from wonder_client import (
    APP_VERSION,
    ASPECT_RATIOS,
    SIZE_TO_ASPECT,
    WonderClient,
    WonderError,
    load_styles,
)

MAX_CONCURRENCY = int(os.getenv("WONDER_CONCURRENCY", "4"))
PORT = int(os.getenv("WONDER_PORT", "8960"))
HERE = os.path.dirname(os.path.abspath(__file__))
PROVIDERS = ("premium", "flux", "falai", "normal")
MODEL_TO_PROVIDER = {f"wonder-{p}": p for p in PROVIDERS}

STYLES = load_styles(os.path.join(HERE, "styles.json"))
STYLE_IDS = {s["value"] for s in STYLES}
STYLE_ALIASES = {s["name"].strip().lower(): s["value"] for s in STYLES}


class AccountPool:
    """Rotating pool of cloud identities.

    A userId is a free, unauthenticated identity: the server registers any string on
    first contact. Dead or exhausted accounts are simply replaced with a new uuid.
    """

    def __init__(self, user_ids: list[str]) -> None:
        self._lock = threading.Lock()
        self._accounts = [WonderClient(user_id=uid) for uid in user_ids]
        self._cursor = itertools.count()

    def acquire(self) -> WonderClient:
        with self._lock:
            return self._accounts[next(self._cursor) % len(self._accounts)]

    def replace(self, account: WonderClient) -> WonderClient:
        fresh = WonderClient()
        with self._lock:
            self._accounts = [fresh if acc is account else acc for acc in self._accounts]
        return fresh

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"userId": acc.user_id, "premium": acc.premium} for acc in self._accounts]


_seed_uids = [uid.strip() for uid in os.getenv("WONDER_UIDS", "").split(",") if uid.strip()]
pool = AccountPool(_seed_uids or [str(uuid.uuid4())])
_gate = threading.BoundedSemaphore(MAX_CONCURRENCY)

app = FastAPI(title="Wonder AI 2API", version=APP_VERSION)


def _recover(exc: WonderError, account: WonderClient) -> WonderError:
    """Fresh identity for budget/identity failures; otherwise keep the account."""
    blob = str(exc).lower()
    if any(marker in blob for marker in ("credit", "quota", "limit", "user", "not found")):
        pool.replace(account)
    return exc


def _generate(payload: dict[str, Any]) -> dict[str, Any]:
    provider = payload.pop("provider", "premium")
    account = pool.acquire()
    payload["userId"] = account.user_id
    try:
        with _gate:
            return account.generate(
                provider=provider,
                model=payload.pop("model_name", "default"),
                **{k: v for k, v in payload.items() if k != "userId"},
            )
    except WonderError as exc:
        raise _recover(exc, account) from exc


# --------------------------------------------------------------- OpenAI API


class ImageGenerationRequest(BaseModel):
    model: str = "wonder-premium"
    prompt: str = Field(min_length=1, max_length=3800)
    n: int = Field(default=1, ge=1, le=10)
    size: str = "1024x1024"
    response_format: str = "url"
    style: str | None = None
    negative_prompt: str | None = None
    seed: int | None = None
    # Wonder-specific escape hatches (ignored by stock OpenAI clients)
    provider: str | None = None
    upstream_model: str = "default"


@app.post("/v1/images/generations")
def images_generations(req: ImageGenerationRequest) -> JSONResponse:
    provider = req.provider or MODEL_TO_PROVIDER.get(req.model)
    if provider is None:
        raise HTTPException(400, f"unknown model {req.model!r}; use one of {sorted(MODEL_TO_PROVIDER)}")
    if provider not in PROVIDERS:
        raise HTTPException(400, f"unknown provider {provider!r}; use one of {list(PROVIDERS)}")

    style = req.style or "s2"
    style = STYLE_ALIASES.get(style.strip().lower(), style)
    if style not in STYLE_IDS:
        raise HTTPException(400, f"unknown style {req.style!r}; GET /wonder/styles for the catalogue")

    aspect = SIZE_TO_ASPECT.get(req.size) or (req.size if req.size in ASPECT_RATIOS else None)
    if aspect is None:
        raise HTTPException(
            400, f"unsupported size {req.size!r}; use one of {sorted(SIZE_TO_ASPECT)} or {list(ASPECT_RATIOS)}"
        )

    started = time.time()
    try:
        result = _generate(
            {
                "prompt": req.prompt,
                "style": style,
                "provider": provider,
                "model_name": req.upstream_model,
                "n": req.n,
                "aspect_ratio": aspect,
                "negative_prompt": req.negative_prompt,
                "seed": req.seed,
            }
        )
    except WonderError as exc:
        raise HTTPException(502, f"upstream error: {exc}") from exc

    urls = result.get("urls") or []
    data: list[dict[str, Any]] = []
    for url in urls:
        if req.response_format == "b64_json":
            blob = requests.get(url, timeout=120).content
            data.append({"b64_json": base64.b64encode(blob).decode()})
        else:
            data.append({"url": url, "revised_prompt": req.prompt})
    return JSONResponse(
        {
            "created": int(started),
            "data": data,
            "seed": result.get("seed"),
            "elapsed": round(time.time() - started, 2),
        }
    )


@app.get("/v1/models")
def models() -> dict[str, Any]:
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": model, "object": "model", "created": now, "owned_by": "wonderai"}
            for model in MODEL_TO_PROVIDER
        ],
    }


# ------------------------------------------------------------- native routes


class NativeCreateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=3800)
    style: str = "s2"
    provider: str = "premium"
    n: int = Field(default=1, ge=1, le=10)
    aspect_ratio: str = "1:1"
    negative_prompt: str | None = None
    seed: int | None = None
    fix_seed: bool = False
    steps: int | None = Field(default=None, ge=1, le=100)
    cfg_scale: float | None = Field(default=None, ge=0, le=30)
    expand_prompt: bool | None = None
    init_image_url: str | None = None


@app.post("/wonder/image/create")
def native_create(req: NativeCreateRequest) -> dict[str, Any]:
    if req.provider not in PROVIDERS:
        raise HTTPException(400, f"unknown provider; use one of {list(PROVIDERS)}")
    if req.style not in STYLE_IDS:
        raise HTTPException(400, "unknown style; GET /wonder/styles")
    started = time.time()
    try:
        result = _generate(req.model_dump())
    except WonderError as exc:
        raise HTTPException(502, f"upstream error: {exc}") from exc
    result["elapsed"] = round(time.time() - started, 2)
    return result


class UpscaleRequest(BaseModel):
    url: str


@app.post("/wonder/upscale")
def native_upscale(req: UpscaleRequest) -> dict[str, Any]:
    account = pool.acquire()
    started = time.time()
    try:
        with _gate:
            url = account.upscale(req.url)
    except WonderError as exc:
        raise HTTPException(502, f"upstream error: {exc}") from exc
    return {"url": url, "elapsed": round(time.time() - started, 2)}


@app.get("/wonder/styles")
def styles() -> dict[str, Any]:
    return {
        "count": len(STYLES),
        "data": [
            {
                "value": s["value"],
                "name": s["name"],
                "is_pro": bool(s.get("is_pro")),
                "on_home": bool(s.get("show_on_home_screen")),
                "priority": s.get("priority_point"),
                "sample": s.get("image_url"),
            }
            for s in sorted(STYLES, key=lambda s: -(s.get("priority_point") or 0))
        ],
    }


@app.get("/wonder/account")
def account() -> dict[str, Any]:
    client = pool.acquire()
    try:
        return {"userId": client.user_id, **client.user()}
    except WonderError as exc:
        raise HTTPException(502, f"upstream error: {exc}") from exc


@app.get("/wonder/pool")
def pool_state() -> dict[str, Any]:
    return {"size": len(pool.snapshot()), "concurrency": MAX_CONCURRENCY, "accounts": pool.snapshot()}


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "styles": len(STYLES), "aspect_ratios": list(ASPECT_RATIOS)}


@app.get("/")
def demo() -> FileResponse:
    return FileResponse(os.path.join(HERE, "static", "index.html"))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
