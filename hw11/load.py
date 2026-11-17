import argparse
import asyncio
import json
from pathlib import Path
import time
import uuid

import httpx


def schedule(duration, rate, scenario):
    count = int(duration * rate)
    entries = []
    for i in range(count):
        if scenario == "skew":
            client = 0 if i % 10 < 8 else 1 + (i // 10 + i % 10) % 3
        else:
            client = i % 4
        entries.append({"index": i, "offset": i / rate, "client": client,
                        "prompt": f"support client={client} prefix={i // 4 % 4}",
                        "max_new_tokens": 4})
    return entries


async def request_one(client, url, entry, start_mono, start_wall, timeout, run):
    planned = start_mono + entry["offset"]
    await asyncio.sleep(max(0, planned - time.monotonic()))
    sent = time.monotonic()
    row = {**entry, "run": run, "request_id": f"{run}-{entry['index']}-{uuid.uuid4().hex[:8]}",
           "planned_wall": start_wall + entry["offset"], "sent_wall": time.time(),
           "planned_monotonic": planned, "sent_monotonic": sent,
           "send_lag_seconds": sent - planned, "status": None, "outcome": "transport_error",
           "pod": None, "model_id": None, "backend_mode": None, "chunks": 0,
           "first_monotonic": None, "completed_monotonic": None,
           "ttfc_seconds": None, "duration_seconds": None, "inter_chunk_seconds": []}
    first = None
    previous = None
    done = False
    try:
        async with client.stream("POST", url + "/stream",
                                 json={k: entry[k] for k in ("prompt", "max_new_tokens")},
                                 headers={"X-Request-ID": row["request_id"]}, timeout=timeout) as response:
            row["status"] = response.status_code
            row["pod"] = response.headers.get("x-pod")
            row["model_id"] = response.headers.get("x-model-version")
            if response.status_code != 200:
                raw = await response.aread()
                try:
                    info = json.loads(raw)
                    row.update({k: info.get(k) for k in ("pod", "model_id", "backend_mode")})
                except (ValueError, TypeError):
                    pass
                row["outcome"] = "rejected" if response.status_code == 429 else "http_error"
            else:
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    piece = json.loads(line)
                    now = time.monotonic()
                    row.update({k: piece[k] for k in ("pod", "model_id", "backend_mode")})
                    if piece["type"] == "chunk":
                        if first is None:
                            first = now
                            row["first_monotonic"] = first
                            row["ttfc_seconds"] = first - sent
                        if previous is not None:
                            row["inter_chunk_seconds"].append(now - previous)
                        previous = now
                        row["chunks"] += 1
                    elif piece["type"] == "done":
                        done = piece["chunks"] == row["chunks"] == entry["max_new_tokens"]
                row["outcome"] = "completed" if done else "aborted"
    except httpx.TimeoutException as exc:
        row["outcome"] = "timeout"
        row["error"] = type(exc).__name__
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        row["outcome"] = "aborted" if row["status"] == 200 else "transport_error"
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["completed_monotonic"] = time.monotonic()
    row["duration_seconds"] = row["completed_monotonic"] - sent
    return row


async def run(args):
    entries = schedule(args.duration, args.rate, args.scenario)
    if args.client >= 0:
        entries = [e for e in entries if e["client"] == args.client]
    wall = args.start or time.time() + 1
    mono = time.monotonic() + wall - time.time()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".schedule.json").write_text(json.dumps({
        "start_wall": wall, "duration": args.duration, "rate": args.rate,
        "scenario": args.scenario, "client": args.client, "entries": entries}, indent=2))
    limits = httpx.Limits(max_connections=256, max_keepalive_connections=0 if not args.keepalive else 32)
    async with httpx.AsyncClient(limits=limits, trust_env=False) as client:
        tasks = [asyncio.create_task(request_one(client, args.url, e, mono, wall,
                                                args.timeout, args.run)) for e in entries]
        with output.open("w") as stream:
            for future in asyncio.as_completed(tasks):
                row = await future
                stream.write(json.dumps(row) + "\n")
                stream.flush()
    print(json.dumps({"output": str(output), "planned": len(entries)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", default="/tmp/requests.jsonl")
    parser.add_argument("--run", default="manual")
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--rate", type=float, default=4)
    parser.add_argument("--scenario", choices=["uniform", "skew"], default="uniform")
    parser.add_argument("--client", type=int, default=-1)
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--timeout", type=float, default=25)
    parser.add_argument("--keepalive", action="store_true")
    asyncio.run(run(parser.parse_args()))
