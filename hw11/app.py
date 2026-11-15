import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
from pydantic import BaseModel, ConfigDict, Field, StrictStr

# Keep the previous API's validation and backend interface without importing model weights.
sys.path.insert(0, str(Path(__file__).resolve().parent / "previous"))
if not (Path(__file__).resolve().parent / "previous").exists():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                         "hw8-9-10/mlops26-hw8-9-10/hw8910-broken"))
from backends import StubBackend
from service import GenerateRequest

log = logging.getLogger("uvicorn.error")
BUCKETS = (.025, .05, .1, .2, .4, .8, 1.5, 3, 6, 12, 30, 60)
RUNNING = Gauge("inference_running", "Active backend requests")
WAITING = Gauge("inference_waiting", "Admitted requests waiting for capacity")
READY = Gauge("inference_ready", "Loaded, warm and accepting requests")
EVENTS = Counter("inference_requests", "Request outcomes", ["outcome"])
TTFC = Histogram("inference_ttfc_seconds", "Admission to first chunk", buckets=BUCKETS)
FULL = Histogram("inference_duration_seconds", "Admission to completed stream", buckets=BUCKETS)
QUEUE = Histogram("inference_queue_seconds", "Admission to backend execution", buckets=BUCKETS)
CACHE = Counter("inference_cache", "Simulated prefix cache lookups", ["result"])


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: StrictStr = Field(pattern="^(user|assistant)$")
    content: StrictStr = Field(min_length=1, max_length=4096)


class StreamRequest(GenerateRequest):
    history: list[Message] = Field(default_factory=list, max_length=16)
    session_id: StrictStr = Field(default="", max_length=100)


class SimulatedBackend(StubBackend):
    def __init__(self, artifact, expected_sha, expected_version, config):
        raw = Path(artifact).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_sha:
            raise ValueError("Artifact SHA256 mismatch")
        model = json.loads(raw)
        if model["model_id"] != expected_version or model["backend_mode"] != "simulated":
            raise ValueError("Artifact version or backend mode mismatch")
        super().__init__({"model_id": model["model_id"], "runtime_sha256": expected_sha})
        self.cache = OrderedDict()
        self.config = config

    def cost(self, prefix):
        key = hashlib.sha256(prefix.encode()).hexdigest()
        hit = key in self.cache
        CACHE.labels("hit" if hit else "miss").inc()
        self.cache[key] = None
        self.cache.move_to_end(key)
        while len(self.cache) > self.config["cache_size"]:
            self.cache.popitem(last=False)
        return self.config["hit_iterations" if hit else "miss_iterations"]

    @staticmethod
    def compute(iterations):
        # PBKDF2 releases the GIL, keeping probes responsive during CPU-bound inference.
        hashlib.pbkdf2_hmac("sha256", b"simulated-inference", b"hw11", iterations)


def environment():
    return {
        "warmup_seconds": float(os.getenv("WARMUP_SECONDS", "20")),
        "broken_probes": os.getenv("BROKEN_PROBES", "false") == "true",
        "fail_warmup": os.getenv("FAIL_WARMUP", "false") == "true",
        "concurrency": int(os.getenv("CONCURRENCY", "1")),
        "queue_size": int(os.getenv("QUEUE_SIZE", "12")),
        "queue_timeout": float(os.getenv("QUEUE_TIMEOUT", "15")),
        "cache_size": int(os.getenv("CACHE_SIZE", "8")),
        "hit_iterations": int(os.getenv("HIT_ITERATIONS", "100000")),
        "miss_iterations": int(os.getenv("MISS_ITERATIONS", "900000")),
        "chunk_iterations": int(os.getenv("CHUNK_ITERATIONS", "50000")),
        "chunk_delay": float(os.getenv("CHUNK_DELAY", ".08")),
        "pod": os.getenv("POD_NAME", "local"),
        "artifact": os.getenv("ARTIFACT_PATH", "artifacts/A.json"),
        "sha": os.getenv("ARTIFACT_SHA", ""),
        "version": os.getenv("MODEL_VERSION", "A"),
    }


def create_app(config=None):
    cfg = environment() if config is None else config
    state = {"ready": False, "draining": False, "backend": None, "error": None,
             "running": 0, "waiting": 0}
    semaphore = asyncio.Semaphore(cfg["concurrency"])

    def identity(request_id):
        backend = state["backend"]
        return {"request_id": request_id, "pod": cfg["pod"], "backend_mode": "simulated",
                "model_id": backend.loaded_id if backend else None,
                "artifact_sha256": backend.runtime_sha256 if backend else None}

    def event(request_id, outcome, **fields):
        log.info(json.dumps({**identity(request_id), "event": outcome,
                             "wall_time": time.time(), "backend_ready": state["ready"],
                             **fields}, ensure_ascii=False))

    @asynccontextmanager
    async def lifespan(app):
        READY.set(0)

        async def load():
            try:
                state["backend"] = SimulatedBackend(cfg["artifact"], cfg["sha"], cfg["version"], cfg)
                await asyncio.sleep(cfg["warmup_seconds"])
                if cfg["fail_warmup"]:
                    raise ValueError("Intentional warmup failure")
                await asyncio.to_thread(state["backend"].warmup)
                state["ready"] = True
                READY.set(1)
                event("", "warmup_finished")
            except Exception as exc:
                state["error"] = str(exc)
                event("", "load_failed", error=str(exc))

        task = asyncio.create_task(load())
        yield
        state["draining"] = True
        READY.set(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(title="HW11 support API", lifespan=lifespan)
    app.state.runtime = state
    app.mount("/metrics", make_asgi_app())

    @app.get("/health")
    async def health():
        return {"alive": True}

    @app.get("/startup")
    @app.get("/ready")
    async def ready():
        ok = not state["draining"] and (state["ready"] or cfg["broken_probes"])
        return JSONResponse({**identity(""), "ready": ok, "backend_ready": state["ready"],
                             "error": state["error"]}, status_code=200 if ok else 503)

    @app.post("/drain")
    async def drain():
        state["draining"] = True
        READY.set(0)
        return {"draining": True}

    @app.get("/debug")
    async def debug():
        return {**identity(""), **{k: v for k, v in state.items() if k != "backend"},
                "config": cfg}

    async def admit(body, request):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        if len(request_id) > 100:
            return JSONResponse({"error": "Request ID too long"}, status_code=400)
        started = time.monotonic()
        event(request_id, "received", session_id=body.session_id,
              client_ip=request.client.host if request.client else None)
        if not state["ready"]:
            EVENTS.labels("unready").inc()
            event(request_id, "unready")
            return JSONResponse({**identity(request_id), "error": "Backend not ready"}, status_code=503)
        if state["running"] + state["waiting"] >= cfg["concurrency"] + cfg["queue_size"]:
            EVENTS.labels("rejected").inc()
            event(request_id, "rejected")
            return JSONResponse({**identity(request_id), "error": "Queue full"}, status_code=429)
        state["waiting"] += 1
        WAITING.inc()
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=cfg["queue_timeout"])
        except asyncio.TimeoutError:
            EVENTS.labels("queue_timeout").inc()
            event(request_id, "queue_timeout")
            return JSONResponse({**identity(request_id), "error": "Queue timeout"}, status_code=429)
        finally:
            state["waiting"] -= 1
            WAITING.dec()
        state["running"] += 1
        RUNNING.inc()
        QUEUE.observe(time.monotonic() - started)
        return request_id, started

    @app.post("/generate")
    async def generate(body: StreamRequest, request: Request):
        return await respond(body, request, False)

    @app.post("/stream")
    async def stream(body: StreamRequest, request: Request):
        return await respond(body, request, True)

    async def respond(body, request, streaming):
        admission = await admit(body, request)
        if isinstance(admission, JSONResponse):
            return admission
        request_id, started = admission
        backend = state["backend"]
        backend.calls += 1
        context = "\n".join(f"{m.role}: {m.content}" for m in body.history)
        prefix = context + "\n" + body.prompt
        iterations = backend.cost(prefix)
        completed = False

        async def pieces():
            nonlocal completed
            first = 0.0
            try:
                await asyncio.to_thread(backend.compute, iterations)
                for index in range(body.max_new_tokens):
                    await asyncio.to_thread(backend.compute, cfg["chunk_iterations"])
                    await asyncio.sleep(cfg["chunk_delay"])
                    if await request.is_disconnected():
                        return
                    if index == 0:
                        first = time.monotonic() - started
                    yield {**identity(request_id), "type": "chunk", "index": index,
                           "text": f"SIMULATED {backend.loaded_id} [{index}] {prefix}"}
                elapsed = time.monotonic() - started
                TTFC.observe(first)
                FULL.observe(elapsed)
                EVENTS.labels("completed").inc()
                completed = True
                event(request_id, "completed", duration=elapsed, ttfc=first,
                      chunks=body.max_new_tokens)
                yield {**identity(request_id), "type": "done", "chunks": body.max_new_tokens,
                       "duration_seconds": elapsed, "ttfc_seconds": first}
            finally:
                if not completed:
                    EVENTS.labels("aborted").inc()
                    event(request_id, "aborted")
                state["running"] -= 1
                RUNNING.dec()
                semaphore.release()

        if streaming:
            async def ndjson():
                async for piece in pieces():
                    yield json.dumps(piece, ensure_ascii=False) + "\n"
            return StreamingResponse(ndjson(), media_type="application/x-ndjson",
                                     headers={"X-Request-ID": request_id, "X-Pod": cfg["pod"],
                                              "X-Model-Version": backend.loaded_id})
        result = []
        async for piece in pieces():
            result.append(piece)
        return {**identity(request_id), "answer": "".join(p["text"] for p in result if p["type"] == "chunk"),
                "output_chunks": body.max_new_tokens, "duration_seconds": time.monotonic() - started}

    return app
