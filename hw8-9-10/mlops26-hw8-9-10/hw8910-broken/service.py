import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from backends import StubBackend, TransformersBackend
import release


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: StrictStr = Field(min_length=1, max_length=16384)
    max_new_tokens: StrictInt = Field(default=32, ge=1, le=32)

    @field_validator("prompt")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Empty prompt")
        return value


def create_app(root, warmup_seconds=0, backend_factory=None, debug=False):
    root = Path(root)
    state = {"ready": False, "backend": None, "error": None}
    lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        async def load():
            try:
                manifest = json.loads((root / "accepted.json").read_text())
                if manifest["runtime"]["base_revision"] != "fixture-only":
                    from real import verify_served_model

                    await asyncio.to_thread(verify_served_model, root, manifest)
                artifact = await asyncio.to_thread(release.verify_files, manifest, root)
                if backend_factory is None:
                    factory = (
                        StubBackend
                        if manifest["runtime"]["base_revision"] == "fixture-only"
                        else TransformersBackend
                    )
                else:
                    factory = backend_factory
                backend = await asyncio.to_thread(factory, manifest, artifact)
                state["backend"] = backend
                state["manifest"] = manifest
                await asyncio.sleep(warmup_seconds)
                await asyncio.to_thread(backend.warmup)
                state["ready"] = True
            except Exception as exc:
                state["error"] = f"{type(exc).__name__}: {exc}"

        loading = asyncio.create_task(load())
        yield
        await loading
        state["ready"] = False

    app = FastAPI(title="Support ticket model API", lifespan=lifespan)

    @app.middleware("http")
    async def body_limit(request: Request, call_next):
        # Bound the stream as well as Content-Length; chunked requests have no length header.
        if request.method == "POST":
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 65536:
                    return JSONResponse({"detail": "Body too large"}, status_code=413)
            request._body = bytes(body)
        return await call_next(request)

    @app.get("/health")
    async def health():
        return {"alive": True}

    @app.get("/ready")
    async def ready():
        return JSONResponse(
            {"ready": state["ready"]}, status_code=200 if state["ready"] else 503
        )

    if debug:

        @app.get("/debug")
        async def diagnostics():
            backend = state["backend"]
            return {
                "loaded_id": backend.loaded_id if backend else None,
                "calls": backend.calls if backend else 0,
                "warmup_finished": state["ready"],
                "load_error": state["error"],
            }

    @app.post("/generate")
    async def generate(body: GenerateRequest):
        started = time.perf_counter()
        if not state["ready"]:
            raise HTTPException(503, "Model is not ready")
        manifest = state["manifest"]
        limit = manifest["runtime"]["generation"]["max_new_tokens"]
        # Only the evaluated limit inherits the evaluation decision for a real model.
        if (
            manifest["runtime"]["base_revision"] != "fixture-only"
            and body.max_new_tokens != limit
        ):
            raise HTTPException(422, f"This runtime requires max_new_tokens={limit}")
        try:
            await asyncio.to_thread(state["backend"].validate_prompt, body.prompt)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        async with lock:
            generation_start = time.perf_counter()
            try:
                result = await asyncio.to_thread(
                    state["backend"].generate, body.prompt, body.max_new_tokens
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            except Exception as exc:
                raise HTTPException(500, "Generation failed") from exc
            finished = time.perf_counter()
        return {
            **result,
            "model_id": state["backend"].loaded_id,
            "runtime_sha256": state["backend"].runtime_sha256,
            "request_id": str(uuid.uuid4()),
            "queue_seconds": generation_start - started,
            "generation_seconds": finished - generation_start,
            "total_seconds": finished - started,
        }

    return app


def main():
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(".lab"))
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--warmup-seconds", type=float, default=0)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    fixture = (
        json.loads((args.workspace / "accepted.json").read_text())["runtime"][
            "base_revision"
        ]
        == "fixture-only"
    )
    uvicorn.run(
        create_app(args.workspace, args.warmup_seconds, debug=args.debug or fixture),
        host="127.0.0.1",
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
