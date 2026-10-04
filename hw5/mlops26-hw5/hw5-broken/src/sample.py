"""Стадия sample: подвыборка из выхода tokenize ДЗ 4.

    python -m src.sample

В ДЗ 4 токенизирован весь датасет: 28 878 примеров train и 3 610 val. Эпоха
на нём — 3 610 шагов оптимизатора, на ноутбуке это часы на вариант, а val
каждые 10 шагов на 3 610 примерах — ещё столько же. Для первого LoRA-рана
берём случайную подвыборку тех же тензоров: заново ничего не токенизируется,
маска и шаблон остаются проверенными в ДЗ 4.
"""

import json
import random
from pathlib import Path

import torch

from src.config import load_params


def subsample(blob: dict, size: int, seed: int) -> dict:
    examples = blob["examples"]
    if size >= len(examples):
        raise SystemExit(f"подвыборка {size} не меньше исходных {len(examples)} примеров")
    # Сортировка индексов сохраняет исходный порядок: перемешивание — дело train.py.
    idx = sorted(random.Random(seed).sample(range(len(examples)), size))
    out = {k: v for k, v in blob.items() if k != "examples"}
    out["examples"] = [examples[i] for i in idx]
    out["source_size"] = len(examples)
    return out


def main() -> None:
    params = load_params()
    cfg, data = params["sample"], params["data"]
    stats = {}
    for split in ("train", "val"):
        blob = torch.load(data[f"source_{split}"], weights_only=False)
        out = subsample(blob, cfg[f"{split}_size"], cfg["seed"])
        Path(data[split]).parent.mkdir(parents=True, exist_ok=True)
        torch.save(out, data[split])
        lens = sorted(len(e["input_ids"]) for e in out["examples"])
        answer = sum(sum(1 for x in e["labels"] if x != -100) for e in out["examples"])
        stats[split] = {
            "examples": len(lens),
            "source_examples": out["source_size"],
            "tokens": sum(lens),
            "answer_tokens": answer,
            "median_len": lens[len(lens) // 2],
            "max_len": lens[-1],
        }
        print(f"{split}: {len(lens)} из {out['source_size']}, токенов {sum(lens):,}, "
              f"медиана {stats[split]['median_len']} -> {data[split]}")
    Path(params["paths"]["metrics"]).mkdir(parents=True, exist_ok=True)
    (Path(params["paths"]["metrics"]) / "sample.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
