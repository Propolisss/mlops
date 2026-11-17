import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from load import request_one


@pytest.fixture
def server(tmp_path):
    artifact = tmp_path / "model.json"
    artifact.write_text('{"model_id":"A","backend_mode":"simulated"}')
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "ARTIFACT_PATH": str(artifact),
           "ARTIFACT_SHA": hashlib.sha256(artifact.read_bytes()).hexdigest(),
           "WARMUP_SECONDS": "0", "CHUNK_DELAY": ".06", "QUEUE_SIZE": "1",
           "HIT_ITERATIONS": "1", "MISS_ITERATIONS": "2", "CHUNK_ITERATIONS": "1"}
    root = Path(__file__).resolve().parents[1]
    with (tmp_path / "server.log").open("w") as log:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:create_app", "--factory",
                                    "--host", "127.0.0.1", "--port", str(port)],
                                   env=env, cwd=root, stdout=log, stderr=log)
        url = f"http://127.0.0.1:{port}"
        try:
            for _ in range(200):
                try:
                    if httpx.get(url + "/ready", timeout=.2).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.02)
            else:
                raise AssertionError((tmp_path / "server.log").read_text())
            yield url
        finally:
            process.terminate()
            process.wait(timeout=10)


async def test_queue_refusals_are_separate_attempts(server):
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=32)) as client:
        mono, wall = time.monotonic(), time.time()
        rows = await asyncio.gather(*[
            request_one(client, server, {"index": i, "offset": 0, "client": 0,
                                        "prompt": "refusal", "max_new_tokens": 4},
                        mono, wall, 5, "test-refusal") for i in range(12)])
        assert len({r["request_id"] for r in rows}) == 12
        assert sum(r["outcome"] == "rejected" for r in rows) >= 10
        assert sum(r["outcome"] == "completed" for r in rows) >= 1
        assert all(r["outcome"] in {"rejected", "completed"} for r in rows)
        metrics = (await client.get(server + "/metrics/")).text
        assert 'inference_requests_total{outcome="rejected"}' in metrics


async def test_disconnected_stream_is_counted_and_capacity_restored(server):
    async with httpx.AsyncClient() as client:
        async with client.stream("POST", server + "/stream",
                                 json={"prompt": "abort", "max_new_tokens": 32}) as response:
            async for line in response.aiter_lines():
                assert json.loads(line)["type"] == "chunk"
                break
        for _ in range(100):
            runtime = (await client.get(server + "/debug")).json()
            if runtime["running"] == 0:
                break
            await asyncio.sleep(.02)
        assert runtime["running"] == runtime["waiting"] == 0
        metrics = (await client.get(server + "/metrics/")).text
        assert 'inference_requests_total{outcome="aborted"} 1.0' in metrics
        response = await client.post(server + "/generate", json={"prompt": "after", "max_new_tokens": 1})
        assert response.status_code == 200
