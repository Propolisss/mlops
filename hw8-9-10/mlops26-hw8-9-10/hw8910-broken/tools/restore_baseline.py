import argparse
import json
from pathlib import Path
import shutil
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import release
from real import link_or_copy, verify_reference


def main():
    from huggingface_hub import snapshot_download

    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=PROJECT / ".real")
    parser.add_argument("--local-files-only", action="store_true")
    args = parser.parse_args()
    root = args.workspace.resolve()
    if root.exists():
        raise ValueError("Existing workspace is not overwritten")
    evidence = PROJECT / "docs/evaluation"
    manifest = json.loads((evidence / "manifest-baseline.json").read_text())
    # Reuse evaluated reports without inventing a new score or passing candidate.
    base = Path(
        snapshot_download(
            manifest["runtime"]["base_name"],
            revision=manifest["runtime"]["base_revision"],
            local_files_only=args.local_files_only,
        )
    )
    root.mkdir()
    (root / "models").mkdir()
    shutil.copytree(base, root / "models/base", copy_function=link_or_copy)
    (root / "reports").symlink_to(evidence, target_is_directory=True)
    for name in (
        "manifest-baseline.json",
        "manifest-candidate.json",
        "protocol.json",
        "release-report.json",
        "baseline-reference.json",
    ):
        shutil.copyfile(evidence / name, root / name)
    protocol = json.loads((root / "protocol.json").read_text())
    for name, rows in [
        ("input.jsonl", protocol["golden"]),
        ("behavioral.jsonl", protocol["behavioral"]),
    ]:
        release.atomic_bytes(
            root / name, b"".join(release.canonical(row) + b"\n" for row in rows)
        )
    release.verify_files(manifest, root)
    verify_reference(root, manifest)
    release.write_json(root / "accepted.json", manifest)
    print(
        "Restored the original baseline and saved evaluation evidence; candidate remains rejected."
    )
    print(
        "Serving requires the evaluated device/dtype/packages. A different runtime needs a new evaluation."
    )


if __name__ == "__main__":
    main()
