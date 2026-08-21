from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.request
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)


def percentile(values, value):
    if not values:
        return 0.0

    ordered = sorted(values)

    index = (
        math.ceil(
            len(ordered)
            * value
            / 100
        )
        - 1
    )

    index = max(
        0,
        min(
            index,
            len(ordered) - 1,
        ),
    )

    return ordered[index]


def request_once(
    base_url,
    path,
    timeout,
):
    url = (
        base_url.rstrip("/")
        + (
            path
            if path.startswith("/")
            else "/" + path
        )
    )

    started = time.perf_counter()

    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent":
                    "SpyonLoadTest/1.0",
            },
            method="GET",
        )

        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            response.read(2048)
            status = int(response.status)

        return {
            "ok": 200 <= status < 400,
            "status": status,
            "latency_ms": (
                time.perf_counter()
                - started
            ) * 1000,
            "path": path,
            "error": "",
        }

    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "status": int(exc.code),
            "latency_ms": (
                time.perf_counter()
                - started
            ) * 1000,
            "path": path,
            "error": str(exc),
        }

    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "latency_ms": (
                time.perf_counter()
                - started
            ) * 1000,
            "path": path,
            "error": type(exc).__name__,
        }


def run_load(
    base_url,
    requests_count,
    concurrency,
    paths,
    timeout,
):
    requests_count = max(
        1,
        int(requests_count),
    )

    concurrency = max(
        1,
        min(
            int(concurrency),
            requests_count,
        ),
    )

    paths = [
        str(path).strip()
        for path in paths
        if str(path).strip()
    ]

    if not paths:
        raise ValueError(
            "At least one path is required"
        )

    planned = [
        paths[index % len(paths)]
        for index in range(
            requests_count
        )
    ]

    started = time.perf_counter()

    results = []

    with ThreadPoolExecutor(
        max_workers=concurrency
    ) as pool:

        futures = [
            pool.submit(
                request_once,
                base_url,
                path,
                timeout,
            )
            for path in planned
        ]

        for future in as_completed(
            futures
        ):
            results.append(
                future.result()
            )

    elapsed = (
        time.perf_counter()
        - started
    )

    latencies = [
        item["latency_ms"]
        for item in results
    ]

    successful = sum(
        1
        for item in results
        if item["ok"]
    )

    statuses = {}

    for item in results:
        key = str(
            item["status"]
            or item["error"]
            or "error"
        )

        statuses[key] = (
            statuses.get(key, 0)
            + 1
        )

    return {
        "requests": len(results),
        "concurrency": concurrency,
        "paths": paths,
        "success": successful,
        "failed": (
            len(results)
            - successful
        ),
        "success_rate_pct": round(
            successful
            * 100
            / max(
                len(results),
                1,
            ),
            2,
        ),
        "elapsed_seconds": round(
            elapsed,
            3,
        ),
        "requests_per_second": round(
            len(results)
            / max(
                elapsed,
                0.000001,
            ),
            2,
        ),
        "latency_ms": {
            "p50": round(
                percentile(
                    latencies,
                    50,
                ),
                2,
            ),
            "p95": round(
                percentile(
                    latencies,
                    95,
                ),
                2,
            ),
            "p99": round(
                percentile(
                    latencies,
                    99,
                ),
                2,
            ),
            "max": round(
                max(latencies)
                if latencies
                else 0,
                2,
            ),
        },
        "status_counts": statuses,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Spyon local HTTP load test"
        )
    )

    parser.add_argument(
        "--base-url",
        default=(
            "http://127.0.0.1:8765"
        ),
    )

    parser.add_argument(
        "--requests",
        type=int,
        default=500,
    )

    parser.add_argument(
        "--concurrency",
        type=int,
        default=20,
    )

    parser.add_argument(
        "--paths",
        nargs="+",
        default=[
            "/health",
            "/ready",
            "/",
        ],
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=10,
    )

    parser.add_argument(
        "--min-success-rate",
        type=float,
        default=99,
    )

    parser.add_argument(
        "--max-p95-ms",
        type=float,
        default=1500,
    )

    args = parser.parse_args()

    result = run_load(
        args.base_url,
        args.requests,
        args.concurrency,
        args.paths,
        args.timeout,
    )

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )

    if (
        result["success_rate_pct"]
        < args.min_success_rate
    ):
        return 2

    if (
        result["latency_ms"]["p95"]
        > args.max_p95_ms
    ):
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
