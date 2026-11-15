from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cluster
from experiments import append_unique_logs, summarize


def test_terminating_pod_cannot_be_selected_as_stream_or_fault_target(monkeypatch):
    terminating = {"metadata": {"name": "old", "deletionTimestamp": "2026-11-15T19:00:00Z"}}
    current = {"metadata": {"name": "current"}}
    monkeypatch.setattr(cluster, "data", lambda *args: {"items": [terminating, current]})
    assert cluster.pods() == [current]


def test_missing_private_kubeconfig_never_falls_back_to_global(monkeypatch, tmp_path):
    monkeypatch.setattr(cluster, "KUBECONFIG", tmp_path / "absent")
    with pytest.raises(RuntimeError, match="Dedicated homework kubeconfig"):
        cluster.k("get", "pods")


def test_deletion_timestamp_is_a_grace_deadline_not_start_time():
    pod = {"metadata": {"deletionTimestamp": "2026-11-15T19:06:19Z", "deletionGracePeriodSeconds": 60}}
    from datetime import datetime, timezone
    expected = datetime(2026, 11, 15, 19, 5, 19, tzinfo=timezone.utc).timestamp()
    assert cluster.termination_started(pod) == expected
    assert cluster.termination_started({"metadata": {}}) is None


def test_error_count_and_quantiles_have_explicit_population():
    rows = [
        {"outcome": "completed", "pod": "a", "ttfc_seconds": 1, "duration_seconds": 2, "send_lag_seconds": 0},
        {"outcome": "completed", "pod": "b", "ttfc_seconds": 3, "duration_seconds": 4, "send_lag_seconds": .1},
        {"outcome": "rejected", "pod": "a", "ttfc_seconds": None, "duration_seconds": .01, "send_lag_seconds": .2},
    ]
    result = summarize(rows)
    assert result["successful_n"] == 2
    assert result["planned"] == 3
    assert result["error_ratio"] == pytest.approx(1 / 3)
    assert result["ttfc_p50"] == 2
    assert result["ttfc_p95"] == pytest.approx(2.9)
    assert result["outcomes"] == {"completed": 2, "rejected": 1}


def test_lecture_evidence_links_frame_primary_source_and_report():
    from scripts.verify_results import lecture_evidence_complete
    assert lecture_evidence_complete()


def test_lecture_evidence_rejects_an_empty_placeholder(monkeypatch, tmp_path):
    from scripts import verify_results
    monkeypatch.setattr(verify_results, "ROOT", tmp_path)
    (tmp_path / "lecture11-source.json").write_text("{}")
    assert not verify_results.lecture_evidence_complete()


def test_log_merge_preserves_unique_events_from_both_capture_methods(tmp_path):
    log = tmp_path / "server.log"
    seen = set()
    append_unique_logs(log, "old-pod warmup\nrequest-a\nrequest-a\n", seen)
    append_unique_logs(log, "request-a\nrequest-b\nnew-pod warmup\n", seen)
    assert log.read_text().splitlines() == [
        "old-pod warmup", "request-a", "request-b", "new-pod warmup"]
