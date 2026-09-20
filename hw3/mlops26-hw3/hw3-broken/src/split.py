"""Стадия split: разбиение на train/val/test."""

import json
import random
import time
from pathlib import Path

from src.config import load_params
from src.contamination import report
from src.schema import Example, dump, iter_examples
from src.textnorm import normalize_group


def group_split(
    groups: dict[str, list[Example]], ratios: dict[str, float], seed: int
) -> dict[str, str]:
    """Раздать сплиты ГРУППАМ, а не строкам: группа уезжает в один сплит целиком.

    Построчный сплит выглядит решением, но им не является: формулировки внутри
    одной темы — парафразы друг друга, и случайная раздача разносит их по
    train и test. Утечка при этом ничего не роняет и не пишет в лог, её
    единственный симптом — подозрительно хорошая метрика на тесте.

    Крупные группы раздаются первыми: последняя большая группа иначе не влезает
    в оставшийся зазор, и фактические доли разъезжаются на проценты.
    """
    keys = sorted(groups)
    random.Random(seed).shuffle(keys)
    keys.sort(key=lambda key: -len(groups[key]))

    total = sum(len(rows) for rows in groups.values())
    targets = {name: total * share for name, share in ratios.items()}
    filled = {name: 0 for name in ratios}

    labels: dict[str, str] = {}
    for key in keys:
        name = max(filled, key=lambda n: targets[n] - filled[n])
        labels[key] = name
        filled[name] += len(groups[key])
    return labels


def main() -> None:
    params = load_params()
    paths = params["paths"]
    cfg = params["split"]
    started = time.perf_counter()

    examples: list[Example] = list(iter_examples(paths["clean"]))
    if cfg["group_key"] != "topic":
        raise SystemExit(f"неизвестный split.group_key: {cfg['group_key']!r}")

    groups: dict[str, list[Example]] = {}
    for ex in examples:
        groups.setdefault(normalize_group(ex.topic), []).append(ex)

    labels = group_split(groups, cfg["ratios"], cfg["seed"])
    buckets: dict[str, list[Example]] = {name: [] for name in cfg["ratios"]}
    for key, rows in groups.items():
        buckets[labels[key]].extend(rows)

    for name, rows in buckets.items():
        out = Path(paths[name])
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for ex in rows:
                fh.write(dump(ex) + "\n")

    nd = params["clean"]["near_dup"]
    rep = report(
        buckets["train"],
        buckets["test"],
        shingle_words=nd["shingle_words"],
        num_perm=nd["num_perm"],
        threshold=params["contamination"]["threshold"],
    )

    metrics = {
        "version": params["collect"]["version"],
        "seed": cfg["seed"],
        "group_key": cfg["group_key"],
        "groups_total": len(groups),
        "sizes": {name: len(rows) for name, rows in buckets.items()},
        "groups": {
            name: len({normalize_group(ex.topic) for ex in rows}) for name, rows in buckets.items()
        },
        "ratios_actual": {
            name: round(len(rows) / len(examples), 4) for name, rows in buckets.items()
        },
        "contamination": rep,
        "seconds": round(time.perf_counter() - started, 2),
    }
    mpath = Path(paths["metrics_split"])
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        "split: "
        + ", ".join(f"{name} {len(rows)}" for name, rows in buckets.items())
        + f" (групп {len(groups)}, {metrics['seconds']} с)"
    )


if __name__ == "__main__":
    main()
