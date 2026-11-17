import collections
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cluster import termination_started
RESULTS = ROOT / "results"


def lecture_evidence_complete():
    try:
        source = json.loads((ROOT / "lecture11-source.json").read_text())
        lecture, primary = source["lecture"], source["primary_source"]
        url = urlsplit(primary["url"])
        evidence = (ROOT / lecture["evidence_image"]).resolve()
        return bool(
            source["company"] and lecture["slide"] and lecture["timestamp_seconds"] >= 0
            and re.fullmatch(r"[0-9a-f]{64}", lecture["sha256"])
            and evidence.is_relative_to(ROOT.resolve()) and evidence.is_file()
            and primary["verified"] is True and primary["title"]
            and url.scheme == "https" and url.hostname
            and primary["url"] in (ROOT / "report.html").read_text()
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def read(path):
    return json.loads((RESULTS / path).read_text())


def lines(path):
    return [json.loads(s) for s in (RESULTS / path).read_text().splitlines() if s]


def events(path):
    records = []
    for line in (RESULTS / path).read_text().splitlines():
        start = line.find('{"request_id":')
        if start >= 0:
            try:
                records.append(json.loads(line[start:]))
            except ValueError:
                pass
    return records


def verify():
    failures = []
    def check(name, condition):
        if not condition:
            failures.append(name)
        print(f'{"PASS" if condition else "FAIL"} {name}')
    for phase in "abc":
        check(f"phase {phase} finished", (RESULTS / f"phase-{phase}.json").exists())
    required = ["probes.json", "graceful-rollout.json", "rollback.json",
                "replacement-dialogs.json", "forced-failure.json", "alerts.json"]
    for name in required:
        check(f"Kubernetes check {name}", (RESULTS / "checks" / name).exists())
    if (RESULTS / "A-rollout/long-stream.json").exists():
        stream = read("A-rollout/long-stream.json")
        first, last = stream["rows"][0], stream["rows"][-1]
        target = first["piece"]["pod"]
        starts = [termination_started(pod) for row in lines("A-rollout/timeline.jsonl")
                  for pod in row.get("pods", {}).get("items", [])
                  if pod["metadata"]["name"] == target and pod["metadata"].get("deletionTimestamp")]
        check("32-part long stream completed", last["piece"]["type"] == "done" and last["piece"]["chunks"] == 32)
        check("long stream spans target Pod termination", any(first["time"] < t < last["time"] for t in starts))
        rollout_requests = lines("A-rollout/requests.jsonl")
        check("rollout zero failed attempts", all(r["status"] == 200 and r["outcome"] == "completed"
                                                 for r in rollout_requests))
    for mode in ["broken", "fixed"]:
        path = RESULTS / f"A-probes-{mode}/server.log"
        if path.exists():
            received = [e for e in events(path.relative_to(RESULTS)) if e["event"] == "received"]
            early = [e for e in received if not e["backend_ready"]]
            check(f"{mode} probe traffic during warmup", bool(early) if mode == "broken" else not early)
    expected = []
    for policy in ["api", "api-affinity", "api-fixed"]:
        for scenario in (["skew"] if policy == "api-fixed" else ["uniform", "skew"]):
            for repeat in range(1, 4):
                expected.append(f"B-{policy}-{scenario}-{repeat}")
    for run in expected:
        path = RESULTS / run / "requests.jsonl"
        if not path.exists():
            check(f"{run} exists", False)
            continue
        rows = lines(f"{run}/requests.jsonl")
        check(f"{run} 480 attempts", len(rows) == 480)
        check(f"{run} unique IDs", len({r["request_id"] for r in rows}) == len(rows))
        check(f"{run} four clients", {r["client"] for r in rows} == {0, 1, 2, 3})
        if "skew" in run:
            check(f"{run} 80 percent from client-0", sum(r["client"] == 0 for r in rows) == 384)
        check(f"{run} 120 second schedule", max(r["offset"] for r in rows) == 119.75)
        check(f"{run} generator send lag < 1s", max(r["send_lag_seconds"] for r in rows) < 1)
        comparison = RESULTS / run / "histogram-comparison.json"
        check(f"{run} histogram comparison", comparison.exists())
        if comparison.exists():
            c = read(f"{run}/histogram-comparison.json")
            count = sum(float(x["value"][1]) for x in c["prometheus_count"]["data"]["result"])
            check(f"{run} same completed population", count == c["client_n"])
    if (RESULTS / "C-hpa/timeline.jsonl").exists():
        timeline = lines("C-hpa/timeline.jsonl")
        seen = [h["status"].get("currentReplicas") for row in timeline
                for h in row.get("hpa", {}).get("items", [])]
        check("HPA observed 2 and 3", {2, 3} <= set(seen))
        check("HPA returned to 2", bool(seen) and seen[-1] == 2)
        ready = [sum(not p["metadata"].get("deletionTimestamp") and any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in p.get("status", {}).get("conditions", []))
            for p in row["pods"]["items"]) for row in timeline if "pods" in row]
        check("HPA transition always preserves at least two Ready Pods", bool(ready) and min(ready) >= 2)
    for filename in ["a-rollout.png", "b-queue.png", "b-policies.png", "c-hpa.png"]:
        check(f"figure {filename}", (ROOT / "figures" / filename).exists())
    index_path = RESULTS / "request-files.json"
    check("raw request index exists", index_path.is_file())
    if index_path.is_file():
        index = read("request-files.json")
        count = 0
        for entry in index["files"]:
            rows = lines(entry["path"]) if entry["format"] == "jsonl" else read(entry["path"])
            check(f"{entry['run']} indexed raw records preserved", len(rows) == entry["records"])
            check(f"{entry['run']} raw checksum matches index",
                  hashlib.sha256((RESULTS / entry["path"]).read_bytes()).hexdigest() == entry["sha256"])
            count += len(rows)
        check("indexed request total matches raw journals", count == index["records"])
    check("shared Service snapshot exists", (RESULTS / "services.json").is_file())
    check("lecture company/source linked", lecture_evidence_complete())
    result = {"passed": not failures, "failures": failures,
              "note": "All required evidence checks passed." if not failures
                      else "Outstanding requirements are listed in failures."}
    (RESULTS / "verification.json").write_text(json.dumps(result, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(verify())
