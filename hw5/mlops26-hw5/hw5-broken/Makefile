.PHONY: install sample train train-all train-freeze compare plot check repro push clean distclean

install:
	uv sync

# Подвыборка из выхода tokenize ДЗ 4 (data/tokenized, импорт через dvc).
sample:
	uv run python -m src.sample

data/sample/train.pt:
	uv run python -m src.sample

# Два прогона: адаптеры на всех слоях и с замороженными первыми 14.
train: data/sample/train.pt train-all train-freeze plot

train-all:
	uv run python -m src.train --variant all_layers

train-freeze:
	uv run python -m src.train --variant freeze14

plot:
	uv run python -m src.plot

# Базовая модель против адаптера на пяти фиксированных промптах.
compare:
	uv run python -m src.compare --variant all_layers

check:
	bash tests/check.sh

# Те же команды, объявленные стадиями в dvc.yaml: пересчитать то, у чего
# изменились deps или params, и отправить адаптеры в локальный remote.
repro:
	uv run dvc repro

push:
	uv run dvc push

# clean — артефакты обучения (адаптеры, метрики, графики); всё пересоздаётся make train.
# distclean — ещё и .venv (~0,8 ГБ): когда ДЗ сдано и место нужнее.
clean:
	rm -rf models metrics/*.json docs/curves.png docs/compare.md .check_*.log
	find . -name __pycache__ -not -path "./.venv/*" -prune -exec rm -rf {} +

distclean: clean
	rm -rf .venv
