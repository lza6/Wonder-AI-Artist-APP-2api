"""Wonder AI Art Generator 5.1.4 protocol client.

Static reverse of com.codeway.wonder (Flutter AOT, libapp.so) — see docs/PROTOCOL.md.

Every business request to https://api.wonderai.app/api/v1/* carries three custom headers:

    ts = <epoch milliseconds>
    ip = 679449021 (premium sentinel) | random int (free tier)
    rs = SHA256( jsonBody + "_" + RS_KEY + "_" + userId + "_" + ts ).hexdigest().upper()

`jsonBody` must be byte-identical to the bytes that are POSTed.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import requests

API_BASE = "https://api.wonderai.app/api/v1/"
HIGHRES_URL = "https://highres.wonderai.app/esrgan"

RS_KEY = "COvereD062doiNgS855TOshIBa"
PREMIUM_IP = 679449021
# _generateRandomInt() in the app returns [0, 1e9) and remaps the premium sentinel to 1234.
FREE_IP_CEILING = 1_000_000_000

# Hardcoded bearer used only by the /esrgan upscaler (no per-user auth).
HIGHRES_TOKEN = (
    "Bearer d5jf7w9b4QdGm3KSK4P1cRUlWTjWziPmdjBJVlEHv4SyyhCpjIpkUwYXjHsW6983"
)

# aspect ratios the upstream actually renders; anything else collapses to 1:1.
ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "4:3": (1024, 768),
    "3:4": (768, 1024),
    "16:9": (1024, 576),
    "9:16": (576, 1024),
}
SIZE_TO_ASPECT = {f"{w}x{h}": ar for ar, (w, h) in ASPECT_RATIOS.items()}

MAX_IMAGES = 10  # measured: 10 accepted, 20 rejected upstream with 500
APP_VERSION = "5.1.4"
PLATFORM = "android"


def _canonical(value: Any) -> Any:
    """Match the server's JSON round-trip.

    The backend parses the body and re-serializes it before verifying `rs`, so a
    float that is integral (`8.0`) becomes `8` and the signature no longer matches.
    Emitting the canonical form up front keeps body bytes == signed bytes.
    """
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


class WonderError(RuntimeError):
    """Upstream returned success=false or a non-2xx status."""

    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self.payload = payload
        if isinstance(payload, dict):
            code = payload.get("error") or "upstream-error"
            message = payload.get("errorMessage") or ""
        else:
            code, message = "upstream-error", str(payload)[:300]
        super().__init__(f"{status} {code}: {message}")


@dataclass
class WonderClient:
    """One cloud identity = one arbitrary `userId` string.

    The server registers any userId on first contact, so a fresh uuid4 string is a
    fresh account with its own credit balance and prompt history.
    """

    user_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    premium: bool = True
    timeout: float = 180.0
    skew_ms: int = 0
    _session: requests.Session = field(default_factory=requests.Session, repr=False)

    # ---------------------------------------------------------------- infra

    def _headers(self, raw_body: str, ts: int) -> dict[str, str]:
        digest = hashlib.sha256(
            f"{raw_body}_{RS_KEY}_{self.user_id}_{ts}".encode()
        ).hexdigest()
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "rs": digest.upper(),
            "ip": str(PREMIUM_IP if self.premium else random.randrange(FREE_IP_CEILING)),
            "ts": str(ts),
        }

    def _post(self, url: str, body: dict[str, Any], headers_extra: dict[str, str] | None = None) -> Any:
        raw = json.dumps(_canonical(body), separators=(",", ":"))
        for attempt in range(4):
            ts = int(time.time() * 1000) + self.skew_ms + (attempt * 5000)
            headers = self._headers(raw, ts)
            if headers_extra:
                headers.update(headers_extra)
            try:
                resp = self._session.post(url, data=raw.encode(), headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                # A local proxy dropping an idle keep-alive socket shows up here; a fresh
                # session forces a new connection instead of reusing the dead one.
                self._session = requests.Session()
                if attempt < 3:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                raise WonderError(-1, {"error": type(exc).__name__, "errorMessage": str(exc)[:300]}) from exc
            try:
                payload = resp.json()
            except ValueError:
                payload = {"success": False, "error": resp.status_code, "errorMessage": resp.text[:300]}

            # "Request time is expired" means our clock lags the server: re-sign with a
            # forward offset and remember it for subsequent calls.
            if (
                isinstance(payload, dict)
                and "time is expired" in str(payload.get("errorMessage", ""))
                and attempt < 2
            ):
                self.skew_ms += 5000
                continue
            if resp.status_code >= 400 or not payload.get("success", False):
                raise WonderError(resp.status_code, payload)
            return payload
        raise WonderError(401, {"error": "request-signature", "errorMessage": "clock skew retries exhausted"})

    # ------------------------------------------------------------- endpoints

    def user(self) -> dict[str, Any]:
        """GET /user/get — credit balance, daily counter, saved prompts."""
        return self._post(API_BASE + "user/get", {"userId": self.user_id})

    def generate(
        self,
        prompt: str,
        *,
        style: str = "s2",
        provider: str = "premium",
        model: str = "default",
        n: int = 1,
        aspect_ratio: str = "1:1",
        negative_prompt: str | None = None,
        seed: int | None = None,
        fix_seed: bool = False,
        steps: int | None = None,
        cfg_scale: float | None = None,
        expand_prompt: bool | None = None,
        init_image_url: str | None = None,
        feature: str = "create",
        safety_checker: bool = False,
        ad_watched: bool = False,
    ) -> dict[str, Any]:
        """POST /image/create — synchronous text/image-to-image generation."""
        n = max(1, min(int(n), MAX_IMAGES))
        if aspect_ratio not in ASPECT_RATIOS:
            aspect_ratio = "1:1"
        body: dict[str, Any] = {
            "userId": self.user_id,
            "prompt": prompt,
            "style": style,
            "provider": provider,
            "num_images": n,  # legacy field, upstream ignores it — kept for parity
            "platform": PLATFORM,
            "appVersion": APP_VERSION,
            "enableSafetyChecker": safety_checker,
            "adWatched": ad_watched,
            "adReason": "",
            "feature": feature,
            "model": model,
            "numImages": n,  # the field the backend actually honours
            "aspectRatio": aspect_ratio,
            "isSeedFixed": fix_seed,
        }
        if negative_prompt:
            body["negativePrompt"] = negative_prompt
        if seed is not None:
            body["seed"] = int(seed)
            body["isSeedFixed"] = True
        if steps is not None:
            body["numInferenceSteps"] = int(steps)
        if cfg_scale is not None:
            body["cfgScale"] = float(cfg_scale)
        if expand_prompt is not None:
            body["expandPrompt"] = bool(expand_prompt)
        if init_image_url:
            body["imageUrl"] = init_image_url
        return self._post(API_BASE + "image/create", body)

    def upscale(self, image_url: str) -> str:
        """POST /esrgan — 4x upscale. No credits consumed, no userId check upstream."""
        payload = self._post(
            HIGHRES_URL,
            {"userId": self.user_id, "url": image_url, "platform": PLATFORM},
            headers_extra={"Authorization": HIGHRES_TOKEN},
        )
        return payload["highResImageUrl"]

    def ad_show(self) -> dict[str, Any]:
        return self._post(API_BASE + "ad/show", {"userId": self.user_id})

    def credit_inc(self) -> dict[str, Any]:
        return self._post(API_BASE + "credit/inc", {"userId": self.user_id})

    def delete_prompt(self, prompt: str) -> dict[str, Any]:
        return self._post(API_BASE + "prompt/delete", {"userId": self.user_id, "prompt": prompt})

    def dreambooth_get(self) -> dict[str, Any]:
        return self._post(API_BASE.replace("api.", "dreambooth-api.") + "dreambooth/get", {"userId": self.user_id})


def load_styles(path: str = "styles.json") -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Wonder AI CLI")
    parser.add_argument("prompt", nargs="?", default="a lighthouse on a cliff at sunset")
    parser.add_argument("--style", default="s2")
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--ar", default="1:1")
    parser.add_argument("--provider", default="premium")
    parser.add_argument("--uid", default=None, help="reuse an existing userId")
    args = parser.parse_args()

    client = WonderClient(user_id=args.uid) if args.uid else WonderClient()
    print("userId:", client.user_id)
    print("account:", client.user())
    started = time.time()
    result = client.generate(args.prompt, style=args.style, n=args.n, aspect_ratio=args.ar, provider=args.provider)
    print(f"took {time.time() - started:.1f}s seed={result.get('seed')}")
    for url in result.get("urls", []):
        print(url)
