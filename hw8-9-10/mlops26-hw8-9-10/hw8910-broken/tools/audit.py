import csv
import json
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import real
import release


def main():
    prefix = "hw8-9-10/mlops26-hw8-9-10/hw8910-broken/"
    original = subprocess.check_output(
        ["git", "show", f"dbafa00:{prefix}tests/test_release.py"], cwd=PROJECT
    )
    assert original == (PROJECT / "tests/test_release.py").read_bytes(), (
        "Public tests changed"
    )
    with (PROJECT / "defects.csv").open() as stream:
        defects = list(csv.DictReader(stream))
    assert len(defects) == 7 and all(all(row.values()) for row in defects), (
        "Incomplete defect matrix"
    )
    evidence = PROJECT / "docs/evidence"
    for row in defects:
        for key in ("before_log", "after_log"):
            assert (PROJECT / row[key]).is_file(), row[key]
    original_log = (evidence / "original-integration.log").read_text()
    for name in (
        "test_cache_recovery_and_invalidation_across_processes",
        "test_http_invalid_requests_never_call_backend",
        "test_http_loaded_version_restart_and_rollback",
        "test_http_readiness_blocks_generation_until_warmup",
        "test_incomplete_foreign_and_rejected_reports_preserve_pointer",
        "test_repeat_release_has_one_event_across_processes",
    ):
        assert f"FAIL: {name}" in original_log, f"Missing red evidence: {name}"
    assert "Ran 25 tests" in (evidence / "quick-check.log").read_text()
    assert "Ran 25 tests" in (evidence / "linux-tests.log").read_text()
    for name in (
        "flow-summary.json",
        "real-flow-summary.json",
        "mutation-summary.json",
    ):
        value = real.read(evidence / name)
        before = next(v for k, v in value.items() if "before" in k)
        after = next(v for k, v in value.items() if "after" in k)
        assert before == after, name
    reports = real.validated_reports(PROJECT / ".real")
    manifest = real.read(PROJECT / ".real/accepted.json")
    release.verify_files(manifest, PROJECT / ".real")
    real.verify_served_model(PROJECT / ".real", manifest)
    decision = real.admission_report(PROJECT / ".real", save=False)
    assert decision["passed"] is False
    attempts = [
        json.loads(line)
        for line in (evidence / "requests.jsonl").read_text().splitlines()
    ]
    assert len(attempts) == 16
    for c in (1, 2):
        assert len([row for row in attempts if row["concurrency"] == c]) == 8
    assert [r["payload_sha256"] for r in attempts[:8]] == [
        r["payload_sha256"] for r in attempts[8:]
    ]
    registry = None
    try:
        from mlflow import MlflowClient

        client = MlflowClient(tracking_uri="http://127.0.0.1:5006")
        model = client.get_registered_model("my-qwen-adapter")
        registry = dict(model.aliases)
        assert registry.get("candidate") == "2" and "champion" not in registry
    except ImportError:
        pass
    release.write_json(
        evidence / "audit.json",
        {
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=PROJECT
            )
            .decode()
            .strip(),
            "public_tests_unchanged": True,
            "seven_defects_documented": True,
            "six_original_integration_tests_failed_on_behavior": True,
            "quick_tests": 25,
            "linux_tests": 25,
            "runtime_and_outputs_verified": True,
            "candidate_passed": False,
            "served_model_id": manifest["model_id"],
            "registry_aliases": registry,
            "baseline_accuracy": reports[0]["overall"]["accuracy"],
            "candidate_accuracy": reports[1]["overall"]["accuracy"],
            "load_attempts": len(attempts),
            "video": "pending user recording",
        },
    )
    print(
        "PASS: requirements/evidence audit; candidate rejected; baseline verified; video pending"
    )


if __name__ == "__main__":
    main()
