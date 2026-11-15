import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cluster import KUBECONFIG, command, data, k, save, upgrade, wait_rollout


def configmap(name, files):
    args = ["create", "configmap", name]
    args.extend(f"--from-file={file}" for file in files)
    yaml = k(*args, "--dry-run=client", "-o", "yaml").stdout
    k("apply", "-f", "-", input=yaml)


def main():
    os.chdir(ROOT)
    os.environ["DOCKER_DEFAULT_PLATFORM"] = "linux/arm64" if platform.machine() == "arm64" else "linux/amd64"
    (ROOT / ".cache").mkdir(exist_ok=True)
    (ROOT / ".tools").mkdir(exist_ok=True)
    kind = ROOT / ".tools/kind"
    if not kind.exists():
        suffix = "darwin-arm64" if sys.platform == "darwin" and platform.machine() == "arm64" else "linux-amd64"
        urllib.request.urlretrieve(f"https://kind.sigs.k8s.io/dl/v0.30.0/kind-{suffix}", kind)
        kind.chmod(0o755)
    save("environment-before.json", {
        "platform": platform.platform(), "machine": platform.machine(),
        "disk": command(["df", "-h", ROOT]).stdout,
        "docker": command(["docker", "info", "--format",
                           '{"version":"{{.ServerVersion}}","cpus":{{.NCPU}},"memory":{{.MemTotal}},"arch":"{{.Architecture}}"}']).stdout,
        "docker_df": command(["docker", "system", "df"]).stdout,
        "kind": command([kind, "version"]).stdout, "helm": command(["helm", "version", "--short"]).stdout,
    })
    if "mlops-hw11" not in command([kind, "get", "clusters"]).stdout.splitlines():
        command(["docker", "pull", "--platform", os.environ["DOCKER_DEFAULT_PLATFORM"],
                 "kindest/node:v1.34.0"], timeout=300)
        command([kind, "create", "cluster", "--name", "mlops-hw11", "--image", "kindest/node:v1.34.0",
                 "--config", "kind.yaml", "--kubeconfig", KUBECONFIG, "--wait", "120s"], timeout=360)
    native = command(["docker", "exec", "mlops-hw11-control-plane", "uname", "-m"]).stdout.strip()
    expected = "aarch64" if platform.machine() == "arm64" else "x86_64"
    if native != expected:
        raise RuntimeError(f"Node architecture {native} != {expected}; refusing emulated measurements")
    k("create", "namespace", "hw11", check=False)
    command(["docker", "build", "--platform", os.environ["DOCKER_DEFAULT_PLATFORM"],
             "-t", "mlops-hw11:0.1.0", "-f", ROOT / "Dockerfile", ROOT.parent], timeout=300)
    command([kind, "load", "docker-image", "mlops-hw11:0.1.0", "--name", "mlops-hw11"], timeout=120)
    command(["docker", "update", "--cpus", "4", "--memory", "4g", "--memory-swap", "4g",
             "mlops-hw11-control-plane"])
    upgrade()
    wait_rollout()
    for i in range(4):
        manifest = {
            "apiVersion": "v1", "kind": "Pod", "metadata": {"name": f"client-{i}", "labels": {"app": "hw11-client"}},
            "spec": {"restartPolicy": "Never", "containers": [{
                "name": "client", "image": "mlops-hw11:0.1.0", "imagePullPolicy": "IfNotPresent",
                "command": ["python", "-c", "import time; time.sleep(14400)"],
                "resources": {"requests": {"cpu": "25m", "memory": "32Mi"},
                              "limits": {"cpu": "250m", "memory": "128Mi"}},
                "securityContext": {"runAsUser": 10001, "allowPrivilegeEscalation": False},
            }]}}
        k("apply", "-f", "-", input=json.dumps(manifest))
    k("wait", "--for=condition=Ready", "pod", "-l", "app=hw11-client", "--timeout=90s", timeout=100)
    # This CA workaround is confined to the disposable kind cluster's metrics collector.
    metrics = ROOT / "monitoring/metrics-server.yaml"
    if not metrics.exists():
        urllib.request.urlretrieve("https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.8.0/components.yaml", metrics)
    k("apply", "-f", metrics, "-n", "kube-system")
    k("patch", "deployment", "metrics-server", "-n", "kube-system", "--type=json", "-p",
      '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]')
    configmap("prometheus-config", ["monitoring/prometheus.yml", "monitoring/rules.yml"])
    configmap("grafana-datasource", ["monitoring/datasource.yml"])
    configmap("grafana-provider", ["monitoring/provider.yml"])
    configmap("grafana-dashboard", ["monitoring/dashboard.json"])
    k("apply", "-f", "monitoring/stack.yaml")
    for name in ["prometheus", "grafana", "kube-state-metrics"]:
        k("rollout", "status", f"deployment/{name}", "--timeout=180s", timeout=190)
    k("rollout", "status", "deployment/metrics-server", "-n", "kube-system",
      "--timeout=120s", timeout=130)
    save("environment-ready.json", {
        "kubernetes": json.loads(k("version", "-o", "json").stdout),
        "nodes": data("get", "nodes"), "clients": data("get", "pods", "-l", "app=hw11-client"),
        "images": data("get", "pods"), "metrics_api": data("get", "apiservice", "v1beta1.metrics.k8s.io"),
        "kube_proxy": k("get", "configmap", "kube-proxy", "-n", "kube-system", "-o", "yaml").stdout,
        "node_limits": command(["docker", "inspect", "--format",
                                '{"memory":{{.HostConfig.Memory}},"nanoCPUs":{{.HostConfig.NanoCpus}}}',
                                "mlops-hw11-control-plane"]).stdout,
    })
    print("HW11 cluster and monitoring are ready", flush=True)


if __name__ == "__main__":
    main()
