import json
from pathlib import Path

PANELS = [
    ("TTFC p50 / p95 by Pod", "s", [
        ("p50 {{pod}}", "histogram_quantile(0.50,sum by(le,pod)(rate(inference_ttfc_seconds_bucket[1m])))"),
        ("p95 {{pod}}", "histogram_quantile(0.95,sum by(le,pod)(rate(inference_ttfc_seconds_bucket[1m])))")]),
    ("Full response p95 by Pod", "s", [
        ("{{pod}}", "histogram_quantile(0.95,sum by(le,pod)(rate(inference_duration_seconds_bucket[1m])))")]),
    ("Running / waiting by Pod", "short", [
        ("running {{pod}}", "inference_running"), ("waiting {{pod}}", "inference_waiting")]),
    ("Completed requests per second", "reqps", [
        ("{{pod}}", 'rate(inference_requests_total{outcome="completed"}[1m])')]),
    ("Errors / refusals / aborts per second", "reqps", [
        ("{{pod}} {{outcome}}", 'rate(inference_requests_total{outcome!="completed"}[1m])')]),
    ("Ready API replicas", "short", [
        ("Ready", 'sum(kube_pod_status_ready{namespace="hw11",condition="true",pod=~"api-.*"})')]),
    ("API CPU by Pod", "cores", [
        ("{{pod}}", 'sum by(pod)(rate(container_cpu_usage_seconds_total{namespace="hw11",container="api"}[1m]))')]),
    ("API RAM by Pod", "bytes", [
        ("{{pod}}", 'container_memory_working_set_bytes{namespace="hw11",container="api"}')]),
]
panels = []
for i, (title, unit, queries) in enumerate(PANELS):
    panels.append({"id": i + 1, "title": title, "type": "timeseries",
                   "datasource": {"type": "prometheus", "uid": "hw11-prometheus"},
                   "gridPos": {"x": (i % 2) * 12, "y": (i // 2) * 8, "w": 12, "h": 8},
                   "fieldConfig": {"defaults": {"unit": unit, "min": 0}, "overrides": []},
                   "targets": [{"refId": chr(65 + j), "expr": expr, "legendFormat": legend}
                               for j, (legend, expr) in enumerate(queries)]})
dashboard = {"uid": "hw11-inference", "title": "HW11 · simulated backend",
             "schemaVersion": 40, "version": 1, "refresh": "5s", "panels": panels,
             "time": {"from": "now-15m", "to": "now"}, "timezone": "utc",
             "tags": ["hw11", "simulated"], "editable": False}
Path(__file__).resolve().parents[1].joinpath("monitoring/dashboard.json").write_text(
    json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n")
