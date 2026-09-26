.PHONY: install sample update repro push tokenize check clean

install:
	uv sync

# Стадия 0, только для преподавателя: parquet курса -> data/train.jsonl, data/val.jsonl.
# У студента вход другой — его датасет из ДЗ 3, положенный в те же файлы.
sample:
	uv run python -m scripts.make_sample

# Стадия tokenize: JSONL -> токенизированный датасет + метрики + отчёт.
# Та же команда объявлена стадией в dvc.yaml — оттуда её и запускает dvc repro.
tokenize:
	uv run python -m src.tokenize_data

# Вход из ДЗ 3 закреплён за коммитом (rev в train.jsonl.dvc / val.jsonl.dvc).
# Перейти на другую версию датасета: make update REV=<коммит с новым dvc.lock ДЗ 3>
update:
	@test -n "$(REV)" || { echo "укажите REV=<коммит>"; exit 1; }
	uv run dvc update --rev $(REV) train.jsonl.dvc val.jsonl.dvc

# Пересчитать стадии, у которых изменились deps или params.
repro:
	uv run dvc repro

push:
	uv run dvc push

check:
	bash tests/check.sh

clean:
	rm -rf data metrics/tokenize.json docs/tokenize_report.md
