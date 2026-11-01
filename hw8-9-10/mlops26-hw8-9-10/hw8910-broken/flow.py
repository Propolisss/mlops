import argparse
import json
import os
from pathlib import Path
import uuid

os.environ.setdefault("PREFECT_HOME", str(Path(".cache/prefect").resolve()))
Path(os.environ["PREFECT_HOME"]).mkdir(parents=True, exist_ok=True)
os.environ.setdefault("PREFECT_SERVER_ANALYTICS_ENABLED", "false")
os.environ.setdefault("PREFECT_LOGGING_LEVEL", "INFO")

from prefect import flow, get_run_logger, task
from prefect.cache_policies import NO_CACHE

import lab
import release


def preparation_config(root):
    config = lab.read(Path(root) / "config.json")
    return {
        **config,
        "implementation_sha256": release.digest(Path(__file__).read_bytes()),
    }


def input_key(context, parameters):
    root = Path(parameters["root"])
    config = preparation_config(root)
    return release.cache_key(root / config.get("input_path", "input.txt"), config)


@task(persist_result=True, cache_key_fn=input_key, result_serializer="json")
def prepare_input(root):
    root = Path(root)
    config = preparation_config(root)
    path = root / config.get("input_path", "input.txt")
    content = path.read_bytes()
    rows = content.decode("utf-8").splitlines()
    if not rows:
        raise ValueError("Empty input")
    if config.get("format") == "golden-jsonl":
        ids = []
        for line in rows:
            row = json.loads(line)
            if not isinstance(row.get("input"), str) or not row["input"].strip():
                raise ValueError("Missing input")
            if (
                not row.get("choices")
                or row.get("expected") not in row["choices"]
                or not row.get("slices")
            ):
                raise ValueError("Invalid golden row")
            ids.append(row["id"])
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate row IDs")
    key = release.cache_key(path, config)
    persisted = Path(".cache/prepared").resolve() / f"{key}.json"
    result = {
        "uri": str(persisted),
        "input_sha256": release.digest(content),
        "rows": len(rows),
        "config_sha256": release.fingerprint(config),
    }
    get_run_logger().info("PREPARED %s rows=%s", result["input_sha256"], result["rows"])
    release.write_json(persisted, result)
    return result


@task(cache_policy=NO_CACHE)
def acquire_artifact(root, manifest_name):
    manifest = lab.read(Path(root) / manifest_name)
    release.verify_files(manifest, root)
    get_run_logger().info(
        "READ_EXISTING_ARTIFACT %s; no training", manifest["model_id"]
    )
    return {
        "manifest_path": str(Path(root) / manifest_name),
        "model_sha256": manifest["model_sha256"],
    }


@task(cache_policy=NO_CACHE)
def evaluate_admission(root, artifact, report_name):
    manifest = lab.read(artifact["manifest_path"])
    report = lab.read(Path(root) / report_name)
    fixture = manifest["runtime"].get("base_revision") == "fixture-only"
    if not fixture:
        from real import verify_evaluation_binding

        protocol = lab.read(Path(root) / "protocol.json")
        rows = [
            json.loads(line)
            for line in (Path(root) / "input.jsonl").read_text().splitlines()
        ]
        if rows != protocol["golden"]:
            raise ValueError(
                "Prepared evaluation input changed; a new full evaluation is required"
            )
        verify_evaluation_binding(root, manifest, report)
    elif report.get("synthetic") is not True:
        raise ValueError("Fixture report must be explicitly synthetic")
    release.validate_release(manifest, report)
    return {
        "report_path": str(Path(root) / report_name),
        "evaluation_run_id": report["evaluation_run_id"],
    }


@task(cache_policy=NO_CACHE)
def publish_version(root, artifact, decision, fail_after_pointer):
    manifest = lab.read(artifact["manifest_path"])
    report = lab.read(decision["report_path"])
    result = release.publication(root, manifest, report, fail_after_pointer)
    get_run_logger().info(
        "PUBLISHED %s release=%s", result["model_id"], result["release_id"]
    )
    return result


@flow(name="model-release", persist_result=True)
def release_flow(
    root,
    manifest_name="manifest-B.json",
    report_name="report-B.json",
    fail_after_prepare=False,
    fail_after_pointer=False,
):
    root = str(Path(root).resolve())
    attempt = str(uuid.uuid4())
    log = Path(root) / "attempts.jsonl"
    try:
        prepared = prepare_input(root)
        if lab.read(prepared["uri"]) != prepared:
            raise ValueError("Persisted preparation artifact is missing or corrupt")
        if fail_after_prepare:
            raise RuntimeError("Injected failure after preparation")
        artifact = acquire_artifact(root, manifest_name)
        decision = evaluate_admission(root, artifact, report_name)
        result = publish_version(root, artifact, decision, fail_after_pointer)
    except Exception as exc:
        with log.open("a") as stream:
            stream.write(
                json.dumps(
                    {"attempt_id": attempt, "status": "failed", "reason": str(exc)}
                )
                + "\n"
            )
        raise
    with log.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "attempt_id": attempt,
                    "status": "complete",
                    "release_id": result["release_id"],
                    "prepared": prepared,
                }
            )
            + "\n"
        )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(".lab"))
    parser.add_argument("--manifest", default="manifest-B.json")
    parser.add_argument("--report", default="report-B.json")
    parser.add_argument("--fail-after-prepare", action="store_true")
    parser.add_argument("--fail-after-pointer", action="store_true")
    args = parser.parse_args()
    release_flow(
        args.workspace,
        args.manifest,
        args.report,
        args.fail_after_prepare,
        args.fail_after_pointer,
    )


if __name__ == "__main__":
    main()
