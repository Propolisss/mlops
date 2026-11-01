import copy
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lab
import release


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "workspace"
        lab.initialize(self.root)
        self.manifest = lab.read(self.root / "manifest-B.json")
        self.report = lab.read(self.root / "report-B.json")

    def test_pointer_journal_crash_recovered_without_duplicate(self):
        with self.assertRaisesRegex(RuntimeError, "after pointer"):
            release.publication(self.root, self.manifest, self.report, True)
        self.assertTrue((self.root / "pending.json").exists())
        for _ in range(2):
            release.publication(self.root, self.manifest, self.report)
        self.assertFalse((self.root / "pending.json").exists())
        events = (self.root / "publications.jsonl").read_text().splitlines()
        self.assertEqual(len(events), 1)
        self.assertEqual(lab.read(self.root / "accepted.json"), self.manifest)

    def test_identity_includes_runtime_and_evaluation(self):
        release.publication(self.root, self.manifest, self.report)
        updated = copy.deepcopy(self.manifest)
        updated["runtime"]["dtype"] = "float16"
        updated["runtime_sha256"] = release.fingerprint(updated["runtime"])
        report = {**self.report, "runtime_sha256": updated["runtime_sha256"]}
        release.publication(self.root, updated, report)
        release.publication(
            self.root, updated, {**report, "evaluation_run_id": "new-eval"}
        )
        events = [
            json.loads(line)
            for line in (self.root / "publications.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len({item["release_id"] for item in events}), 3)

    def test_tampered_artifact_rejected_before_pointer_update(self):
        before = (self.root / "accepted.json").read_bytes()
        (self.root / "model-B.txt").write_text("STUB ONLY corrupted weights")
        with self.assertRaisesRegex(ValueError, "checksum"):
            release.publication(self.root, self.manifest, self.report)
        self.assertEqual((self.root / "accepted.json").read_bytes(), before)

    def test_disk_error_leaves_bytes_and_cleans_temporary_files(self):
        before = (self.root / "accepted.json").read_bytes()
        with patch("os.replace", side_effect=OSError("simulated disk error")):
            with self.assertRaises(OSError):
                release.publish(self.manifest, self.report, self.root / "accepted.json")
        self.assertEqual((self.root / "accepted.json").read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_nan_and_bad_binding_fail_closed(self):
        before = (self.root / "accepted.json").read_bytes()
        for field, value in [
            ("metrics", {"accuracy": float("nan"), "format_rate": 1}),
            ("runtime_sha256", "f" * 64),
            ("passed", "true"),
        ]:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    release.publication(
                        self.root, self.manifest, {**self.report, field: value}
                    )
                self.assertEqual((self.root / "accepted.json").read_bytes(), before)

    def test_prefect_creates_missing_parent_directories_on_clean_start(self):
        home = Path(self.tmp.name) / "new-parent" / "prefect"
        project = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                'import flow; import os; from pathlib import Path; assert Path(os.environ["PREFECT_HOME"]).is_dir()',
            ],
            cwd=self.tmp.name,
            env={**os.environ, "PYTHONPATH": str(project), "PREFECT_HOME": str(home)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
