import json
from datetime import datetime
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent
KUBECONFIG = ROOT / ".cache/kubeconfig"
CONTEXT = "kind-mlops-hw11"


def command(args, timeout=60, check=True, input=None):
    result = subprocess.run([str(a) for a in args], cwd=ROOT, input=input,
                            capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"{args[0:4]} failed ({result.returncode}): {result.stderr[-3000:]}")
    return result


def k(*args, timeout=60, check=True, input=None):
    if not KUBECONFIG.is_file():
        raise RuntimeError("Dedicated homework kubeconfig is missing")
    return command(["kubectl", "--kubeconfig", KUBECONFIG, "--context", CONTEXT,
                    "-n", "hw11", *args], timeout=timeout, check=check, input=input)


def data(*args):
    return json.loads(k(*args, "-o", "json").stdout)


def upgrade(**values):
    args = ["helm", "--kubeconfig", KUBECONFIG, "--kube-context", CONTEXT,
            "upgrade", "--install", "hw11", "chart", "-n", "hw11"]
    for name, value in values.items():
        args.extend(["--set", f"{name}={str(value).lower() if isinstance(value, bool) else value}"])
    return command(args)


def wait_rollout():
    return k("rollout", "status", "deployment/api", "--timeout=180s", timeout=190)


def pods():
    return [pod for pod in data("get", "pods", "-l", "app=hw11-api")["items"]
            if not pod["metadata"].get("deletionTimestamp")]


def termination_started(pod):
    metadata = pod["metadata"]
    deadline = metadata.get("deletionTimestamp")
    if deadline is None:
        return None
    return (datetime.fromisoformat(deadline.replace("Z", "+00:00")).timestamp()
            - metadata.get("deletionGracePeriodSeconds", 0))


def client(code, pod="client-0", timeout=60):
    result = k("exec", pod, "--", "python", "-c", code, timeout=timeout)
    return json.loads(result.stdout)


def get(url, pod="client-0"):
    return client("import httpx,json; r=httpx.get(" + repr(url) +
                  ",timeout=5); print(json.dumps({'status':r.status_code,'body':r.json()}))", pod)


def post(url, body, pod="client-0", timeout=60):
    return client("import httpx,json; r=httpx.post(" + repr(url) + ",json=" + repr(body) +
                  f",timeout={timeout - 5}); print(json.dumps({{'status':r.status_code,"
                  "'body':r.json() if 'application/json' in r.headers.get('content-type','') else r.text}))",
                  pod, timeout)


def wait_until(predicate, timeout=180, interval=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(interval)
    raise TimeoutError("Condition did not become true")


def save(path, value):
    path = ROOT / "results" / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False))
