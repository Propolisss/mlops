#!/usr/bin/env python3
"""Снимок источника: parquet с Hugging Face → sources/tickets-<lang>.jsonl.

Сеть живёт здесь и только здесь. Стадия collect читает уже скачанные файлы,
поэтому `dvc repro` воспроизводим и не зависит от того, что автор датасета
сделает со своим репозиторием завтра.

Снимок и его sha256 версионируются через DVC (sources.dvc), так что коммит
однозначно определяет данные, на которых собран raw.jsonl.
"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_params  # noqa: E402

TAG_COLUMNS = [f"tag_{i}" for i in range(1, 9)]
KEEP = ["subject", "body", "answer", "type", "queue", "priority", "language", *TAG_COLUMNS]
CHUNK = 1 << 20


def download(url: str, dest: Path) -> str:
    """Скачать, если файла ещё нет. Возвращает sha256 — им сверяется снимок."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        print(f"качаю {url}")
        digest = hashlib.sha256()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urlopen(url) as response, tmp.open("wb") as fh:
            while chunk := response.read(CHUNK):
                fh.write(chunk)
                digest.update(chunk)
        tmp.rename(dest)
        return digest.hexdigest()
    print(f"снимок уже на месте: {dest}")
    digest = hashlib.sha256()
    with dest.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    params = load_params(str(ROOT / "params.yaml"))
    cfg = params["collect"]
    out_dir = ROOT / cfg["source_dir"]
    snapshot = out_dir / "tickets.parquet"

    sha = download(cfg["source_url"], snapshot)
    table = pq.read_table(snapshot, columns=KEEP)
    rows = table.to_pylist()

    written: dict[str, int] = {}
    for lang in sorted({r["language"] for r in rows if r["language"]}):
        path = out_dir / f"tickets-{lang}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            count = 0
            for row in rows:
                if row["language"] != lang:
                    continue
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                count += 1
        written[lang] = count
        print(f"  {path.name}: {count} строк")

    manifest = {
        "dataset": "Tobi-Bueck/customer-support-tickets",
        "page": "https://huggingface.co/datasets/Tobi-Bueck/customer-support-tickets",
        "license": "CC BY-NC 4.0",
        "url": cfg["source_url"],
        "sha256": sha,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "rows_total": len(rows),
        "rows_by_language": written,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"sha256 снимка: {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
