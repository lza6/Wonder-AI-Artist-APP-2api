"""Local stress test for the Wonder AI 2API gateway.

Examples
    python stress_test.py                       # 40 requests, 4 concurrent, via local gateway
    python stress_test.py -n 120 -c 8           # heavier
    python stress_test.py --target upscale      # stress the 4x upscaler
    python stress_test.py --verify-images       # additionally download every result
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from collections import Counter

import httpx

PROMPTS = [
    "a lighthouse on a cliff at sunset, volumetric light",
    "cyberpunk street market at night, neon reflections",
    "a red fox in a snowy forest, soft morning light",
    "ancient temple floating above the clouds",
    "portrait of an astronaut, studio lighting",
    "a bowl of ramen, steam rising, top-down view",
    "surreal desert with giant glass flowers",
    "a vintage motorcycle on a coastal road",
]

SIZES = ["1024x1024", "1024x576", "576x1024", "1024x768", "768x1024"]


async def one_generation(client: httpx.AsyncClient, base: str, idx: int) -> dict:
    payload = {
        "prompt": PROMPTS[idx % len(PROMPTS)],
        "model": "wonder-premium",
        "n": 1,
        "size": SIZES[idx % len(SIZES)],
    }
    started = time.perf_counter()
    try:
        resp = await client.post(f"{base}/v1/images/generations", json=payload, timeout=300)
        elapsed = time.perf_counter() - started
        body = resp.json()
        if resp.status_code != 200:
            return {"ok": False, "elapsed": elapsed, "error": str(body.get("detail"))[:180]}
        urls = [item.get("url") for item in body.get("data", [])]
        return {"ok": bool(urls), "elapsed": elapsed, "urls": urls, "error": None if urls else "empty data"}
    except Exception as exc:  # noqa: BLE001 - stress harness records any failure kind
        return {"ok": False, "elapsed": time.perf_counter() - started, "error": f"{type(exc).__name__}: {exc}"[:180]}


async def one_upscale(client: httpx.AsyncClient, base: str, url: str, idx: int) -> dict:
    started = time.perf_counter()
    try:
        resp = await client.post(f"{base}/wonder/upscale", json={"url": url}, timeout=300)
        elapsed = time.perf_counter() - started
        body = resp.json()
        if resp.status_code != 200:
            return {"ok": False, "elapsed": elapsed, "error": str(body.get("detail"))[:180]}
        return {"ok": True, "elapsed": elapsed, "urls": [body["url"]], "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "elapsed": time.perf_counter() - started, "error": f"{type(exc).__name__}: {exc}"[:180]}


async def verify_image(client: httpx.AsyncClient, url: str) -> tuple[bool, int]:
    try:
        resp = await client.get(url, timeout=180)
        return resp.status_code == 200 and len(resp.content) > 4096, len(resp.content)
    except Exception:  # noqa: BLE001
        return False, 0


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
    return ordered[index]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--total", type=int, default=40)
    parser.add_argument("-c", "--concurrency", type=int, default=4)
    parser.add_argument("--base", default="http://127.0.0.1:8960")
    parser.add_argument("--target", choices=["generate", "upscale"], default="generate")
    parser.add_argument("--verify-images", action="store_true")
    parser.add_argument("--upscale-source", default=None, help="image URL for --target upscale")
    args = parser.parse_args()

    limits = httpx.Limits(max_connections=args.concurrency + 4, max_keepalive_connections=args.concurrency)
    async with httpx.AsyncClient(limits=limits) as client:
        health = await client.get(f"{args.base}/health", timeout=20)
        print(f"gateway {args.base} -> {health.status_code} {health.json()}")

        source = args.upscale_source
        if args.target == "upscale" and not source:
            seed = await client.post(
                f"{args.base}/v1/images/generations",
                json={"prompt": PROMPTS[0], "model": "wonder-premium", "n": 1},
                timeout=300,
            )
            source = seed.json()["data"][0]["url"]
            print(f"upscale source: {source}")

        semaphore = asyncio.Semaphore(args.concurrency)
        queue = asyncio.Queue()
        for idx in range(args.total):
            queue.put_nowait(idx)

        async def worker() -> None:
            while True:
                try:
                    idx = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                async with semaphore:
                    if args.target == "upscale":
                        result = await one_upscale(client, args.base, source, idx)
                    else:
                        result = await one_generation(client, args.base, idx)
                results.append(result)

        results: list[dict] = []
        started = time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(args.concurrency)))
        wall = time.perf_counter() - started

        ok = [r for r in results if r["ok"]]
        failed = [r for r in results if not r["ok"]]
        latencies = [r["elapsed"] for r in ok]

        print("\n=== 结果 ===")
        print(f"总请求      : {len(results)}   并发: {args.concurrency}")
        print(f"成功 / 失败 : {len(ok)} / {len(failed)}  (成功率 {len(ok)/len(results)*100:.1f}%)")
        print(f"墙钟耗时    : {wall:.1f}s   吞吐: {len(ok)/wall:.2f} req/s")
        if latencies:
            print(
                f"延迟 p50/p90/p99/max : {statistics.median(latencies):.2f}s / "
                f"{pct(latencies,0.9):.2f}s / {pct(latencies,0.99):.2f}s / {max(latencies):.2f}s"
            )
        if failed:
            print("失败原因分布:")
            for reason, count in Counter(f["error"] for f in failed).most_common():
                print(f"  x{count}  {reason}")

        if args.verify_images and ok:
            sample = [r["urls"][0] for r in ok[: min(8, len(ok))]]
            checks = await asyncio.gather(*(verify_image(client, url) for url in sample))
            good = sum(1 for valid, _ in checks if valid)
            sizes = [size for _, size in checks if size]
            print(f"产物下载校验: {good}/{len(checks)} 张可下载，平均 {statistics.mean(sizes)/1024:.0f}KB" if sizes else "产物下载校验: 全部失败")

        return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
