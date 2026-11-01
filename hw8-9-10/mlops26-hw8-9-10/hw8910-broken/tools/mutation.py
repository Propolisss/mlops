import os
from pathlib import Path
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import release

source = (PROJECT / "release.py").read_text()
old = 'if report.get("status") != "complete" or report.get("passed") is not True:'
if source.count(old) != 1:
    raise AssertionError("Mutation site changed")
before = release.digest(source.encode())
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    (root / "release.py").write_text(
        source.replace(old, 'if not report.get("passed"):')
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(PROJECT / "tests"),
            "-v",
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    log = result.stdout + result.stderr + f"\nEXIT_CODE={result.returncode}\n"
    (PROJECT / "docs/evidence/mutation-red.log").write_text(log)
    if result.returncode == 0 or "AssertionError: ValueError not raised" not in log:
        raise AssertionError("Broken admission check was not caught by tests")
after = release.digest((PROJECT / "release.py").read_bytes())
if before != after:
    raise AssertionError("Working source changed")
release.write_json(
    PROJECT / "docs/evidence/mutation-summary.json",
    {
        "mutation": "Accept truthy passed without complete status in an isolated code copy",
        "exit_code": result.returncode,
        "source_before_sha256": before,
        "source_after_sha256": after,
        "restored": True,
        "working_tree_was_not_modified": True,
    },
)
print("PASS: intentional mutation caught, working source unchanged")
