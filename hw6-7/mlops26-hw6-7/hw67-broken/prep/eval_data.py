"""Стадия eval_data: набор проверки части B из данных ДЗ 3.

    python -m prep.eval_data

- data/eval/golden.jsonl — тикеты test из prep/golden.yaml, задача — выбрать очередь;
- data/eval/training-index.jsonl — все исходные тикеты train, тем же форматом input,
  чтобы проверка точного совпадения сравнивала одинаково собранные строки;
- data/eval/behavioral.jsonl — кейсы из prep/behavioral.yaml.

group_id — тема (topic): по ней ДЗ 3 делило split, поэтому пересечение групп
golden и train означало бы утечку.
"""

import json
import re
from pathlib import Path

import yaml

from src.common import load_params

QUEUES = ["Billing and Payments", "Customer Service", "General Inquiry", "Human Resources",
          "IT Support", "Product Support", "Returns and Exchanges", "Sales and Pre-Sales",
          "Service Outages and Maintenance", "Technical Support"]
CHOICES = QUEUES + ["REFUSE"]
OUT = Path("data/eval")
# Немецкие буквы или частые служебные слова; проверено глазами на всех 51 тикетах golden.
GERMAN = re.compile(r"[äöüßÄÖÜ]|\b(und|der|die|das|ich|nicht|mit|wir|ist|für|bitte|Sie)\b")
SHORT_CHARS = 300


def make_input(ticket: str, choices: list[str]) -> str:
    return f"{ticket.strip()}\n\nchoices: {', '.join(choices)}"


def read_jsonl(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def golden_rows(test: list[dict], selection: dict) -> list[dict]:
    by_id = {r["id"]: r for r in test}
    rows = []
    for item in selection["items"]:
        record = by_id[item["id"]]
        answer = json.loads(record["messages"][-1]["content"])
        ticket = record["messages"][1]["content"]
        message = ticket.split("Message:\n", 1)[-1]
        slices = ["lang_de" if GERMAN.search(ticket) else "lang_en",
                  f"type_{answer['type'].lower()}",
                  "len_short" if len(message) <= SHORT_CHARS else "len_long"]
        rows.append({"id": f"golden-{record['id']}", "group_id": record["topic"],
                     "input": make_input(ticket, CHOICES), "expected": answer["queue"],
                     "choices": CHOICES, "slices": slices,
                     "source": f"ДЗ 3 test.jsonl id={record['id']}, тема «{record['topic']}»",
                     "review": f"v{selection['version']}: {item['note']}"})
    return rows


def main() -> None:
    params = load_params()
    test = read_jsonl(params["data"]["split_test"])
    train = read_jsonl(params["data"]["split_train"])
    selection = yaml.safe_load(Path("prep/golden.yaml").read_text(encoding="utf-8"))
    golden = golden_rows(test, selection)
    index = [{"id": r["id"], "group_id": r["topic"], "input": make_input(r["messages"][1]["content"], CHOICES)}
             for r in train]
    behavior = []
    for item in yaml.safe_load(Path("prep/behavioral.yaml").read_text(encoding="utf-8"))["items"]:
        choices = list(reversed(QUEUES)) + ["REFUSE"] if item.get("choices_reversed") else CHOICES
        behavior.append({"id": item["id"], "group_id": item["group_id"], "type": item["type"],
                         "input": make_input(item["ticket"], choices), "expected": item["expected"],
                         "choices": choices, "slices": ["behavior"], "source": item["source"]})
    write_jsonl(OUT / "golden.jsonl", golden)
    write_jsonl(OUT / "training-index.jsonl", index)
    write_jsonl(OUT / "behavioral.jsonl", behavior)
    stats = {"golden": len(golden), "training_index": len(index), "behavioral": len(behavior),
             "slices": {}, "expected": {}}
    for r in golden:
        for s in r["slices"]:
            stats["slices"][s] = stats["slices"].get(s, 0) + 1
        stats["expected"][r["expected"]] = stats["expected"].get(r["expected"], 0) + 1
    Path("metrics").mkdir(exist_ok=True)
    Path("metrics/eval_data.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                              encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
