import collections
from datetime import datetime, timezone
import html
import hashlib
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments import summarize

RESULTS = ROOT / "results"
FIGURES = ROOT / "figures"
FIGURES.mkdir(exist_ok=True)
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.alpha": .2, "figure.dpi": 140})


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []


def metrics(row, name):
    return row.get(name, {}).get("data", {}).get("result", [])


def pod_ready(pod):
    return any(c["type"] == "Ready" and c["status"] == "True"
               for c in pod.get("status", {}).get("conditions", []))


def savefig(name):
    plt.tight_layout()
    plt.savefig(FIGURES / name)
    plt.close()


def rollout_graph():
    timeline = read_lines(RESULTS / "A-rollout/timeline.jsonl")
    requests = read_lines(RESULTS / "A-rollout/requests.jsonl")
    if not timeline:
        return False
    start = timeline[0]["wall_time"]
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    for pod in sorted({p["metadata"]["name"] for row in timeline
                       for p in row.get("pods", {}).get("items", [])}):
        points = [(row["wall_time"] - start,
                   int(pod_ready(p))) for row in timeline
                  for p in row.get("pods", {}).get("items", []) if p["metadata"]["name"] == pod]
        axes[0].step(*zip(*points), where="post", label=pod[-12:])
    endpoint_points = [(r["wall_time"] - start,
                        sum(e.get("conditions", {}).get("ready", False)
                            for s in r.get("endpoints", {}).get("items", [])
                            for e in s.get("endpoints", []))) for r in timeline]
    axes[0].plot(*zip(*endpoint_points), "k--", label="Service ready endpoints")
    axes[0].set(ylabel="Ready / count", title="A: warmup and rolling update A → B")
    axes[0].legend(fontsize=7, ncol=3)
    for version, marker in [("A", "o"), ("B", "^")]:
        rows = [r for r in requests if r["model_id"] == version]
        axes[1].scatter([r["sent_wall"] - start for r in rows],
                        [r["duration_seconds"] for r in rows], marker=marker, label=version)
    long = read_json(RESULTS / "A-rollout/long-stream.json", {})
    if long.get("rows"):
        first, last = long["rows"][0]["time"], long["rows"][-1]["time"]
        axes[1].axvspan(first - start, last - start, alpha=.15, color="#165d83", label="long stream A")
    axes[1].set(xlabel="Seconds from observation start", ylabel="Response duration, s")
    axes[1].legend()
    savefig("a-rollout.png")
    return True


def queue_graph():
    timeline = read_lines(RESULTS / "B-api-affinity-skew-1/timeline.jsonl")
    if not timeline:
        return False
    start = timeline[0]["wall_time"]
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    for name, axis, ylabel in [("waiting", axes[0], "Waiting requests"),
                                ("ttfc", axes[1], "Server p95 TTFC, s")]:
        by_pod = collections.defaultdict(list)
        for row in timeline:
            for item in metrics(row, name):
                value = float(item["value"][1])
                if value == value:
                    by_pod[item["metric"].get("pod", "?")].append((row["wall_time"] - start, value))
        for index, (pod, points) in enumerate(by_pod.items()):
            axis.plot(*zip(*points), label=pod[-12:], linestyle="-" if index % 2 else "--")
        axis.set(ylabel=ylabel)
        axis.legend()
    axes[0].set_title("B: ClientIP affinity, 80% of arrivals from one client, repeat 1")
    axes[1].set_xlabel("Seconds from observation start")
    savefig("b-queue.png")
    return True


def policies_graph(summaries):
    groups = collections.defaultdict(list)
    for run, summary in summaries.items():
        if run.startswith("B-"):
            groups[run.rsplit("-", 1)[0][2:]].append(summary)
    if not groups:
        return False
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    labels = list(groups)
    for i, name in enumerate(labels):
        rows = groups[name]
        axes[0].scatter([i + (j - 1) * .1 for j in range(len(rows))],
                        [r["ttfc_p95"] for r in rows], label=name)
        axes[1].scatter([i + (j - 1) * .1 for j in range(len(rows))],
                        [100 * r["error_ratio"] for r in rows])
    axes[0].set(ylabel="Client successful p95 TTFC, s", title="B: each point is one 120-second run")
    axes[1].set(ylabel="Unsuccessful attempts, %")
    axes[1].set_xticks(range(len(labels)), labels, rotation=15, ha="right")
    savefig("b-policies.png")
    return True


def policy_findings(summaries):
    groups = collections.defaultdict(list)
    distributions = []
    for run, summary in summaries.items():
        if not run.startswith("B-"):
            continue
        groups[run.rsplit("-", 1)[0]].append(summary)
        counts = summary["pod_distribution"]
        concentration = max(counts.values(), default=0) / max(summary["sent"], 1)
        distributions.append({"run": run, "largest_pod_fraction": concentration,
                              "hot_replica_observed": concentration >= .75,
                              "pod_distribution": counts})
    (RESULTS / "distribution-findings.json").write_text(json.dumps(distributions, indent=2))
    affinity, fixed = groups.get("B-api-affinity-skew", []), groups.get("B-api-fixed-skew", [])
    if len(affinity) != 3 or len(fixed) != 3:
        return "Для вывода об исправлении нужны все три skew-повтора ClientIP и Service без привязки."
    def aggregate(rows):
        valid = [r["ttfc_p95"] for r in rows if r["ttfc_p95"] is not None]
        return (sum(valid) / len(valid) if valid else None,
                sum(r["planned"] - r["successful_n"] for r in rows) / sum(r["planned"] for r in rows))
    ap, ae = aggregate(affinity)
    fp, fe = aggregate(fixed)
    if ap is None or fp is None:
        return "Недостаточно успешных ответов для сравнения p95; см. доли неуспеха."
    conclusion = ("Исправление снизило средний p95 без роста доли неуспеха."
                  if fp < ap and fe <= ae else
                  "Ожидаемый выигрыш исправления подтверждён не полностью; нужны дополнительные условия маршрутизации.")
    return (f"Среднее трёх успешных p95 TTFC: ClientIP {ap:.3f} s, без привязки {fp:.3f} s. "
            f"Неуспех по всем попыткам: {ae:.1%} и {fe:.1%}. {conclusion} "
            "Каждый p95 относится к своему прогону; это не объединённый p95 всей серии. "
            "Локальность кеша при случайном распределении ухудшается, но очередь может стоить дороже пересчёта.")


def infrastructure_findings():
    down = read_json(RESULTS / "C-hpa/scaled-down.json")
    if not down:
        return "Повторный опыт HPA ещё не завершён."
    seconds = down["observed_down_wall"] - down["load_stopped_wall"]
    requests = read_lines(RESULTS / "C-hpa-load/requests.jsonl")
    distribution = dict(collections.Counter(r["pod"] for r in requests))
    timeline = read_lines(RESULTS / "C-hpa/timeline.jsonl")
    start = read_json(RESULTS / "C-hpa-load/run.json")["start_wall"]
    ready_three = [row["wall_time"] - start for row in timeline
                   if sum(pod_ready(p) and not p["metadata"].get("deletionTimestamp")
                          for p in row.get("pods", {}).get("items", [])) == 3]
    observed = f"{min(ready_three):.1f} s" if ready_three else "не зафиксировано"
    return (f"Три Ready-реплики впервые наблюдались через {observed} после начала подачи нагрузки. "
            f"Возврат к двум репликам наблюдался через {seconds:.1f} s после завершения нагрузочного клиента. "
            "Это фактическое время вместе с усреднением CPU, окном стабилизации и циклом контроллера. "
            f"Распределение {len(requests)} попыток HPA-опыта по Pod: {distribution}. "
            "Новая ёмкость не получает горячие ClientIP-привязки автоматически; масштабирование само по себе очередь не устраняет.")


def lecture_case():
    source = read_json(ROOT / "lecture11-source.json")
    if not source:
        return ("<p><strong>Ограничение источников.</strong> Связь с кейсом компании из лекции "
                "ещё не подтверждена; этот пункт защиты остаётся открытым.</p>")
    lecture = source["lecture"]
    primary = source["primary_source"]
    return (
        '<p><strong>Кейс из лекции.</strong> На 08:30, слайд '
        f'{html.escape(lecture["slide"])} «{html.escape(lecture["title"])}» '
        f'(<a href="{html.escape(lecture["evidence_image"], quote=True)}">кадр из видео</a>), '
        'разбирается Google Vertex AI / GKE Inference Gateway. '
        f'<a href="{html.escape(primary["url"], quote=True)}">{html.escape(primary["title"])}</a> '
        'объясняет изменение весов prefix:queue:kv-utilization с 3:3:2 на 3:5:2: '
        'очередь получает больший вес, и маршрутизатор обходит горячую реплику даже при cache hit. '
        'Это поддерживает выбор свободной B вместо ожидания 3 секунды на A. '
        'В первоисточнике cache hit вырос с 35% до 70%, p95 TTFT для DeepSeek V3.1 снизился на 52%. '
        'Эти результаты относятся к системе Google с настоящими токенами. '
        'Наше исправление снимает ClientIP-привязку и проверено по TTFC CPU-имитатора; '
        'готовая cache/queue-aware политика и её веса на inference engine здесь не проверялись.</p>'
    )


def hpa_graph():
    timeline = read_lines(RESULTS / "C-hpa/timeline.jsonl")
    if not timeline:
        return False
    start = timeline[0]["wall_time"]
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    current, ready, cpu = [], [], []
    for row in timeline:
        now = row["wall_time"] - start
        hs = row.get("hpa", {}).get("items", [])
        if hs and "currentReplicas" in hs[0].get("status", {}):
            current.append((now, hs[0]["status"]["currentReplicas"]))
        ready.append((now, sum(pod_ready(p) for p in row.get("pods", {}).get("items", []))))
        cpu_samples = metrics(row, "cpu")
        cpu.append((now, sum(float(i["value"][1]) for i in cpu_samples) if cpu_samples else float("nan")))
    if current:
        axes[0].step(*zip(*current), where="post", label="HPA current replicas")
    axes[0].step(*zip(*ready), where="post", label="Ready", linestyle="--")
    axes[0].set(ylabel="Replicas", title="C: CPU HPA, min=2 / max=3 / stabilization=60s")
    axes[0].set_yticks([2, 3])
    axes[0].set_ylim(1.8, 3.2)
    axes[0].legend()
    axes[1].plot(*zip(*cpu), label="Total API CPU cores")
    axes[1].set(xlabel="Seconds from observation start", ylabel="CPU cores")
    run = read_json(RESULTS / "C-hpa-load/run.json", {})
    if run:
        a = run["start_wall"] - start
        axes[1].axvspan(a, a + run["duration"], color="#165d83", alpha=.15, label="6 req/s skew arrivals")
    axes[1].legend()
    savefig("c-hpa.png")
    return True


def report(summaries, charts, status):
    def number(value):
        return "нет данных" if value is None else f"{value:.3f}"
    rows = "".join(
        f'<tr><td><a href="results/{html.escape(name)}/summary.json">{html.escape(name)}</a></td>'
        f'<td>{s["successful_n"]}/{s["planned"]}</td><td>{number(s["ttfc_p50"])}</td>'
        f'<td>{number(s["ttfc_p95"])}</td><td>{100 * s["error_ratio"]:.1f}%</td></tr>'
        for name, s in summaries.items())
    probes = read_json(RESULTS / "checks/probes.json", {})
    checks = sorted(p.relative_to(ROOT).as_posix() for p in (RESULTS / "checks").glob("*.json"))
    image_tags = "".join(f'<figure><img src="figures/{filename}" alt="{html.escape(description)}">'
                         f'<figcaption>{html.escape(description)}</figcaption></figure>'
                         for filename, description in charts)
    template = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ДЗ 11 · Kubernetes под нагрузкой</title>
<style>
:root{--bg:#f4f7f8;--surface:#fcfdfd;--text:#172a32;--muted:#4c626c;--link:#165d83;--failure:#a82d31}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:17px/1.6 system-ui,sans-serif}
main{max-width:1100px;margin:auto;padding:32px 24px 64px}h1{font-size:2rem;line-height:1.2}h2{font-size:1.5rem;margin:48px 0 16px}
h3{font-size:1.2rem;margin:32px 0 8px}p,li{max-width:74ch}a{color:var(--link);text-underline-offset:3px}
nav{display:flex;flex-wrap:wrap;gap:16px}nav a{padding:8px 0;min-height:44px}
.table{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.85rem;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:12px;border-bottom:1px solid #cad4d8}th{background:var(--surface)}
pre{background:var(--surface);padding:16px;overflow-x:auto}code{font-size:.9em}
figure{margin:24px 0}img{display:block;width:100%;height:auto}figcaption,.meta{color:var(--muted);font-size:.9rem}
:focus-visible{outline:3px solid var(--link);outline-offset:4px}::selection{background:#c9e3ee}
@media print{body{background:var(--surface)}nav{display:none}h2{break-after:avoid}figure,tr{break-inside:avoid}}
</style></head><body><main>
<h1>Kubernetes под нагрузкой</h1>
<p>CPU-имитатор сервиса поддержки. Статус опытов: STATUS. Обновлено: GENERATED.</p>
<nav aria-label="Разделы"><a href="#a">Прогрев и выпуск</a><a href="#b">Балансировка</a><a href="#c">HPA и алерты</a><a href="#review">Ревью</a></nav>
<p><a href="README.md">Запуск, ограничения и удаление стенда</a> ·
<a href="results/summary.json">Машиночитаемый итог</a> · <a href="results/request-files.json">Индекс сырых журналов запросов</a></p>
<h2 id="a">Прогрев, обновление и откат</h2>
<p><strong>Гипотеза.</strong> Готовность HTTP раньше завершения прогрева приводит к 503.
Исходный дефект: brokenProbes=true. Исправление: startup/readiness проверяют прогретый backend,
liveness проверяет живой HTTP. Прогрев 20 секунд, timeout probes 2 секунды.</p>
<p>Получено 503 в исходном / исправленном опыте: PROBES. Полные
<a href="results/A-probes-broken/requests.jsonl">исходные запросы</a> и
<a href="results/A-probes-fixed/requests.jsonl">повтор</a>, соответствующие timeline и server.log сохранены рядом.</p>
<p>RollingUpdate использует maxUnavailable=0 и maxSurge=1. Перед заменой A запущен длинный поток.
preStop снимает готовность, но продолжает обслуживать остаточный трафик до распространения EndpointSlice.
Затем Uvicorn завершает активные ответы. Grace period 60 секунд, ожидание Uvicorn 45 секунд.</p>
<p>B-bad намеренно не проходит прогрев. Её нельзя включать в рабочие endpoints.
После управляемого отката фактические ответы должны сообщать A. Отдельный SIGKILL
проверяет обрыв потока и восстановление; нулевые ошибки при SIGKILL не заявляются.</p>
<p>Артефакт доставляется через ConfigMap, SHA256 и model_id проверяются при загрузке.
История передаётся в запросе; после замены Pod продолжается тот же диалог, кеш начинается заново.</p>
<h2 id="b">Привязка к Pod и горячая реплика</h2>
<p><strong>Гипотеза.</strong> При 80% запросов от одного IP ClientIP перегружает одну реплику,
хотя у другой остаётся ёмкость. Исправление: Service без sessionAffinity, распределение новых TCP-соединений.
Состояние диалога не зависит от выбранной реплики.</p>
<p>Сравниваются одни и те же два backend: concurrency=1, queue=12, LRU=8.
Hit/miss: 100000/900000 PBKDF2-итераций, каждая часть 50000 итераций и 0.08 секунды паузы.
Четыре клиента с разными IP, keep-alive выключен. Все сценарии имеют одинаковое open-loop расписание
для сравниваемых политик, 4 запроса/с, 120 секунд, три повтора. Кеш в начале каждого прогона холодный.</p>
<p>Случайное распределение обычного Service и размещение ClientIP могут менять перекос между повторами.
Все полученные прогоны сохраняются, включая случаи без ожидаемого эффекта.
Ни один результат имитатора не доказывает выигрыш на настоящем inference engine.</p>
<p>POLICY_FINDINGS</p>
<p><strong>Контрпример.</strong> В <a href="results/B-api-affinity-uniform-1/requests.jsonl">ClientIP/uniform, повтор 1</a>
перекос не возник: по 240 запросов на Pod, два клиентских IP на каждую реплику,
480/480 завершений и p95 TTFC 0.375 s. В двух остальных uniform-повторах распределение привязок оказалось хуже.
Таким образом, ClientIP не гарантирует перегрузку при равномерных клиентах.
При 80% трафика через один IP перекос был во всех трёх ClientIP-повторах.
Обычный Service в skew-повторе 1 распределил запросы 236/244: новые TCP-соединения
одного IP могли попадать на разные Pod. Трасса сохраняет client и pod для каждого запроса.</p>
<div class="table"><table><thead><tr><th>Прогон</th><th>Успех / попытки</th><th>p50 TTFC, s</th><th>p95 TTFC, s</th><th>Неуспех</th></tr></thead><tbody>ROWS</tbody></table></div>
<h2 id="c">HPA, мониторинг и измерения</h2>
<p><strong>Гипотеза.</strong> Вычислительная нагрузка поднимет среднюю загрузку относительно CPU requests
выше 70%, HPA увеличит число реплик с 2 до 3. После снятия нагрузки и окна стабилизации 60 секунд
число вернётся к 2. ClientIP не перераспределяет существующую горячую привязку на новый Pod.</p>
<p>HPA_FINDINGS</p>
<p>Prometheus использует Pod discovery и classic Histogram. Request ID и session ID остаются в журналах;
в метриках ограниченные outcome/cache result и имена Pod. Дашборд содержит TTFC, полную длительность,
running/waiting, завершения/с, отказы, CPU/RAM и Ready.</p>
<p>Алерты: p95 TTFC &gt; 0.8 секунды и неуспех &gt; 5%. Для обоих нужны минимум
10 наблюдений за 30 секунд и удержание 15 секунд. Timeline сохраняет pending/firing,
последующий нормальный трафик проверяет возврат в норму.</p>
<pre><code>histogram_quantile(0.95,
  sum by (le) (rate(inference_ttfc_seconds_bucket[1m])))</code></pre>
<p>Клиент считает интервалы монотонными часами. TTFC: от отправки до первой части;
inter-chunk latency: интервалы между частями. Это не TTFT/TPOT: фактических токенов здесь нет.
Успех требует HTTP 200, всех частей и завершающей записи done. Отказы, обрывы, тайм-ауты
и транспортные ошибки учитываются раздельно, автоматических повторов нет.</p>
<p>В каждом B-прогоне histogram-comparison.json сопоставляет число успешных клиентских ответов
и серверных наблюдений после последнего scrape. Buckets и сырой клиентский p95 имеют
разную точность; серверный TTFC также не включает сеть.</p>
CHARTS
<h2>Собственные проверки</h2>
<p><a href="results/unit-tests.txt">Локальные тесты</a> · <a href="results/linux-tests.txt">Linux-тесты</a>.
Проверки Kubernetes: CHECKS.</p>
<h2 id="review">Ревью: следует ли навсегда закреплять диалог за Pod?</h2>
<p>При очереди A в 3 секунды и пересчёте префикса на свободной B за 0.8 секунды новый запрос
выгоднее отправить на B: ожидаемый выигрыш до начала обработки 2.2 секунды при прочих равных.
Кеш следует учитывать вместе с очередью, свободными слотами и стоимостью пересчёта.
Постоянная привязка не имеет механизма обхода перегруженной или потерянной реплики.</p>
<p>Для решения нужны распределения длины входа, hit/miss и eviction, время пересчёта,
running/waiting, TTFC и ошибки по репликам, стоимость передачи истории, точность оценки
очереди и измерение при меняющемся трафике. Допуск нового запроса должен ограничивать
очередь и бюджет ожидания: при исчерпании ёмкости вернуть явный 429 с новой попыткой,
учитываемой отдельно. Свободный Pod должен означать свободную вычислительную ёмкость.</p>
<p>История диалога передаётся в запросе или хранится вне Pod; после потери A другой Pod
восстанавливает диалог и повторно строит кеш. Уже начатый поток остаётся на своей реплике
до завершения в пределах grace period. Перенаправлять его середину на B нельзя:
нужны протокол возобновления, идентификаторы частей и дедупликация. При SIGKILL клиент
фиксирует обрыв и решает вопрос новой попытки, не выдавая частичный ответ за завершённый.</p>
LECTURE_CASE
<h2>Ограничения</h2>
<p>Один узел kind, четыре CPU, CPU-имитатор, фиксированные входы. Нет GPU/KV-cache,
continuous batching, prefill/decode disaggregation и настоящих токенов. Потеря узла не
исследована. Для реальной модели нужно отдельно проверить локальность кеша, доступность
весов всем репликам, повторяемость версии, производительность и потоковый протокол.
Без завершённых phase-файлов работа не считается полностью проверенной.</p>
</main></body></html>"""
    content = (template.replace("STATUS", html.escape(status))
               .replace("GENERATED", datetime.now(timezone.utc).isoformat(timespec="seconds"))
               .replace("PROBES", html.escape(str(probes) if probes else "опыт ещё не завершён"))
               .replace("ROWS", rows or '<tr><td colspan="5">Завершённых прогонов пока нет.</td></tr>')
               .replace("POLICY_FINDINGS", html.escape(policy_findings(summaries)))
               .replace("HPA_FINDINGS", html.escape(infrastructure_findings()))
               .replace("LECTURE_CASE", lecture_case())
               .replace("CHARTS", image_tags or "<p>Графики появятся после получения данных.</p>")
               .replace("CHECKS", ", ".join(f'<a href="{p}">{html.escape(Path(p).name)}</a>' for p in checks)
                        or "ещё не завершены"))
    (ROOT / "report.html").write_text(content)


def main():
    summaries = {}
    request_files = []
    request_count = 0
    for file in sorted([*RESULTS.glob("*/requests.jsonl"), *RESULTS.glob("*/requests.json")]):
        rows = read_lines(file) if file.suffix == ".jsonl" else read_json(file, [])
        if not rows:
            continue
        name = file.parent.name
        summary = summarize(rows)
        summaries[name] = summary
        (file.parent / "summary.json").write_text(json.dumps(summary, indent=2))
        request_files.append({"run": name, "path": file.relative_to(RESULTS).as_posix(),
                              "records": len(rows), "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                              "format": "jsonl" if file.suffix == ".jsonl" else "json"})
        request_count += len(rows)
    (RESULTS / "request-files.json").write_text(json.dumps(
        {"records": request_count, "files": request_files}, ensure_ascii=False, indent=2))
    (RESULTS / "summary.json").write_text(json.dumps(summaries, indent=2, ensure_ascii=False))
    charts = []
    for generator, filename, caption in [
        (rollout_graph, "a-rollout.png", "Прогрев, Ready/Endpoints и ответы при rollout A→B."),
        (queue_graph, "b-queue.png", "Очередь и p95 TTFC по Pod при ClientIP и перекосе 80%."),
        (lambda: policies_graph(summaries), "b-policies.png", "Сравнение политик: три повтора, 120 секунд каждый."),
        (hpa_graph, "c-hpa.png", "Число реплик и CPU под нагрузкой и после её снятия."),
    ]:
        if generator():
            charts.append((filename, caption))
    phases = {p: (RESULTS / f"phase-{p}.json").exists() for p in "abc"}
    status = ", ".join(f"{p.upper()} {'завершён' if complete else 'не завершён'}"
                       for p, complete in phases.items())
    report(summaries, charts, status)
    print(json.dumps({"phases": phases, "runs": len(summaries), "requests": request_count,
                      "charts": len(charts)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
