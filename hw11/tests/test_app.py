import hashlib
import json
from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import SimulatedBackend, StreamRequest, create_app, environment
from load import schedule


@pytest.fixture
def config(tmp_path):
    artifact = tmp_path / "model.json"
    artifact.write_text('{"model_id":"A","backend_mode":"simulated"}')
    return {**environment(), "artifact": str(artifact),
            "sha": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "warmup_seconds": .1, "hit_iterations": 1, "miss_iterations": 2,
            "chunk_iterations": 1, "chunk_delay": .001}


def wait_ready(client):
    for _ in range(100):
        if client.get("/ready").status_code == 200:
            return
        time.sleep(.01)
    raise AssertionError("Backend did not warm up")


def test_startup_gates_warmup(config):
    with TestClient(create_app(config)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/startup").status_code == 503
        assert client.get("/ready").status_code == 503
        assert client.post("/stream", json={"prompt": "x"}).status_code == 503
        wait_ready(client)
        assert client.get("/startup").status_code == 200


def test_original_probe_defect_is_reproducible(config):
    with TestClient(create_app({**config, "broken_probes": True})) as client:
        assert client.get("/ready").status_code == 200
        assert client.get("/ready").json()["backend_ready"] is False
        assert client.post("/stream", json={"prompt": "x"}).status_code == 503


def test_verified_version_and_stream(config):
    with TestClient(create_app(config)) as client:
        wait_ready(client)
        response = client.post("/stream", json={"prompt": "billing", "max_new_tokens": 3},
                               headers={"x-request-id": "check-stream"})
        rows = [json.loads(line) for line in response.text.splitlines()]
        assert response.status_code == 200
        assert [r["type"] for r in rows] == ["chunk", "chunk", "chunk", "done"]
        assert all(r["model_id"] == "A" and r["backend_mode"] == "simulated" for r in rows)
        assert all(r["request_id"] == "check-stream" for r in rows)
        assert rows[-1]["chunks"] == 3


def test_two_dialogs_are_isolated(config):
    with TestClient(create_app(config)) as client:
        wait_ready(client)
        for own, other in [("SECRET-ALPHA", "SECRET-BETA"), ("SECRET-BETA", "SECRET-ALPHA")]:
            response = client.post("/generate", json={
                "prompt": "continue", "max_new_tokens": 1,
                "history": [{"role": "user", "content": own}]})
            assert own in response.json()["answer"]
            assert other not in response.json()["answer"]


def test_artifact_hash_and_version_fail_closed(config):
    with pytest.raises(ValueError, match="SHA256"):
        SimulatedBackend(config["artifact"], "0" * 64, "A", config)
    with pytest.raises(ValueError, match="version"):
        SimulatedBackend(config["artifact"], config["sha"], "B", config)


def test_bounded_lru_cache(config):
    backend = SimulatedBackend(config["artifact"], config["sha"], "A",
                               {**config, "cache_size": 1})
    assert backend.cost("a") == 2
    assert backend.cost("a") == 1
    assert backend.cost("b") == 2
    assert backend.cost("a") == 2
    assert len(backend.cache) == 1


def test_drain_and_legacy_validation(config):
    with TestClient(create_app(config)) as client:
        wait_ready(client)
        assert client.post("/stream", json={"prompt": "x", "max_new_tokens": 33}).status_code == 422
        assert client.post("/drain").status_code == 200
        assert client.get("/ready").status_code == 503
        # Endpoint propagation can lag readiness; preStop still serves that in-flight traffic.
        assert client.post("/stream", json={"prompt": "x", "max_new_tokens": 1}).status_code == 200
        assert client.get("/health").status_code == 200


def test_schedule_is_open_loop_reproducible_and_skewed():
    a = schedule(120, 4, "skew")
    assert a == schedule(120, 4, "skew")
    assert len(a) == 480
    assert sum(e["client"] == 0 for e in a) == 384
    assert {e["client"] for e in a} == {0, 1, 2, 3}
    assert a[-1]["offset"] == 119.75
