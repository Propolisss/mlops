import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import statistics
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import release


def request(url, path, payload=None):
    data = None if payload is None else release.canonical(payload)
    message = Request(
        url + path, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urlopen(message, timeout=120) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def smoke(url, output):
    if request(url, "/health")[0] != 200 or request(url, "/ready")[0] != 200:
        raise AssertionError("API is not ready")
    before = request(url, "/debug")[1]["calls"]
    invalid = []
    for limit in (0, 100000, "32", True, 16):
        status, response = request(
            url, "/generate", {"prompt": "ticket", "max_new_tokens": limit}
        )
        invalid.append({"limit": limit, "status": status, "response": response})
        if status != 422:
            raise AssertionError("Invalid/unevaluated generation limit was accepted")
    after = request(url, "/debug")[1]["calls"]
    if before != after:
        raise AssertionError("Invalid requests invoked the model")
    protocol = json.loads((PROJECT / ".real/protocol.json").read_text())
    status, response = request(
        url,
        "/generate",
        {"prompt": protocol["golden"][0]["input"], "max_new_tokens": 32},
    )
    if status != 200 or response["answer"].startswith("STUB ONLY"):
        raise AssertionError("Missing real model response")
    expected = json.loads((PROJECT / ".real/accepted.json").read_text())
    if (
        response["model_id"] != expected["model_id"]
        or response["runtime_sha256"] != expected["runtime_sha256"]
    ):
        raise AssertionError("Served version differs from accepted manifest")
    release.write_json(
        output / "api-smoke.json",
        {
            "invalid_requests": invalid,
            "before_calls": before,
            "after_invalid_calls": after,
            "real_response": response,
            "status": status,
        },
    )


def benchmark(url, output):
    protocol = json.loads((PROJECT / ".real/protocol.json").read_text())
    rows = protocol["golden"][:8]
    summaries = []
    attempts = []
    for concurrency in (1, 2):

        def attempt(row):
            started = time.perf_counter()
            payload = {"prompt": row["input"], "max_new_tokens": 32}
            status, response, error = None, {}, None
            try:
                status, response = request(url, "/generate", payload)
                if status != 200:
                    error = str(response)
            except (URLError, OSError, TimeoutError) as exc:
                error = f"{type(exc).__name__}: {exc}"
            return {
                "id": row["id"],
                "concurrency": concurrency,
                "status": status,
                "error": error,
                "payload_sha256": release.fingerprint(payload),
                "max_new_tokens": 32,
                "client_seconds": time.perf_counter() - started,
                **response,
            }

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            batch = list(executor.map(attempt, rows))
        elapsed = time.perf_counter() - started
        attempts.extend(batch)
        successes = [
            item for item in batch if item["status"] == 200 and item["error"] is None
        ]
        errors = [item for item in batch if item not in successes]
        summaries.append(
            {
                "concurrency": concurrency,
                "n": len(batch),
                "successes": len(successes),
                "errors": len(errors),
                "batch_seconds": elapsed,
                "successful_requests_per_second": len(successes) / elapsed,
                "mean_success_seconds": statistics.mean(
                    i["client_seconds"] for i in successes
                )
                if successes
                else None,
                "max_success_seconds": max(
                    (i["client_seconds"] for i in successes), default=None
                ),
                "mean_error_seconds": statistics.mean(
                    i["client_seconds"] for i in errors
                )
                if errors
                else None,
                "max_error_seconds": max(
                    (i["client_seconds"] for i in errors), default=None
                ),
            }
        )
    release.atomic_bytes(
        output / "requests.jsonl",
        b"".join(release.canonical(item) + b"\n" for item in attempts),
    )
    release.write_json(output / "load-summary.json", summaries)
    print(json.dumps(summaries, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["smoke", "load"])
    parser.add_argument("--url", default="http://127.0.0.1:8091")
    args = parser.parse_args()
    output = PROJECT / "docs/evidence"
    if args.command == "smoke":
        smoke(args.url, output)
    else:
        benchmark(args.url, output)


if __name__ == "__main__":
    main()
