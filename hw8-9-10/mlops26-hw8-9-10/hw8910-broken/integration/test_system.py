import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT = Path(
    os.environ.get("PROJECT_UNDER_TEST", Path(__file__).resolve().parents[1])
)


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "workspace"
        self.command("init")

    def command(self, *args, success=True):
        result = subprocess.run(
            [
                sys.executable,
                str(PROJECT / "lab.py"),
                "--workspace",
                str(self.root),
                *args,
            ],
            cwd=PROJECT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(
            self.url + path, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def server(self, warmup=0.0):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        log = tempfile.TemporaryFile()
        self.addCleanup(log.close)
        process = subprocess.Popen(
            [
                sys.executable,
                str(PROJECT / "service.py"),
                "--workspace",
                str(self.root),
                "--port",
                str(port),
                "--warmup-seconds",
                str(warmup),
            ],
            cwd=PROJECT,
            stdout=log,
            stderr=log,
        )

        def stop():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)

        self.addCleanup(stop)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if self.request("/health")[0] == 200:
                    return process
            except (URLError, OSError):
                pass
            if process.poll() is not None:
                log.seek(0)
                self.fail(log.read().decode())
            time.sleep(0.03)
        self.fail("HTTP server did not start")

    def wait_ready(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.request("/ready")[0] == 200:
                return
            time.sleep(0.03)
        self.fail("Server did not become ready")

    def test_cache_recovery_and_invalidation_across_processes(self):
        failed = self.command("run", "--fail-after-prepare", success=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("PREPARED", failed.stdout)
        self.assertIn("CACHE_HIT", self.command("run").stdout)
        (self.root / "input.txt").write_text("Changed content at the same path\n")
        changed = self.command("run").stdout
        self.assertIn("PREPARED", changed)
        self.assertNotIn("CACHE_HIT", changed)
        for key, value in [
            ("tokenizer_revision", "r2"),
            ("template_sha256", "f" * 64),
            ("max_length", 256),
            ("code_revision", "prepare-v2"),
        ]:
            config = json.loads((self.root / "config.json").read_text())
            config[key] = value
            (self.root / "config.json").write_text(json.dumps(config))
            self.assertIn("PREPARED", self.command("run").stdout)

    def test_incomplete_foreign_and_rejected_reports_preserve_pointer(self):
        for name in ["report-incomplete.json", "report-A.json", "report-reject.json"]:
            with self.subTest(report=name):
                self.root = Path(self.tmp.name) / name.removesuffix(".json")
                self.command("init")
                before = (self.root / "accepted.json").read_bytes()
                result = self.command("run", "--report", name, success=False)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual((self.root / "accepted.json").read_bytes(), before)

    def test_repeat_release_has_one_event_across_processes(self):
        self.command("run")
        before = (self.root / "accepted.json").read_bytes()
        self.command("run")
        self.assertEqual((self.root / "accepted.json").read_bytes(), before)
        events = (self.root / "publications.jsonl").read_text().splitlines()
        self.assertEqual(len(events), 1, events)

    def test_http_readiness_blocks_generation_until_warmup(self):
        self.server(warmup=1.5)
        self.assertEqual(self.request("/health")[0], 200)
        self.assertEqual(self.request("/ready")[0], 503)
        before = self.request("/debug")[1]["calls"]
        self.assertEqual(self.request("/generate", {"prompt": "ticket"})[0], 503)
        self.assertEqual(self.request("/debug")[1]["calls"], before)
        self.wait_ready()
        self.assertTrue(self.request("/debug")[1]["warmup_finished"])
        self.assertEqual(self.request("/generate", {"prompt": "ticket"})[0], 200)

    def test_http_loaded_version_restart_and_rollback(self):
        process = self.server()
        self.wait_ready()
        self.command("run")
        response = self.request("/generate", {"prompt": "ticket"})[1]
        print("STUB_LIFECYCLE before_restart", json.dumps(response))
        self.assertEqual(response["model_id"], "fixture-A")
        self.assertIn("fixture-A", response["answer"])
        process.terminate()
        process.wait(timeout=15)
        process = self.server()
        self.wait_ready()
        response = self.request("/generate", {"prompt": "ticket"})[1]
        print("STUB_LIFECYCLE after_restart", json.dumps(response))
        self.assertEqual(response["model_id"], "fixture-B")
        self.assertIn("fixture-B", response["answer"])
        # A is an already accepted fixture; no new quality decision is invented here.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "rollback_release", PROJECT / "release.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.publish(
            json.loads((self.root / "manifest-A.json").read_text()),
            json.loads((self.root / "report-A.json").read_text()),
            self.root / "accepted.json",
        )
        process.terminate()
        process.wait(timeout=15)
        self.server()
        self.wait_ready()
        response = self.request("/generate", {"prompt": "ticket"})[1]
        print("STUB_LIFECYCLE after_rollback", json.dumps(response))
        self.assertEqual(response["model_id"], "fixture-A")

    def test_http_invalid_requests_never_call_backend(self):
        self.server()
        self.wait_ready()
        before = self.request("/debug")[1]["calls"]
        invalid = [
            {},
            {"prompt": ""},
            {"prompt": " "},
            {"prompt": 42},
            [],
            {"prompt": "x", "max_new_tokens": 0},
            {"prompt": "x", "max_new_tokens": 100000},
            {"prompt": "x", "max_new_tokens": "2"},
            {"prompt": "x", "max_new_tokens": True},
            {"prompt": "x" * 17000},
        ]
        for payload in invalid:
            with self.subTest(payload=str(payload)[:100]):
                before = self.request("/debug")[1]["calls"]
                self.assertIn(self.request("/generate", payload)[0], (400, 413, 422))
                self.assertEqual(self.request("/debug")[1]["calls"], before)
        before = self.request("/debug")[1]["calls"]
        self.assertEqual(
            self.request("/generate", {"prompt": "ticket", "max_new_tokens": 32})[0],
            200,
        )
        self.assertEqual(self.request("/debug")[1]["calls"], before + 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
