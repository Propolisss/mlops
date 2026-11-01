ДЗ 8+9+10 — процесс выпуска и API тикетов поддержки

Исходная версия: dbafa00. Исправления: 5c93db9, 13adad5.
Результаты и ограничения: REPORT.txt. Приёмка: docs/ACCEPTANCE.md.
Семь причин: defects.csv. Исходные tests/ не изменены, отпечаток 09bd65e6b886.

Все команды ниже выполняются из этой папки.

Быстрая проверка без весов:
  make install
  make check
  make lint
  make original       # новые тесты на исходной версии: ожидаемые отказы в логах
  make mutation       # намеренная поломка копии исправленного кода: ожидаемый красный прогон
  make flows          # Prefect: сбой, кеш, инвалидация, повтор, отказ, восстановление публикации
  make check-linux    # те же 25 проверок в Linux-контейнере, без model extra

На macOS проверки сервиса работают локально. Linux проверен отдельно контейнером.
Для трёх функций release.py достаточно стандартной библиотеки Python 3.12:
  python3 -m unittest discover -s tests -v

Реальная модель (уже подготовлена на ноутбуке):
  make install-model
  make gate           # exit 1: кандидат действительно отклонён
  make serve          # отдельный терминал; 127.0.0.1:8091, только localhost
  make smoke
  make benchmark

Swagger UI: http://127.0.0.1:8091/docs
GET /health, GET /ready, POST /generate. Debug включён только для локальной демонстрации.
Для реальной модели max_new_tokens строго 32, как в оценке. Вход до 16384 символов
и 4096 токенов вместе с шаблоном. Одновременно вычисляется один запрос.

С чистого клона для демонстрации baseline, без MLflow и без повторного обучения:
  make install-model
  .venv/bin/python tools/restore_baseline.py
  make serve
Веса публичной Qwen загружаются с Hugging Face по точной ревизии и проверяются
по закоммиченному manifest. При наличии HF-кеша можно добавить --local-files-only.
Нужны сохранённые отчёты docs/evaluation/ и прежняя папка hw6-7 из этого репозитория.
Оценённый runtime — MPS/bfloat16 с версиями из manifest. На CPU/CUDA нужна новая
оценка; скрипт не переписывает runtime и не придумывает новый PASS.

Полное повторение оценки baseline и старого кандидата:
  make install-model
  # MLflow ДЗ 6+7 должен быть запущен на 127.0.0.1:5006 с прежними volumes.
  # .real должен отсутствовать: существующий workspace скрипт не перезаписывает.
  make real-init
  make evaluate
  make reference      # однократное сохранение исходной baseline при отказе кандидату
  .venv/bin/python tools/evidence.py real-flow
Новый sweep не выполняется. real-init читает артефакт существующего training run.
Повторная оценка перезаписывает docs/evaluation/: делайте её осознанно в отдельной
копии проекта, если хотите сохранить текущие результаты побайтно.

STUB — только инженерные тесты. Успешный STUB-выпуск не является допуском Qwen.
Кандидат модели не принят; champion в MLflow не создан. API обслуживает исходную
baseline согласно разделу C задания. baseline-reference.json явно хранит
quality_gate_passed=false; это отдельная инициализация исходной точки отсчёта.

В Git нет весов, окружений и кешей. Для serving преподавателю достаточно публичной
базы и отчётов в репозитории. Повтор оценки адаптера требует старого MLflow:
models:/my-qwen-adapter/2, training run e3e3faa78edf487fa8b529f5df3a45db.
Этот сервер локальный; ссылка на него не является публичным доступом к весам.
Артефакт можно предоставить преподавателю отдельно из .real/models/candidate/
по запросу, сверяя файлы с docs/evaluation/manifest-candidate.json.

ci.example.yml — только пример. Удалённый GitHub Actions не запускался,
защита merge не настраивалась. Выполненный эквивалент CI — make check-linux.
