"""Стадия tokenize ДЗ 4, перенесённая в ДЗ 6+7: split ДЗ 3 -> data/tokenized/*.pt.

    python -m prep.tokenize

Логика примера та же, что в hw4-broken/src/tokenize_data.py: шаблон чата
через prep/prompt.py (копия из ДЗ 4), граница промпта по токенам с запасным
путём по символам, маска -100 на промпте, обрезка по max_seq_len, пример без
единого токена ответа выбрасывается. Отчёт, packing и прогноз времени из ДЗ 4
здесь не нужны: обучению нужны только train.pt и val.pt.

Перезапуск нужен потому, что train.py сверяет пометку модели в тензорах с
model.name, а тензоры ДЗ 4 помечены Qwen/Qwen3-1.7B. Токенизатор у 0.6B и 1.7B
один и тот же; --compare-with сверяет новые тензоры со старыми поэлементно.
"""

import argparse
import json
from pathlib import Path

import torch
from transformers import AutoTokenizer

from prep.prompt import build_chat_text, prompt_token_len
from src.common import load_params
from src.data import LABEL_PAD_ID


def encode(tokenizer, record: dict, params: dict, max_seq_len: int) -> tuple[dict, int]:
    messages = record["messages"]
    full_text = build_chat_text(tokenizer, messages, params, add_generation_prompt=False)
    prompt_text = build_chat_text(tokenizer, messages, params, add_generation_prompt=True)
    if not full_text.startswith(prompt_text):
        raise ValueError(f"{record.get('id')}: строка инференса не префикс строки обучения")
    enc = tokenizer(full_text, add_special_tokens=False, return_offsets_mapping=True)
    ids = enc["input_ids"]
    n_prompt, _ = prompt_token_len(tokenizer, prompt_text, ids, enc["offset_mapping"])
    full_len = len(ids)
    ids = ids[:max_seq_len]
    n = min(n_prompt, len(ids))
    labels = [LABEL_PAD_ID] * n + list(ids[n:])
    example = {"id": record.get("id"), "input_ids": ids, "attention_mask": [1] * len(ids), "labels": labels}
    return example, full_len


def tokenize_split(tokenizer, path: Path, params: dict) -> tuple[list[dict], dict]:
    max_seq_len = params["tokenize"]["max_seq_len"]
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    examples, truncated, dropped = [], 0, 0
    for row in rows:
        example, full_len = encode(tokenizer, row, params, max_seq_len)
        truncated += full_len > max_seq_len
        if not any(x != LABEL_PAD_ID for x in example["labels"]):
            dropped += 1
            continue
        examples.append(example)
    stats = {"rows": len(rows), "examples": len(examples), "truncated": truncated,
             "dropped_no_supervision": dropped,
             "tokens": sum(len(e["input_ids"]) for e in examples),
             "supervised_tokens": sum(sum(x != LABEL_PAD_ID for x in e["labels"]) for e in examples)}
    return examples, stats


def compare(new_path: Path, old_path: Path) -> dict:
    new, old = (torch.load(p, weights_only=True) for p in (new_path, old_path))
    keys = ("id", "input_ids", "attention_mask", "labels")
    same = len(new["examples"]) == len(old["examples"]) and all(
        a[k] == b[k] for a, b in zip(new["examples"], old["examples"]) for k in keys)
    return {"old": str(old_path), "old_model": old["model"], "new_model": new["model"],
            "examples": len(new["examples"]), "identical": same,
            "pad_token_id_equal": new["pad_token_id"] == old["pad_token_id"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--compare-with", help="каталог с train.pt/val.pt ДЗ 4 для поэлементной сверки")
    args = ap.parse_args()
    params = load_params()
    tokenizer = AutoTokenizer.from_pretrained(params["model"]["name"], revision=params["model"]["revision"])
    tokenizer.padding_side = params["tokenize"]["padding_side"]
    stats = {}
    for split in ("train", "val"):
        examples, stats[split] = tokenize_split(tokenizer, Path(params["data"][f"split_{split}"]), params)
        out = Path(params["data"][split])
        out.parent.mkdir(parents=True, exist_ok=True)
        torch.save({"examples": examples, "model": params["model"]["name"],
                    "max_seq_len": params["tokenize"]["max_seq_len"],
                    "padding_side": tokenizer.padding_side, "pad_token_id": tokenizer.pad_token_id}, out)
        print(f"{split}: {stats[split]['examples']} примеров, обрезано {stats[split]['truncated']} -> {out}")
        if args.compare_with:
            stats[split]["compare_with_hw4"] = compare(out, Path(args.compare_with) / f"{split}.pt")
            print(f"  сверка с ДЗ 4: {stats[split]['compare_with_hw4']}")
    Path("metrics").mkdir(exist_ok=True)
    Path("metrics/tokenize.json").write_text(json.dumps(
        {"model": params["model"]["name"], "max_seq_len": params["tokenize"]["max_seq_len"],
         "splits": stats}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
