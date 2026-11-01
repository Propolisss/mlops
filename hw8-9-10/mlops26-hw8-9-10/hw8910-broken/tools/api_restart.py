import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from tools.benchmark import request
import release

protocol = json.loads((PROJECT / ".real/protocol.json").read_text())
observations = []
for index in range(2):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with (PROJECT / f"docs/evidence/api-restart-{index}.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "service.py",
                "--workspace",
                ".real",
                "--port",
                str(port),
                "--warmup-seconds",
                "2",
                "--debug",
            ],
            cwd=PROJECT,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 60
            saw_not_ready = False
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("API exited during loading")
                try:
                    status, _ = request(url, "/ready")
                    if status == 503:
                        saw_not_ready = True
                    if status == 200:
                        break
                except (URLError, OSError):
                    pass
                time.sleep(0.05)
            else:
                raise TimeoutError("Model did not become ready")
            status, response = request(
                url,
                "/generate",
                {"prompt": protocol["golden"][0]["input"], "max_new_tokens": 32},
            )
            if status != 200 or not saw_not_ready:
                raise AssertionError("Missing real startup/readiness evidence")
            observations.append(
                {
                    "process": index + 1,
                    "saw_ready_503_before_warmup": saw_not_ready,
                    "status": status,
                    "response": response,
                }
            )
        finally:
            process.terminate()
            process.wait(timeout=20)
if observations[0]["response"]["model_id"] != observations[1]["response"]["model_id"]:
    raise AssertionError("Restart loaded a different baseline")
if observations[0]["response"]["answer"] != observations[1]["response"]["answer"]:
    raise AssertionError("Greedy generation changed across restarts")
release.write_json(PROJECT / "docs/evidence/api-restart.json", observations)
print("PASS: real baseline loaded and warmed up in two independent processes")
