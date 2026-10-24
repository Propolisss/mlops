.PHONY: install data push up down sweep promote predict check check-storage
install:
	uv sync --frozen
# Данные: импорт split ДЗ 3 -> tokenize (Qwen3-0.6B) -> набор проверки; см. dvc.yaml.
data:
	uv run dvc repro
push:
	uv run dvc push
up:
	docker compose -p mlops-hw6 up --build -d --wait
down:
	docker compose -p mlops-hw6 down
sweep:
	uv run python -m src.sweep
promote:
	uv run python -m evaluation.cli promote
predict:
	uv run python -m src.predict --uri models:/my-qwen-adapter@champion --prompt "Какой показатель отражает запасы железа?"
check:
	bash tests/check.sh
check-storage:
	uv run python tests/check_storage.py

.PHONY: reports report candidate baseline-init evaluate eval check-eval check-negative check-all baseline-update predict-candidate
report:
	uv run python -m src.report
candidate:
	uv run python -m evaluation.cli candidate
baseline-init:
	uv run python -m evaluation.cli baseline-init
evaluate:
	uv run python -m evaluation.cli evaluate
eval:
	uv run python -m evaluation.cli gate
check-eval:
	uv run python -m unittest discover -s tests -p 'test_*.py' -v
check-negative:
	uv run python -m evaluation.cli negative
check-all: check check-eval
# metrics/ в .gitignore: копии отчётов сдачи лежат в docs/reports/ и коммитятся.
REPORTS = sweep.json candidate-model.json storage-check.json baseline-outputs.json candidate-outputs.json \
	baseline-eval.json candidate-eval.json eval-diff.json quality-gate.json negative-gate.json
reports:
	mkdir -p docs/reports
	cd metrics && cp $(REPORTS) ../docs/reports/
baseline-update:
	uv run python -m evaluation.cli baseline-update
predict-candidate:
	uv run python -m src.predict --uri models:/my-qwen-adapter@candidate --prompt "Выберите правильный вариант."
