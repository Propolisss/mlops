# Проверка требований ДЗ 8+9+10

| Требование | Реализация / доказательство |
|---|---|
| Исходный проект отдельным коммитом | `dbafa00` |
| Исправленные код и зависимости | `5c93db9`, `13adad5`, `uv.lock`, `pyproject.toml` |
| Семь независимых причин | `defects.csv`, красные `evidence/original-*.log` |
| Минимум шесть своих интеграционных тестов | `integration/test_system.py`: шесть; три HTTP, три subprocess |
| Красный и зелёный прогон каждого из шести | `evidence/original-integration.log`, `evidence/quick-check.log` |
| Ещё проверки транзакции/файлов и чистого старта | Шесть в `integration/test_safety.py` |
| Выданные 13 тестов сохранены | Отпечаток `09bd65e6b886`, пустой diff `tests/` |
| Тесты ДЗ 6+7 сохранены | Пустой diff `hw6-7/`; `hw67-regression.log` (24 unit), `hw67-full-check.log` (9/9) |
| Один Prefect-flow, четыре задачи | `flow.py`, `evidence/flow-*.log` |
| Prefect в чистом Linux-окружении | `evidence/linux-prefect.log`, `evidence/linux-prefect/` |
| Реальная подготовка и устойчивый кеш | Проверка JSONL, `evidence/real-flow-*.log` |
| Сбой и восстановление в новом процессе | `flow-failure.log`, `flow-recovery.log`, состояние `Cached` |
| Изменение содержимого по прежнему пути | `flow-invalidation.log`, `real-flow-invalidation.log` |
| Идемпотентность, попытки отдельно от выпусков | `flow-summary.json`, `test_repeat_release_has_one_event_across_processes` |
| Сбой между указателем и журналом | `pointer-*.log`, durable intent, `test_pointer_journal_crash_recovered_without_duplicate` |
| Полное исходное правило качества | Импорт `quality_gate` ДЗ 6+7 в `real.py` |
| Связанный release-report | `evaluation/release-report.json`, исходные отчёты и их SHA-256 |
| Manifest и реальная проверка файлов | `evaluation/manifest-*.json`, `release.verify_files` |
| Одинаковый runtime оценки и API | Общий `TransformersBackend`, хеш runtime, проверка пакетов и tokenizer |
| Missing/NaN/Inf/чужой отчёт/ошибка диска | Выданные тесты + `test_safety.py` |
| Отказ сохраняет accepted побайтно | `evidence/real-flow-summary.json` с двумя одинаковыми SHA-256 |
| Намеренная поломка проверок | `evidence/mutation-red.log`, `mutation-summary.json`, исправленный код не повреждён |
| Единый быстрый запуск без весов | `make check`, 25/25; `evidence/linux-tests.log` |
| Три триггера с командами | Раздел 6 `REPORT.txt` |
| FastAPI, health/ready/generate | `service.py`, `evidence/api-smoke.json`, `api-restart.json` |
| Настоящий ответ модели | `evidence/api-smoke.json`, веса Qwen; ответ не STUB |
| Прогрев, 503 до ready | HTTP-тест + два настоящих запуска в `api-restart.json` |
| Неправильный запрос без backend | HTTP-тест + реальные 422 и `calls: 0 -> 0` в `api-smoke.json` |
| Лимиты входа настоящей модели | `evidence/api-input-limits.json`: токены/символы/тело/пустой prompt без генерации |
| Фактически загруженные ID и runtime | `backends.py`, `api-smoke.json`, STUB lifecycle в `quick-check.log` |
| A → B → A с перезапусками | `test_http_loaded_version_restart_and_rollback`, STUB ONLY |
| Baseline при отклонённом кандидате | `evaluation/baseline-reference.json`; это не PASS и не champion |
| Одинаковые 8 запросов при 1 и 2 клиентах | `evidence/requests.jsonl`, `load-summary.json` |
| Ошибки, токены, задержка и req/s | Сырые строки и раздел 8 `REPORT.txt` |
| Prefill/decode/KV-cache и ограничения замера | Раздел 10 `REPORT.txt` |
| Способ доступа к весам | `README.txt`, раздел 10 отчёта; публичная база и отдельный локальный адаптер |
| Одно видео 5–8 минут | **Осталось записать пользователю**; `DEMO.md` |

Удалённый CI, Docker-образ сервиса, streaming, кластер и новая модель не заявляются.
