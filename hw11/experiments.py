import argparse
import concurrent.futures
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import subprocess
import threading
import time
import urllib.parse

from cluster import ROOT, client, command, data, get, k, pods, post, save, termination_started, upgrade, wait_rollout, wait_until


def prom(query, at=None):
    params = {"query": query}
    if at is not None:
        params["time"] = at
    url = "http://prometheus:9090/api/v1/query?" + urllib.parse.urlencode(params)
    return get(url)["body"]


def append_unique_logs(path, text, seen):
    with path.open("a") as stream:
        for line in text.splitlines():
            if line not in seen:
                stream.write(line + "\n")
                seen.add(line)


@contextmanager
def observe(name):
    stop = threading.Event()
    folder = ROOT / "results" / name
    folder.mkdir(parents=True, exist_ok=True)
    log_path = folder / "server.log"
    log_path.write_text("")
    log_lines = set()
    log_lock = threading.Lock()
    def capture_logs(text):
        with log_lock:
            append_unique_logs(log_path, text, log_lines)
    def sample():
        with (folder / "timeline.jsonl").open("w") as stream:
            while not stop.is_set():
                now = time.time()
                try:
                    record = {
                        "wall_time": now,
                        "pods": data("get", "pods", "-l", "app=hw11-api"),
                        "endpoints": data("get", "endpointslices", "-l",
                                          "kubernetes.io/service-name=api"),
                        "hpa": data("get", "hpa"),
                        "metrics": k("get", "--raw",
                                     "/apis/metrics.k8s.io/v1beta1/namespaces/hw11/pods",
                                     check=False).stdout,
                    }
                    for expr_name, expr in {
                        "waiting": "inference_waiting", "running": "inference_running",
                        "ttfc": "histogram_quantile(0.95,sum by(pod,le)(rate(inference_ttfc_seconds_bucket[30s])))",
                        "alerts": 'ALERTS{alertname=~"HighTTFC|HighFailureRatio"}',
                        "cpu": 'sum by(pod)(rate(container_cpu_usage_seconds_total{namespace="hw11",container="api"}[30s]))',
                    }.items():
                        record[expr_name] = prom(expr)
                    stream.write(json.dumps(record) + "\n")
                    stream.flush()
                    recent = k("logs", "-l", "app=hw11-api", "--prefix",
                               "--since=8s", "--tail=2000", check=False).stdout
                    capture_logs(recent)
                except Exception as exc:
                    stream.write(json.dumps({"wall_time": now, "error": str(exc)}) + "\n")
                    stream.flush()
                stop.wait(3)
    worker = threading.Thread(target=sample, daemon=True)
    worker.start()
    try:
        yield folder
    finally:
        stop.set()
        worker.join(timeout=40)
        capture_logs(k(
            "logs", "-l", "app=hw11-api", "--all-containers=true", "--prefix",
            "--tail=100000", check=False).stdout)
        save("services.json", data("get", "services"))
        save(f"{name}/deployment.json", data("get", "deployment", "api"))


def fetch_client_files(run, ids):
    folder = ROOT / "results" / run
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in ids:
        filename = f"/tmp/{run.replace('/', '-')}.jsonl"
        text = k("exec", f"client-{i}", "--", "cat", filename).stdout
        rows.extend(json.loads(line) for line in text.splitlines() if line)
        schedule = k("exec", f"client-{i}", "--", "cat",
                     filename.replace(".jsonl", ".schedule.json")).stdout
        (folder / f"schedule-client-{i}.json").write_text(schedule)
    rows.sort(key=lambda r: r["index"])
    (folder / "requests.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return rows


def histogram_comparison(run, rows):
    # Fresh Pods start each B run; use lifetime buckets after the last response is scraped.
    time.sleep(8)
    result = prom("sum by(le)(inference_ttfc_seconds_bucket)")
    count = prom("sum(inference_ttfc_seconds_count)")
    success = [r for r in rows if r["outcome"] == "completed"]
    buckets = {item["metric"]["le"]: float(item["value"][1])
               for item in result["data"]["result"]}
    client_buckets = {le: sum(r["ttfc_seconds"] <= float(le) for r in success)
                      for le in buckets}
    save(f"{run}/histogram-comparison.json", {
        "population": "completed responses from fresh API Pods, entire B run",
        "client_n": len(success), "prometheus_count": count,
        "server_buckets": buckets, "client_buckets": client_buckets,
        "prometheus_p95": prom("histogram_quantile(0.95,sum by(le)(inference_ttfc_seconds_bucket))"),
        "client_summary": summarize(rows),
        "note": "Server admission excludes network transit. Buckets interpolate; client quantiles use raw samples.",
    })
    assert sum(float(x["value"][1]) for x in count["data"]["result"]) == len(success)


def load(run, service="api", duration=120, rate=4.0, scenario="uniform", clients=4,
         start=None, keepalive=False, timeout=25):
    start = start or time.time() + 8
    save(f"{run}/run.json", {"start_wall": start, "duration": duration, "rate": rate,
                            "scenario": scenario, "service": service, "clients": clients,
                            "keepalive": keepalive, "request_timeout": timeout,
                            "cache_initial_state": "cold after pod replacement" if run.startswith("B-") else "see phase"})
    def one(i):
        args = ["exec", f"client-{i}", "--", "python", "/app/load.py",
                "--url", f"http://{service}:8000", "--run", run.replace("/", "-"),
                "--output", f"/tmp/{run.replace('/', '-')}.jsonl",
                "--duration", str(duration), "--rate", str(rate),
                "--scenario", scenario, "--start", str(start), "--timeout", str(timeout)]
        if clients == 4:
            args.extend(["--client", str(i)])
        if keepalive:
            args.append("--keepalive")
        k(*args, timeout=duration + timeout + 60)
    with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as pool:
        list(pool.map(one, range(clients)))
    return fetch_client_files(run, range(clients))


def restart_cold():
    k("rollout", "restart", "deployment/api")
    wait_rollout()


def sample_history_after_replacement():
    before = post("http://api:8000/generate",
                  {"prompt": "first", "max_new_tokens": 1,
                   "history": [{"role": "user", "content": "DIALOG-ALPHA-317"}]})
    victim = before["body"]["pod"]
    k("delete", "pod", victim, "--wait=false")
    wait_until(lambda: victim not in [p["metadata"]["name"] for p in pods()])
    wait_rollout()
    after = post("http://api:8000/generate",
                 {"prompt": "continue", "max_new_tokens": 1,
                  "history": [{"role": "user", "content": "DIALOG-ALPHA-317"},
                              {"role": "assistant", "content": "first response"}]})
    other = post("http://api:8000/generate",
                 {"prompt": "continue", "max_new_tokens": 1,
                  "history": [{"role": "user", "content": "DIALOG-BETA-992"}]})
    assert before["status"] == after["status"] == other["status"] == 200
    assert before["body"]["artifact_sha256"] == after["body"]["artifact_sha256"]
    assert after["body"]["model_id"] == "A"
    assert "DIALOG-ALPHA-317" in after["body"]["answer"]
    assert "DIALOG-BETA-992" not in after["body"]["answer"]
    assert "DIALOG-ALPHA-317" not in other["body"]["answer"]
    save("checks/replacement-dialogs.json", {"before": before, "after": after, "other": other})


def probes():
    outcomes = {}
    for broken in [True, False]:
        name = "A-probes-broken" if broken else "A-probes-fixed"
        upgrade(modelVersion="A", brokenProbes=broken, warmupSeconds=20)
        wait_rollout()
        with observe(name):
            k("rollout", "restart", "deployment/api")
            rows = load(name, duration=55, rate=2, clients=1)
            wait_rollout()
        outcomes[name] = sum(r["status"] == 503 for r in rows)
    save("checks/probes.json", outcomes)
    assert outcomes["A-probes-broken"] > 0, outcomes
    assert outcomes["A-probes-fixed"] == 0, outcomes


def long_stream(pod, filename):
    code = """
import httpx,json,time
from pathlib import Path
t=time.monotonic()
rows=[]
with httpx.stream('POST',URL,json={'prompt':'long-running-rollout','max_new_tokens':32},timeout=50) as r:
    status=r.status_code
    for line in r.iter_lines():
        if line:
            rows.append({'time':time.time(),'piece':json.loads(line)})
            if len(rows)==1: Path('/tmp/long-stream-ready.json').write_text(json.dumps(rows[0]))
print(json.dumps({'status':status,'rows':rows,'duration':time.monotonic()-t}))
""".replace("URL", repr(f"http://{pod}:8000/stream"))
    result = client(code, pod="client-3", timeout=60)
    save(filename, result)
    return result


def rollout():
    upgrade(modelVersion="A", chunkDelay=1.25, queueTimeout=45)
    wait_rollout()
    old = pods()[0]
    old_debug = get(f"http://{old['status']['podIP']}:8000/debug")
    assert old_debug["body"]["config"]["chunk_delay"] == 1.25, old_debug
    k("annotate", "pod", old["metadata"]["name"], "controller.kubernetes.io/pod-deletion-cost=-1000",
      "--overwrite")
    client("from pathlib import Path; import json; Path('/tmp/long-stream-ready.json').unlink(missing_ok=True); print('true')",
           pod="client-3")
    with observe("A-rollout"):
        with concurrent.futures.ThreadPoolExecutor() as pool:
            stream_future = pool.submit(long_stream, old["status"]["podIP"], "A-rollout/long-stream.json")
            wait_until(lambda: client("from pathlib import Path; import json; print(json.dumps(Path('/tmp/long-stream-ready.json').exists()))",
                                      pod="client-3"), timeout=15, interval=.2)
            background = pool.submit(load, "A-rollout", duration=75, rate=.2, clients=1, timeout=60)
            rollout_started = time.time()
            save("A-rollout/rollout-marker.json", {"wall_time": rollout_started})
            upgrade(modelVersion="B", chunkDelay=1.25, queueTimeout=45)
            wait_rollout()
            rows = background.result()
            stream = stream_future.result()
    assert stream["rows"][-1]["piece"]["type"] == "done"
    assert stream["duration"] >= 39
    assert stream["rows"][0]["time"] < rollout_started < stream["rows"][-1]["time"]
    timeline = [json.loads(line) for line in (ROOT / "results/A-rollout/timeline.jsonl").read_text().splitlines()]
    terminated = [termination_started(p)
                  for row in timeline for p in row.get("pods", {}).get("items", [])
                  if p["metadata"]["name"] == old["metadata"]["name"] and p["metadata"].get("deletionTimestamp")]
    assert any(stream["rows"][0]["time"] < t < stream["rows"][-1]["time"] for t in terminated)
    assert all(r["outcome"] == "completed" for r in rows), "Rollout interrupted requests"
    assert all(r["status"] == 200 for r in rows)
    assert {r["model_id"] for r in rows} == {"A", "B"}
    save("checks/graceful-rollout.json", {"passed": True, "requests": len(rows),
                                        "long_stream_chunks": len(stream["rows"]) - 1,
                                        "long_stream_duration": stream["duration"],
                                        "long_stream_overlapped_termination": True,
                                        "rollout_started_wall": rollout_started,
                                        "long_stream_pod": old["metadata"]["name"]})
    release_recovery()


def release_recovery():
    with observe("A-bad-release"):
        upgrade(modelVersion="B-bad", failWarmup=True)
        rows = load("A-bad-release", duration=45, rate=.2, clients=1, timeout=60)
        snapshot = {"pods": pods(), "endpoints": data("get", "endpointslices")}
        save("A-bad-release/unready.json", snapshot)
        assert all(r["model_id"] == "B" and r["outcome"] == "completed" for r in rows)
        upgrade(modelVersion="A")
        wait_rollout()
        answers = [post("http://api:8000/generate", {"prompt": "rollback", "max_new_tokens": 1})
                   for _ in range(8)]
        assert all(a["body"]["model_id"] == "A" for a in answers)
        save("checks/rollback.json", answers)
    sample_history_after_replacement()
    forced()


def forced():
    previous = ROOT / "results/A-forced/requests.json"
    if previous.exists() and json.loads(previous.read_text())[0]["outcome"] == "completed":
        previous.parent.rename(ROOT / "results/A-forced-in-namespace")
    upgrade(modelVersion="A", chunkDelay=.6)
    wait_rollout()
    victim = pods()[0]
    with observe("A-forced"):
        code = """
import asyncio,httpx,json,time,sys
sys.path.insert(0,'/app')
from load import request_one
async def main():
 async with httpx.AsyncClient() as c:
  t=time.monotonic(); w=time.time()
  r=await request_one(c,URL,{'index':0,'offset':0,'client':3,'prompt':'forced-kill','max_new_tokens':32},t,w,40,'forced')
  print(json.dumps(r))
asyncio.run(main())
""".replace("URL", repr(f"http://{victim['status']['podIP']}:8000"))
        with concurrent.futures.ThreadPoolExecutor() as pool:
            pending = pool.submit(client, code, "client-3", 50)
            time.sleep(3)
            # PID 1 ignores an unhandled SIGKILL from its own namespace; signal via the node runtime.
            container_id = victim["status"]["containerStatuses"][0]["containerID"].split("://", 1)[1]
            command(["docker", "exec", "mlops-hw11-control-plane",
                     "ctr", "-n", "k8s.io", "tasks", "kill", "--signal", "SIGKILL", container_id])
            row = pending.result()
            save("A-forced/requests.json", [row])
            assert row["outcome"] == "aborted", row
        rows = load("A-forced-recovery", duration=45, rate=.5, clients=1)
        wait_rollout()
    save("checks/forced-failure.json", {"interrupted": row, "recovery": rows})
    upgrade(modelVersion="A")
    wait_rollout()


def balance(resume=False):
    upgrade(modelVersion="A")
    wait_rollout()
    for service in ["api", "api-affinity", "api-fixed"]:
        scenarios = ["uniform", "skew"] if service != "api-fixed" else ["skew"]
        for scenario in scenarios:
            for repeat in range(1, 4):
                name = f"B-{service}-{scenario}-{repeat}"
                if resume and (ROOT / "results" / name / "summary.json").exists():
                    print(f"REUSE {name}", flush=True)
                    continue
                print(name, flush=True)
                restart_cold()
                with observe(name):
                    rows = load(name, service=service, scenario=scenario)
                    histogram_comparison(name, rows)
                assert len(rows) == 480
                assert max(r["send_lag_seconds"] for r in rows) < 1, "Load generator fell behind"
                save(f"{name}/summary.json", summarize(rows))


def summarize(rows):
    from collections import Counter
    def quantile(values, q):
        values = sorted(values)
        if not values:
            return None
        index = (len(values) - 1) * q
        lower = int(index)
        return values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (index - lower)
    success = [r for r in rows if r["outcome"] == "completed"]
    return {"planned": len(rows), "sent": len(rows), "outcomes": dict(Counter(r["outcome"] for r in rows)),
            "pod_distribution": dict(Counter(r["pod"] for r in rows)),
            "successful_n": len(success), "error_ratio": 1 - len(success) / len(rows) if rows else None,
            "ttfc_p50": quantile([r["ttfc_seconds"] for r in success], .5),
            "ttfc_p95": quantile([r["ttfc_seconds"] for r in success], .95),
            "duration_p95": quantile([r["duration_seconds"] for r in success], .95),
            "max_send_lag": max((r["send_lag_seconds"] for r in rows), default=0)}


def autoscale():
    upgrade(modelVersion="A", **{"hpa.enabled": True})
    wait_rollout()
    with observe("C-hpa"):
        load("C-hpa-load", duration=140, rate=6, scenario="skew", service="api-affinity")
        wait_until(lambda: len([p for p in pods() if not p["metadata"].get("deletionTimestamp")]) == 3,
                   timeout=90)
        save("C-hpa/scaled-up.json", data("get", "hpa", "api"))
        lowered = time.time()
        wait_until(lambda: data("get", "hpa", "api")["status"].get("currentReplicas") == 2,
                   timeout=360, interval=5)
        save("C-hpa/scaled-down.json", {"load_stopped_wall": lowered,
                                       "observed_down_wall": time.time(),
                                       "hpa": data("get", "hpa", "api")})
        time.sleep(6)
    hpa_timeline = [json.loads(line) for line in (ROOT / "results/C-hpa/timeline.jsonl").read_text().splitlines()]
    for row in hpa_timeline:
        if "pods" in row:
            ready_count = sum(
                not pod["metadata"].get("deletionTimestamp") and any(
                    c["type"] == "Ready" and c["status"] == "True"
                    for c in pod.get("status", {}).get("conditions", []))
                for pod in row["pods"]["items"])
            assert ready_count >= 2, ("HPA transition lost the two-replica minimum", row["wall_time"])
    upgrade(modelVersion="A")
    wait_rollout()
    restart_cold()
    with observe("C-alerts"):
        load("C-alert-load", service="api-affinity", scenario="skew", duration=95, rate=18)
        load("C-alert-recovery", duration=75, rate=1, clients=1)
        save("C-alerts/final-alerts.json", get("http://prometheus:9090/api/v1/alerts")["body"])
    timeline = [json.loads(line) for line in (ROOT / "results/C-alerts/timeline.jsonl").read_text().splitlines()]
    seen = {}
    for name in ["HighTTFC", "HighFailureRatio"]:
        states = {entry["metric"]["alertstate"]
                  for item in timeline for entry in item.get("alerts", {}).get("data", {}).get("result", [])
                  if entry["metric"]["alertname"] == name}
        seen[name] = sorted(states)
        assert {"pending", "firing"} <= states, (name, states)
    final = json.loads((ROOT / "results/C-alerts/final-alerts.json").read_text())
    assert not final["data"]["alerts"], final
    save("checks/alerts.json", seen)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["a", "b", "c", "all"])
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    def phase_a():
        prerequisite = ["probes", "graceful-rollout", "rollback", "replacement-dialogs"]
        if args.resume and all((ROOT / f"results/checks/{name}.json").exists() for name in prerequisite):
            forced()
        elif args.resume and (ROOT / "results/checks/graceful-rollout.json").exists():
            release_recovery()
        else:
            if not args.resume or not (ROOT / "results/checks/probes.json").exists():
                probes()
            rollout()
    checks = {"a": phase_a, "b": lambda: balance(args.resume), "c": autoscale}
    for phase in (checks if args.phase == "all" else [args.phase]):
        if args.resume and (ROOT / f"results/phase-{phase}.json").exists():
            print(f"REUSE PHASE {phase}", flush=True)
            continue
        print(f"PHASE {phase} {time.ctime()}", flush=True)
        checks[phase]()
        save(f"phase-{phase}.json", {"completed_wall": time.time()})
    print("Experiments finished", flush=True)


if __name__ == "__main__":
    main()
