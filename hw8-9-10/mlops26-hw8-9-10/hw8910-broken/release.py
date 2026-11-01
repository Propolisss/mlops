import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

HASH_FIELDS = ("model_sha256", "protocol_sha256", "golden_sha256", "runtime_sha256")


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(value):
    return digest(canonical(value))


def cache_key(data_path: Path, config: dict) -> str:
    return fingerprint(
        {"data_sha256": digest(Path(data_path).read_bytes()), "config": config}
    )


def validate_release(manifest: dict, report: dict) -> None:
    if not isinstance(manifest, dict) or not isinstance(report, dict):
        raise ValueError("Manifest and report must be objects")
    if (
        type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 1
    ):
        raise ValueError("Unsupported schema_version")
    for key in ("model_id", "artifact_uri"):
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            raise ValueError(f"Missing {key}")
    for key in HASH_FIELDS:
        if not isinstance(manifest.get(key), str) or not re.fullmatch(
            "[0-9a-f]{64}", manifest[key]
        ):
            raise ValueError(f"Invalid {key}")
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict) or not runtime:
        raise ValueError("Missing runtime")
    if fingerprint(runtime) != manifest["runtime_sha256"]:
        raise ValueError("Stale runtime_sha256")
    if report.get("status") != "complete" or report.get("passed") is not True:
        raise ValueError("Incomplete or rejected evaluation")
    if (
        not isinstance(report.get("evaluation_run_id"), str)
        or not report["evaluation_run_id"].strip()
    ):
        raise ValueError("Missing evaluation_run_id")
    for key in ("model_id", *HASH_FIELDS):
        if report.get(key) != manifest[key]:
            raise ValueError(f"Evaluation mismatch: {key}")
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("Missing metrics")
    for key in ("accuracy", "format_rate"):
        value = metrics.get(key)
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise ValueError(f"Invalid metric: {key}")


def atomic_bytes(target, data):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, suffix=".tmp", delete=False
        ) as stream:
            temp = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def write_json(target, value):
    atomic_bytes(target, canonical(value) + b"\n")


def publish(manifest: dict, report: dict, target: Path) -> None:
    validate_release(manifest, report)
    atomic_bytes(target, canonical(manifest) + b"\n")


def verify_files(manifest, root):
    files = manifest.get("file_hashes")
    if (
        not isinstance(files, dict)
        or not files
        or fingerprint(files) != manifest["model_sha256"]
    ):
        raise ValueError("Missing or incompatible artifact file list")
    artifact = (Path(root) / manifest["artifact_uri"]).resolve()
    directory = artifact if artifact.is_dir() else artifact.parent
    for name, expected in files.items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            raise ValueError(f"Invalid artifact path: {name}")
        if digest(path.read_bytes()) != expected:
            raise ValueError(f"Artifact checksum mismatch: {name}")
    return artifact


def publication(root, manifest, report, fail_after_pointer=False):
    root = Path(root)
    validate_release(manifest, report)
    verify_files(manifest, root)
    release_id = fingerprint({"manifest": manifest, "report": report})
    pending = root / "pending.json"
    event = {
        "release_id": release_id,
        "model_id": manifest["model_id"],
        "runtime_sha256": manifest["runtime_sha256"],
        "evaluation_run_id": report["evaluation_run_id"],
    }
    journal = root / "publications.jsonl"
    # A durable intent makes a crash between two files recoverable with one writer.
    if pending.exists():
        intent = json.loads(pending.read_text())
        if intent["release_id"] != release_id:
            raise ValueError(
                "Recover the pending release before starting a different one"
            )
    write_json(pending, event)
    publish(manifest, report, root / "accepted.json")
    if fail_after_pointer:
        raise RuntimeError("Injected failure after pointer update")
    events = (
        [json.loads(line) for line in journal.read_text().splitlines()]
        if journal.exists()
        else []
    )
    if not any(item["release_id"] == release_id for item in events):
        events.append(event)
        atomic_bytes(journal, b"".join(canonical(item) + b"\n" for item in events))
    pending.unlink()
    return event
