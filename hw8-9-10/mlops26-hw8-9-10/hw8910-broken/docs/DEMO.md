# Видео ДЗ 8+9+10

Все команды — из `hw8-9-10/mlops26-hw8-9-10/hw8910-broken`.
Сервис уже запущен на `http://127.0.0.1:8091`. Для повторного запуска — `make serve`
в отдельном терминале. Не запускайте второй процесс на том же порту.

## Порядок показа, 5–8 минут

1. **GitHub:** PR, исходный коммит `dbafa00`, исправления `5c93db9`, отчётный коммит,
   diff. Коротко: модель маршрутизирует обращения, кандидат хуже базы, поэтому API
   обслуживает исходную Qwen3-0.6B. Новый sweep не выполнялся.
2. **Тесты вживую:** `make check` — 25/25 и отпечаток `09bd65e6b886`, задержаться
   на результате. `make mutation` и открыть `docs/evidence/mutation-red.log`:
   намеренная ошибка действительно валит тесты, рабочий код не повреждён.
3. **Расследование:** `defects.csv`, исходный красный лог; назвать семь причин.
   Открыть `release.py`, показать строгий отчёт, атомарную замену и durable intent.
4. **Prefect:** `flow.py`, затем сохранённые `flow-failure.log`, `flow-recovery.log`
   и `flow-invalidation.log`: Completed → сбой → Cached → пересчёт нового входа.
   В `pointer-recovery.log` показать восстановление события без дубликата.
   При желании повторить весь опыт `make flows` — около минуты, для видео хватит логов.
5. **Настоящий отказ:** `make gate` — ожидаемый ненулевой exit. В
   `docs/evaluation/release-report.json` и `baseline-reference.json` показать отказ,
   связь с отчётами и явное отсутствие PASS у кандидата. В
   `docs/evidence/real-flow-summary.json` — одинаковые хеши указателя до/после отказа.
6. **Настоящий API вживую:** `make smoke`, затем открыть `http://127.0.0.1:8091/docs`.
   Нажать Try it out у `/health`, `/ready` и `/generate`, выполнить запрос:

   ```json
   {
     "prompt": "Customer support ticket: I need a copy of my invoice.\nChoices: Billing, IT Support, Technical Support, REFUSE",
     "max_new_tokens": 32
   }
   ```

   Показать реальный ответ, `model_id`, runtime, число токенов и времена.
   Затем заменить лимит на `100000`: HTTP 422. `/debug` показывает счётчик.
   В `api-restart.json` — готовность и настоящая загрузка в двух процессах;
   STUB-переключения A → B → A видны в `quick-check.log` как `STUB_LIFECYCLE`.
7. **Нагрузка:** `make benchmark` (несколько секунд), `load-summary.json`:
   те же 8 запросов, второй клиент ждёт сериализованный backend. Кратко назвать
   ограничения: мало запросов, без p95/TTFT/ITL, это Transformers, не vLLM.

Для обзора кода: `release.py`, `flow.py`, `service.py`, `backends.py`, `real.py`,
`integration/test_system.py`, `integration/test_safety.py`, `Makefile`,
`pyproject.toml`, `defects.csv`, `REPORT.txt`. Остальные файлы и diff доступны в PR.
Веса, .venv и кеши в репозиторий не входят.

Демонстрационные smoke/load-команды не публикуют кандидата и не меняют MLflow aliases.
`make gate` пересобирает тот же связанный отказ по сохранённым ответам.
