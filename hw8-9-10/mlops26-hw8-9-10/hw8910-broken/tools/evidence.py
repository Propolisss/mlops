import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import release


def invoke(arguments, name, expected=0, env=None, cwd=PROJECT):
    result = subprocess.run(
        [sys.executable, *arguments],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    text = (
        f"$ {sys.executable} {' '.join(arguments)}\n"
        + result.stdout
        + result.stderr
        + f"\nEXIT_CODE={result.returncode}\n"
    )
    output = PROJECT / "docs/evidence" / f"{name}.log"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text)
    print(f"{name}: exit={result.returncode}", flush=True)
    if expected == 0 and result.returncode != 0:
        raise RuntimeError(text[-6000:])
    if expected != 0 and result.returncode == 0:
        raise AssertionError(f"{name} unexpectedly succeeded")
    return result, text


def original():
    prefix = "hw8-9-10/mlops26-hw8-9-10/hw8910-broken/"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in ["lab.py", "release.py", "service.py", "tests/test_release.py"]:
            content = subprocess.check_output(
                ["git", "show", f"dbafa00:{prefix}{name}"], cwd=PROJECT
            )
            target = root / name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(content)
        env = {**os.environ, "PROJECT_UNDER_TEST": str(root), "PYTHONPATH": str(root)}
        invoke(
            [
                "-m",
                "unittest",
                "discover",
                "-s",
                str(PROJECT / "integration"),
                "-p",
                "test_system.py",
                "-v",
            ],
            "original-integration",
            expected=1,
            env=env,
            cwd=root,
        )
        invoke(
            ["-m", "unittest", "discover", "-s", "tests", "-v"],
            "original-public",
            expected=1,
            env=env,
            cwd=root,
        )


def flows():
    root = PROJECT / ".lab-flow" / str(uuid.uuid4())
    invoke(["lab.py", "--workspace", str(root), "init"], "flow-init")
    env = {
        **os.environ,
        "PREFECT_HOME": str(PROJECT / ".cache/prefect"),
        "PREFECT_SERVER_ANALYTICS_ENABLED": "false",
    }
    args = ["flow.py", "--workspace", str(root)]
    invoke([*args, "--fail-after-prepare"], "flow-failure", 1, env)
    _, recovered = invoke(args, "flow-recovery", env=env)
    if "Cached" not in recovered:
        raise AssertionError("Prefect did not restore the completed preparation task")
    (root / "input.txt").write_text("Changed bytes in the original input path\n")
    _, changed = invoke(args, "flow-invalidation", env=env)
    if "PREPARED" not in changed:
        raise AssertionError("Changed input did not rerun preparation")
    invoke(args, "flow-repeat", env=env)
    events = (root / "publications.jsonl").read_text().splitlines()
    if len(events) != 1:
        raise AssertionError("Repeated release produced duplicate events")
    before = release.digest((root / "accepted.json").read_bytes())
    invoke([*args, "--report", "report-reject.json"], "flow-reject", 1, env)
    after = release.digest((root / "accepted.json").read_bytes())
    if before != after:
        raise AssertionError("Rejection changed accepted bytes")
    crash_root = root.parent / (root.name + "-crash")
    invoke(["lab.py", "--workspace", str(crash_root), "init"], "pointer-init")
    crash_args = ["flow.py", "--workspace", str(crash_root)]
    invoke([*crash_args, "--fail-after-pointer"], "pointer-failure", 1, env)
    if not (crash_root / "pending.json").exists():
        raise AssertionError("Missing durable publication intent")
    invoke(crash_args, "pointer-recovery", env=env)
    invoke(crash_args, "pointer-repeat", env=env)
    if len((crash_root / "publications.jsonl").read_text().splitlines()) != 1:
        raise AssertionError("Crash recovery duplicated publication")
    if (crash_root / "pending.json").exists():
        raise AssertionError("Recovery did not complete intent")
    release.write_json(
        PROJECT / "docs/evidence/flow-summary.json",
        {
            "workspace": str(root.relative_to(PROJECT)),
            "events": len(events),
            "accepted_before_reject_sha256": before,
            "accepted_after_reject_sha256": after,
            "crash_recovered": True,
            "attempts": len((root / "attempts.jsonl").read_text().splitlines()),
        },
    )


def real_flow():
    root = PROJECT / ".real"
    args = [
        "flow.py",
        "--workspace",
        str(root),
        "--manifest",
        "manifest-candidate.json",
        "--report",
        "release-report.json",
    ]
    before = release.digest((root / "accepted.json").read_bytes())
    invoke([*args, "--fail-after-prepare"], "real-flow-failure", 1)
    _, recovery = invoke(args, "real-flow-recovery-reject", 1)
    if "Cached" not in recovery or "Incomplete or rejected evaluation" not in recovery:
        raise AssertionError(
            "Expected cached preparation followed by quality rejection"
        )
    original = (root / "input.jsonl").read_bytes()
    import json

    rows = [json.loads(line) for line in original.decode().splitlines()]
    rows[0]["input"] += "\nChanged evaluation input."
    try:
        release.atomic_bytes(
            root / "input.jsonl",
            b"".join(release.canonical(row) + b"\n" for row in rows),
        )
        _, invalidated = invoke(args, "real-flow-invalidation", 1)
        if (
            "PREPARED" not in invalidated
            or "new full evaluation is required" not in invalidated
        ):
            raise AssertionError(
                "Changed evaluation data reused a stale admission decision"
            )
    finally:
        release.atomic_bytes(root / "input.jsonl", original)
    after = release.digest((root / "accepted.json").read_bytes())
    if before != after:
        raise AssertionError("Rejected real candidate changed the baseline pointer")
    release.write_json(
        PROJECT / "docs/evidence/real-flow-summary.json",
        {
            "accepted_before_sha256": before,
            "accepted_after_sha256": after,
            "candidate_rejected": True,
            "changed_input_rejected": True,
            "baseline_retained": True,
        },
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["original", "flows", "real-flow"])
    args = parser.parse_args()
    if args.command == "original":
        original()
    elif args.command == "flows":
        flows()
    else:
        real_flow()


if __name__ == "__main__":
    main()
