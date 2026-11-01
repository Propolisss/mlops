import argparse
import gc
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import time

from backends import TransformersBackend, tokenizer_fingerprint
import release

PROJECT = Path(__file__).resolve().parent
PREVIOUS = PROJECT.parents[2] / "hw6-7/mlops26-hw6-7/hw67-broken"
OUTPUT = PROJECT / "docs/evaluation"


def read(path):
    return json.loads(Path(path).read_text())


def previous_solution():
    spec = importlib.util.spec_from_file_location(
        "hw67_solution", PREVIOUS / "evaluation/solution.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def file_hashes(directory):
    return {
        str(path.relative_to(directory)): release.digest(path.read_bytes())
        for path in sorted(Path(directory).rglob("*"))
        if path.is_file() and ".cache" not in path.parts
    }


def sources():
    return {
        "backend": release.digest((PROJECT / "backends.py").read_bytes()),
        "evaluation": release.digest(Path(__file__).read_bytes()),
        "quality_gate": release.digest(
            (PREVIOUS / "evaluation/solution.py").read_bytes()
        ),
    }


def link_or_copy(source, target):
    try:
        os.link(Path(source).resolve(), target)
    except OSError:
        shutil.copyfile(source, target)
    return target


def initialize(root):
    import torch
    from huggingface_hub import snapshot_download
    from mlflow import MlflowClient
    import mlflow
    from transformers import AutoTokenizer
    import yaml

    root = Path(root).resolve()
    if root.exists():
        raise ValueError(
            "Use a new workspace; existing evidence is never silently overwritten"
        )
    root.mkdir(parents=True)
    (root / "models").mkdir()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (root / "reports").symlink_to(OUTPUT, target_is_directory=True)
    baseline = read(PREVIOUS / "evaluation/baseline.json")
    candidate = read(PREVIOUS / "docs/reports/candidate-model.json")
    config = read(PREVIOUS / "evaluation/params.json")
    base = Path(
        snapshot_download(
            baseline["name"], revision=baseline["revision"], local_files_only=True
        )
    )
    # HF snapshots contain symlinks outside the snapshot; materialize a self-contained artifact.
    shutil.copytree(base, root / "models/base", copy_function=link_or_copy)
    mlflow.set_tracking_uri("http://127.0.0.1:5006")
    client = MlflowClient()
    version = client.get_model_version(candidate["name"], candidate["version"])
    if version.run_id != candidate["run_id"]:
        raise ValueError("Registry version and existing training run disagree")
    downloaded = Path(client.download_artifacts(candidate["run_id"], "model"))
    info = yaml.safe_load((downloaded / "MLmodel").read_text())
    adapter = (
        downloaded / info["flavors"]["python_function"]["artifacts"]["adapter"]["path"]
    ).resolve()
    if not adapter.is_relative_to(downloaded.resolve()):
        raise ValueError("Invalid adapter location")
    old_manifest = read(adapter / "manifest.json")
    if (
        old_manifest["model"]["name"] != baseline["name"]
        or old_manifest["model"]["revision"] != baseline["revision"]
    ):
        raise ValueError("Adapter base disagrees with the fixed baseline")
    shutil.copytree(adapter, root / "models/candidate")
    tokenizer = AutoTokenizer.from_pretrained(base, local_files_only=True)
    template = tokenizer.chat_template
    runtime = {
        "base_name": baseline["name"],
        "base_revision": baseline["revision"],
        "tokenizer_sha256": tokenizer_fingerprint(tokenizer),
        "template_sha256": release.digest(template.encode()),
        "chat_template": template,
        "generation": {
            "system": config["system"],
            "max_new_tokens": config["max_new_tokens"],
            "do_sample": False,
            "seed": config["seed"],
            "enable_thinking": config["enable_thinking"],
        },
        "dtype": "bfloat16",
        "device": "mps" if torch.backends.mps.is_available() else "cpu",
        "attention_implementation": "sdpa",
        "max_input_tokens": 4096,
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("torch", "transformers", "peft")
        },
    }
    for name, source in [
        ("input.jsonl", "golden.jsonl"),
        ("behavioral.jsonl", "behavioral.jsonl"),
    ]:
        shutil.copyfile(PREVIOUS / "docs/reports/eval-data" / source, root / name)
    rows = [
        json.loads(line) for line in (root / "input.jsonl").read_text().splitlines()
    ]
    behavior = [
        json.loads(line)
        for line in (root / "behavioral.jsonl").read_text().splitlines()
    ]
    protocol = {
        "runtime": runtime,
        "critical_slices": config["critical_slices"],
        "golden": rows,
        "behavioral": behavior,
        "sources": sources(),
    }
    release.write_json(root / "protocol.json", protocol)
    release.write_json(OUTPUT / "protocol.json", protocol)
    release.write_json(
        root / "config.json",
        {
            "input_path": "input.jsonl",
            "format": "golden-jsonl",
            "tokenizer_revision": baseline["revision"],
            "template_sha256": runtime["template_sha256"],
            "max_length": runtime["max_input_tokens"],
            "code_revision": sources()["evaluation"],
        },
    )
    for role in ("baseline", "candidate"):
        files = file_hashes(
            root / "models" / ("base" if role == "baseline" else "candidate")
        )
        manifest = {
            "schema_version": 1,
            "model_id": f"{baseline['name']}@{baseline['revision']}"
            if role == "baseline"
            else candidate["uri"],
            "artifact_uri": "models/base" if role == "baseline" else "models/candidate",
            "kind": "base" if role == "baseline" else "adapter",
            "model_sha256": release.fingerprint(files),
            "file_hashes": files,
            "protocol_sha256": release.fingerprint(protocol),
            "golden_sha256": release.fingerprint(rows),
            "runtime_sha256": release.fingerprint(runtime),
            "runtime": runtime,
            "origin": baseline if role == "baseline" else candidate,
        }
        release.write_json(root / f"manifest-{role}.json", manifest)
        release.write_json(OUTPUT / f"manifest-{role}.json", manifest)
    release.write_json(
        OUTPUT / "provenance.json",
        {
            "baseline": baseline,
            "candidate": candidate,
            "old_evaluation_run_id": read(
                PREVIOUS / "docs/reports/candidate-eval.json"
            )["evaluation_run_id"],
            "note": "Existing training artifacts read from MLflow; no sweep or training was performed.",
        },
    )


def evaluate(root):
    import torch

    root = Path(root).resolve()
    protocol = read(root / "protocol.json")
    if protocol["sources"] != sources():
        raise ValueError(
            "Evaluation implementation changed; initialize a new protocol/workspace"
        )
    solution = previous_solution()
    rows, behavior = protocol["golden"], protocol["behavioral"]
    run_id = "hw8910-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    for role in ("baseline", "candidate"):
        manifest = read(root / f"manifest-{role}.json")
        artifact = release.verify_files(manifest, root)
        torch.manual_seed(protocol["runtime"]["generation"]["seed"])
        backend = TransformersBackend(manifest, artifact)
        backend.warmup()
        outputs = []
        for index, row in enumerate(rows + behavior, 1):
            started = time.perf_counter()
            try:
                result = backend.generate(
                    row["input"], protocol["runtime"]["generation"]["max_new_tokens"]
                )
                raw, error = result["answer"], None
            except Exception as exc:
                raw, error = "", f"{type(exc).__name__}: {exc}"
            outputs.append(
                {
                    "id": row["id"],
                    "raw": raw,
                    "error": error,
                    "latency_seconds": time.perf_counter() - started,
                }
            )
            print(f"{role} {index}/{len(rows) + len(behavior)} {raw}", flush=True)
        report = solution.score(rows, outputs[: len(rows)])
        report.update(
            status="complete",
            model=manifest["origin"],
            protocol_sha256=manifest["protocol_sha256"],
            golden_sha256=manifest["golden_sha256"],
            baseline_spec_sha256=release.fingerprint(
                read(PREVIOUS / "evaluation/baseline.json")
            ),
            behavioral=solution.score(behavior, outputs[len(rows) :]),
            outputs_sha256=release.fingerprint(outputs),
            evaluation_run_id=run_id,
            runtime_sha256=manifest["runtime_sha256"],
        )
        release.write_json(OUTPUT / f"{role}-outputs.json", outputs)
        release.write_json(OUTPUT / f"{role}-eval.json", report)
        del backend
        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    admission_report(root)


def validated_reports(root):
    root = Path(root)
    protocol = read(root / "protocol.json")
    if protocol["sources"] != sources():
        raise ValueError("Evaluation code changed")
    solution = previous_solution()
    reports = []
    for role in ("baseline", "candidate"):
        manifest = read(root / f"manifest-{role}.json")
        report = read(root / "reports" / f"{role}-eval.json")
        outputs = read(root / "reports" / f"{role}-outputs.json")
        if (
            manifest["protocol_sha256"] != release.fingerprint(protocol)
            or manifest["runtime"] != protocol["runtime"]
            or manifest["runtime_sha256"] != release.fingerprint(protocol["runtime"])
            or manifest["golden_sha256"] != release.fingerprint(protocol["golden"])
        ):
            raise ValueError("Manifest differs from evaluated runtime/protocol")
        if (
            report["protocol_sha256"] != manifest["protocol_sha256"]
            or report["runtime_sha256"] != manifest["runtime_sha256"]
            or report["golden_sha256"] != manifest["golden_sha256"]
            or report["baseline_spec_sha256"]
            != release.fingerprint(read(PREVIOUS / "evaluation/baseline.json"))
        ):
            raise ValueError("Report runtime/protocol mismatch")
        if (
            report["model"] != manifest["origin"]
            or release.fingerprint(outputs) != report["outputs_sha256"]
        ):
            raise ValueError("Report model/output mismatch")
        n = len(protocol["golden"])
        recalculated = solution.score(protocol["golden"], outputs[:n])
        if any(
            report[key] != recalculated[key]
            for key in ("overall", "slices", "per_item")
        ):
            raise ValueError("Stored scores disagree with raw outputs")
        if report["behavioral"] != solution.score(protocol["behavioral"], outputs[n:]):
            raise ValueError("Behavioral scores disagree with raw outputs")
        reports.append(report)
    return reports


def admission_report(root, save=True):
    root = Path(root)
    baseline, candidate = validated_reports(root)
    manifest = read(root / "manifest-candidate.json")
    try:
        gate = previous_solution().quality_gate(
            baseline, candidate, read(root / "protocol.json")["critical_slices"]
        )
    except ValueError as exc:
        gate = {"passed": False, "reason": str(exc)}
    bindings = {
        f"reports/{name}.json": release.digest(
            (root / "reports" / f"{name}.json").read_bytes()
        )
        for name in (
            "baseline-eval",
            "candidate-eval",
            "baseline-outputs",
            "candidate-outputs",
            "protocol",
        )
    }
    report = {
        "status": "complete",
        "passed": gate.get("passed") is True,
        "gate": gate,
        "evaluation_run_id": candidate["evaluation_run_id"],
        "model_id": manifest["model_id"],
        **{key: manifest[key] for key in release.HASH_FIELDS},
        "metrics": {
            key: candidate["overall"][key] for key in ("accuracy", "format_rate")
        },
        "source_files": bindings,
    }
    if save:
        release.write_json(root / "release-report.json", report)
        release.write_json(OUTPUT / "release-report.json", report)
    return report


def verify_evaluation_binding(root, manifest, report):
    root = Path(root)
    for path, expected in report.get("source_files", {}).items():
        if release.digest((root / path).read_bytes()) != expected:
            raise ValueError(f"Evaluation evidence changed: {path}")
    if not report.get("source_files"):
        raise ValueError("Missing evaluation evidence")
    current = admission_report(root, save=False)
    if current != report or manifest != read(root / "manifest-candidate.json"):
        raise ValueError("Release report is not the result of the current quality gate")


def retain_baseline(root):
    root = Path(root)
    if (root / "accepted.json").exists():
        raise ValueError("Reference initialization is allowed only once")
    report = admission_report(root)
    if report["passed"]:
        raise ValueError("Candidate passed; publish it through the release flow")
    baseline, _ = validated_reports(root)
    if baseline["status"] != "complete" or baseline["overall"]["errors"]:
        raise ValueError("Baseline evaluation is incomplete")
    manifest = read(root / "manifest-baseline.json")
    release.verify_files(manifest, root)
    reference = {
        "decision": "retain_original_baseline",
        "quality_gate_passed": False,
        "reason": report["gate"]["reason"],
        "manifest_sha256": release.fingerprint(manifest),
        "baseline_spec_sha256": release.digest(
            (PREVIOUS / "evaluation/baseline.json").read_bytes()
        ),
        "baseline_eval_sha256": release.digest(
            (root / "reports/baseline-eval.json").read_bytes()
        ),
        "candidate_release_report_sha256": release.digest(
            (root / "release-report.json").read_bytes()
        ),
        "note": "Initial reference allowed by assignment section C; not a candidate PASS or champion promotion.",
    }
    release.write_json(root / "baseline-reference.json", reference)
    release.write_json(OUTPUT / "baseline-reference.json", reference)
    release.write_json(root / "accepted.json", manifest)


def verify_reference(root, manifest):
    root = Path(root)
    record = read(root / "baseline-reference.json")
    if record["decision"] != "retain_original_baseline" or manifest["kind"] != "base":
        raise ValueError("No authorization for this model")
    if record["manifest_sha256"] != release.fingerprint(manifest):
        raise ValueError("Accepted manifest changed after reference initialization")
    if record["baseline_spec_sha256"] != release.digest(
        (PREVIOUS / "evaluation/baseline.json").read_bytes()
    ):
        raise ValueError("Original baseline changed")
    if record["baseline_eval_sha256"] != release.digest(
        (root / "reports/baseline-eval.json").read_bytes()
    ):
        raise ValueError("Baseline evidence changed")
    if record["candidate_release_report_sha256"] != release.digest(
        (root / "release-report.json").read_bytes()
    ):
        raise ValueError("Candidate decision changed")
    validated_reports(root)


def verify_served_model(root, manifest):
    if manifest["kind"] == "base":
        verify_reference(root, manifest)
    else:
        report = read(Path(root) / "release-report.json")
        verify_evaluation_binding(root, manifest, report)
        release.validate_release(manifest, report)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["init", "evaluate", "gate", "retain-baseline"]
    )
    parser.add_argument("--workspace", type=Path, default=Path(".real"))
    args = parser.parse_args()
    if args.command == "init":
        initialize(args.workspace)
    elif args.command == "evaluate":
        evaluate(args.workspace)
    elif args.command == "gate":
        result = admission_report(args.workspace)
        print(json.dumps(result["gate"], ensure_ascii=False, indent=2))
        if not result["passed"]:
            raise SystemExit(1)
    else:
        retain_baseline(args.workspace)


if __name__ == "__main__":
    main()
