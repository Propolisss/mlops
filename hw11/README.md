# ДЗ 11. Kubernetes под нагрузкой

Продолжение API из ДЗ 8–10. В образ входят прежние `GenerateRequest` и
интерфейс `StubBackend`; новый `SimulatedBackend` добавляет CPU-работу,
прогрев, ограниченную очередь, LRU-кеш префиксов и потоковый HTTP.
Прежняя домашняя работа не изменена. `/generate` остаётся доступен,
`/stream` выдаёт NDJSON с завершающей записью `type=done`.

**Backend mode: simulated.** Части ответа не являются токенами модели.
Измеряются TTFC, inter-chunk latency и полная длительность. Настоящие веса
не загружаются. Значение `max_new_tokens` сохранено для совместимости API,
но в этом backend задаёт количество частей (1–32).

## Требования и бюджет

- Docker, kubectl, Helm, uv; kind 0.30.0 скачивается в `.tools/`.
- Kubernetes 1.34.0; выделенный kubeconfig `.cache/kubeconfig`.
- Узел kind: 4 CPU / 4 GiB RAM; API: 100m/96Mi requests,
  600m/192Mi limits на Pod. Максимум три реплики.
- Prometheus: 2 часа / 128 MB retention, emptyDir до 192Mi.
  Grafana: emptyDir до 96Mi. Постоянные тома не создаются.
- До начала работы наблюдалось около 15 GiB свободного места.
  Проверять свободное место перед повтором; планировать до 4 GiB
  на образы и контейнер узла. Фактические данные: `results/environment-*.json`.
- Архитектура задаётся явно: `DOCKER_DEFAULT_PLATFORM=linux/amd64`
  из глобального окружения не должен приводить к эмуляции ARM-ноутбука.
- Команды используют только `kind-mlops-hw11`. Текущий глобальный
  kubeconfig и контексты других кластеров не изменяются.

## Запуск

Из корня репозитория:

```bash
cd hw11
uv sync --python 3.12 --locked
uv run python scripts/dashboard.py
uv run python scripts/setup.py
```

`setup.py` собирает ARM-образ на ARM Mac и amd64-образ на Linux x86,
загружает его в kind, устанавливает Helm-релиз, четыре клиентских Pod,
metrics-server, Prometheus, kube-state-metrics и Grafana.
Зависимости закреплены в `uv.lock` и `requirements.txt` с хешами,
версии образов закреплены тегами. Фактические image ID фиксируются в
результатах; теги публичных образов не гарантируют неизменность digest.
Для metrics-server отключена проверка сертификата kubelet **только внутри
одноразового локального kind**.

Запускать эксперименты последовательно, без параллельного изменения Deployment:

```bash
uv run python experiments.py a
uv run python experiments.py b
uv run python experiments.py c
uv run python scripts/analyze.py
```

Или `uv run python experiments.py all`. Опыты выполняются в Kubernetes,
host только управляет kubectl и сохраняет результаты. Длительный запуск
нужно отправлять в фон с ограничением общего времени.
При ошибке скрипт останавливается; отсутствующий результат не считается PASS.
Для продолжения после исправления причины: `uv run python experiments.py all --resume`.
Этот режим сохраняет завершённые этапы; для нового сравнения запускайте без `--resume`.

## Проверки

```bash
uv run pytest -q
helm lint chart
```

Собственные проверки Kubernetes встроены в `experiments.py`:

1. `probes`: дефект готовности до прогрева и повтор после исправления.
2. `rollout`: полный поток при A→B, B-bad вне трафика, ответы A после отката.
3. `sample_history_after_replacement`: прежний SHA и версия после замены Pod.
4. `rollout` / forced: SIGKILL обрывает поток; следующая серия фиксирует восстановление.
5. `sample_history_after_replacement`: контрольные строки двух диалогов изолированы,
   история продолжается после замены реплики.

Дополнительно `tests/test_transport.py` проверяет реальные HTTP-отказы,
уникальные попытки, обрыв потока клиентом и возврат свободной ёмкости.
Результаты Kubernetes-проверок находятся в `results/checks/`.

## Устройство опытов

- Прогрев 20 секунд. Startup разрешает запуск последующих probes после
  проверки артефакта и прогрева. Readiness управляет EndpointSlice.
  Liveness проверяет только живой HTTP/event loop, не длину очереди.
- Исходный дефект включается `brokenProbes=true`: HTTP-процесс считается
  готовым, хотя backend возвращает 503. Исправление использует backend readiness.
- RollingUpdate: maxUnavailable=0, maxSurge=1, minReadySeconds=3.
  preStop снимает готовность и даёт EndpointSlice 4 секунды на распространение;
  в этот период остаточный трафик обслуживается. Uvicorn ждёт активные потоки
  до 45 секунд, terminationGracePeriodSeconds=60.
- Длинный поток начинается до обновления: генератор подтверждает получение
  первой части отдельным маркером. Для выбранного Pod задаётся
  `controller.kubernetes.io/pod-deletion-cost=-1000`, чтобы он завершался первым.
  Только в этом опыте задержка части увеличена до 1.25 s (32 части ≈40 s);
  параметры B во всех сравнениях остаются прежними.
  Начало завершения Pod вычисляется как `deletionTimestamp − deletionGracePeriodSeconds`;
  сам `deletionTimestamp` является сроком завершения удаления.
- Модель-имитатор приходит из ConfigMap, все реплики проверяют один SHA256
  и `model_id`. Состояние диалога передаётся в `history`; Pod хранит только
  ограниченный имитатор кеша. Потеря кеша не уничтожает историю.
- PBKDF2-HMAC-SHA256 создаёт реальную CPU-нагрузку. Стоимость:
  hit=100000, miss=900000, каждая часть=50000 итераций; sleep=0.08 s/часть.
  Вместимость LRU=8, concurrency=1, очередь до 12, ожидание до 15 s.
  Все сравнения B используют эти параметры без изменения.
- B: обычный Service и ClientIP над одними репликами; исправление:
  `api-fixed` с sessionAffinity=None. Три повтора uniform/skew для первых
  двух политик и три skew-повтора исправления, каждый 120 секунд.
  4 запроса/с, всего 480, skew=80% через client-0. Каждый повтор начинается
  с замены Pod и холодного кеша. Запланированное расписание одинаковое.
- Клиент создаёт новое TCP-соединение для каждого запроса: keep-alive
  отключён, чтобы отделить ClientIP от закрепления соединения.
  Реальные IP видны в `server.log`, сеть/kube-proxy записаны в environment.
  `--keepalive` доступен для отдельного дополнительного опыта.
- HPA autoscaling/v2: CPU averageUtilization=70%, min=2, max=3.
  Scale-down stabilization=60 s. Это средняя метрика относительно CPU requests,
  не размер очереди. Привязка ClientIP может оставить новую реплику без горячего клиента.
- Deployment явно сохраняет `replicas: 2` и при включении HPA: удаление поля
  в Helm patch сбрасывало число до Kubernetes default=1. HPA затем управляет
  scale subresource. Изменения Helm выполняются в покое; обновление значений
  replicas во время масштабированной нагрузки потребовало бы отдельной координации.
- Алерты: p95 TTFC >0.8 s и доля неуспешных исходов >5%, окно 30 s,
  минимум 10 наблюдений, `for: 15s`. Сохраняются pending/firing и восстановление.

## Измерения

Клиент использует `time.monotonic()` для длительностей. Wall clock служит
для совмещения графиков и согласованного начала четырёх клиентов.
Сохраняются запланированное/фактическое время отправки и send lag.
Генератор open-loop: завершение предыдущего запроса не меняет расписание.
Повторов запросов нет; каждый request ID означает отдельную попытку.

Завершённый поток требует записи `done` и совпадения числа частей.
HTTP 200 без неё считается `aborted`. Отдельные исходы: completed, rejected,
http_error, aborted, timeout, transport_error. Неотправленные запросы выявляются
сравнением сохранённого расписания с журналом.

TTFC = first_monotonic − sent_monotonic. Inter-chunk latency = разность времён
двух соседних частей. Перцентили клиента считаются по успешным ответам
с линейной интерполяцией; число наблюдений и доля ошибок идут рядом.
Серверные Histogram TTFC/duration учитывают тот же набор завершённых ответов.
Histogram оценивает перцентиль внутри bucket, поэтому совпадение p95 до
миллисекунды не ожидается; серверное время не включает сеть.

PromQL находится в `monitoring/dashboard.json` и `monitoring/rules.yml`.
Для p95 по всем репликам:

```promql
histogram_quantile(0.95, sum by (le) (rate(inference_ttfc_seconds_bucket[1m])))
```

Для отдельной реплики сохраняется `pod` в `sum by (pod, le)`.
Сравнение lifetime buckets свежих Pod с клиентским журналом каждого B-прогона:
`histogram-comparison.json`. Request/session ID остаются в логах;
в метриках только ограниченные outcome/cache result и discovery Pod labels.

## Интерфейсы

```bash
kubectl --kubeconfig .cache/kubeconfig --context kind-mlops-hw11 -n hw11 port-forward svc/grafana 3000:3000
```

Открыть `http://127.0.0.1:3000/d/hw11-inference`. Это локальный учебный
стенд: анонимный доступ только Viewer; наружу сервисы не публикуются.
Prometheus аналогично: `svc/prometheus 9090:9090`.

## Удаление

```bash
bash scripts/cleanup.sh
```

Удаляются только кластер `mlops-hw11` и образ `mlops-hw11:0.1.0`.
Другие проекты, shared base images и тома прошлых домашних работ остаются.
Образы monitoring, загруженные внутри kind, удаляются вместе с узлом.
Локальные `results/`, `.venv/` и `.tools/` сохраняются для воспроизведения.

## Состав сдачи

`chart/`, `Dockerfile`, `uv.lock`, `requirements.txt`, `monitoring/`,
`tests/`, `experiments.py`, `results/`, четыре графика и `report.html`.
Сырые запросы хранятся один раз, по прогонам: `results/<run>/requests.jsonl`
(для единичного принудительного обрыва — `requests.json`).
Индекс `results/request-files.json` содержит пути, число записей и SHA256; общий
дублирующий `results/requests.jsonl` не хранится. Серверные журналы объединены
в `server.log` каждого опыта с удалением дословно повторяющихся строк из
перекрывающихся выборок. Одинаковый для всех опытов снимок трёх Service
хранится в `results/services.json`. Служебные попытки вынесены в игнорируемый
локальный архив `.cache/pr-cleanup/`; они не являются доказательствами итоговых опытов.
Статус готовности определяется фактическими `results/phase-{a,b,c}.json`
и `results/verification.json`.

Заключительное ревью связано с кейсом Google Vertex AI / GKE Inference Gateway
из `lecture11-extended.mp4`, 08:30, слайд 70/224 «Результаты нагрузки Google».
Кадр: `docs/lecture11-google.jpg`. Идентификатор видео, его SHA256 и первоисточник:
`lecture11-source.json`.

Первоисточник: [How we cut Vertex AI latency by 35% with GKE Inference Gateway](https://cloud.google.com/blog/products/containers-kubernetes/how-gke-inference-gateway-improved-latency-for-vertex-ai),
Google Cloud, 7 февраля 2026. Веса prefix:queue:kv-utilization изменены
с 3:3:2 на 3:5:2, чтобы обходить перегруженные реплики даже при cache hit.
Рост cache hit с 35% до 70% и снижение p95 TTFT на 52% относятся к системе Google;
наши результаты относятся только к CPU-имитатору и измеряют TTFC.
