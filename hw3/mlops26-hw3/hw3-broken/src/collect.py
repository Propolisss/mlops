"""Стадия collect: снимок источника → data/raw.jsonl.

Источник — таблица тикетов службы поддержки (Tobi-Bueck/customer-support-tickets,
CC BY-NC 4.0). Снимок кладёт на диск scripts/fetch_source.py; сюда он приходит
уже скачанным, поэтому стадия не ходит в сеть и воспроизводима.

Контракт стадии, а не её внутренности, держит остальной пайплайн:
на выходе JSONL со строками {"id", "topic", "messages": [system, user, assistant]}.

Скачанный чужой набор сам по себе сдачей не является (README, «Готовый датасет
как источник»). Поэтому стадия не перекладывает таблицу в JSONL один в один,
а делает три вещи, и каждая видна числом в metrics/collect.json:

  1. сужает набор до перечисленных очередей (collect.queues), если это нужно задаче;
  2. сверяет разметку источника (collect.verify_labels) — тикет без обязательных
     полей, со значением вне допустимого набора или с текстом-близнецом под
     другой разметкой выбрасывается, а не переносится в обучение;
  3. собирает из плоских колонок обучающий пример: составную тему
     «очередь / главный тег», один из вариантов инструкции и JSON-ответ.
"""

import hashlib
import json
import re
import time
from collections import defaultdict
from pathlib import Path

from src.config import load_params, source_files
from src.textnorm import normalize_text

TAG_COLUMNS = [f"tag_{i}" for i in range(1, 9)]
REQUIRED = ("subject", "body", "answer", "queue", "type", "priority", "tag_1")

_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_BLANK_RUNS = re.compile(r"\n{3,}")
# Порядок важен: \r\n разворачивается до одиночного \n, иначе останется \r.
_ESCAPES = (("\\r\\n", "\n"), ("\\r", "\n"), ("\\n", "\n"), ("\\t", "\t"))


def unescape(text: str) -> str:
    """Развернуть переводы строк, оставшиеся от выгрузки в CSV.

    В источнике перенос строки лежит двумя символами — обратным слэшем и «n», —
    а абзацы местами размечены тегом <br>. Для модели это мусорные токены
    посреди предложения, для фильтра длин — лишние символы. Плейсхолдеры
    анонимизации (<name>, <tel_num>) трогать нельзя: они несут смысл.
    """
    for source, replacement in _ESCAPES:
        text = text.replace(source, replacement)
    text = _BR.sub("\n", text)
    return _BLANK_RUNS.sub("\n\n", text).strip()


def pick_prompt(example_id: str, variants: list[str]) -> str:
    """Детерминированно выбрать вариант инструкции по id примера.

    Именно sha1, а не встроенный hash(): тот солится на каждый запуск процесса,
    и raw.jsonl переставал бы быть воспроизводимым.
    """
    digest = hashlib.sha1(example_id.encode("utf-8")).hexdigest()
    return variants[int(digest, 16) % len(variants)]


def make_id(row: dict) -> str:
    """Идентификатор примера. В источнике его нет — считаем от содержимого.

    От языка тоже: в многоязычной выгрузке встречаются переводы одного тикета,
    и без языка у них совпал бы id.
    """
    payload = "␟".join((row["language"] or "", row["subject"], row["body"]))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def tags_of(row: dict) -> list[str]:
    return [row[c].strip() for c in TAG_COLUMNS if (row.get(c) or "").strip()]


def topic_of(row: dict) -> str:
    """Тема обращения — ключ группы для сплита.

    Одной очереди мало: крупнейшая занимает 29% набора, а сплит по группам
    требует, чтобы ни одна не решала за весь датасет. Главный тег дробит
    очередь на предметные темы и даёт сотни групп вместо десяти.
    """
    return f"{row['queue']} / {row['tag_1'].strip()}"


def label_signature(row: dict) -> str:
    """Разметка тикета одной строкой — для поиска противоречий в источнике."""
    return json.dumps(
        [row["queue"], row["type"], row["priority"], tags_of(row)], ensure_ascii=False
    )


def build_answer(row: dict, include_reply: bool) -> str:
    answer = {
        "queue": row["queue"],
        "type": row["type"],
        "priority": row["priority"],
        "tags": tags_of(row),
    }
    if include_reply:
        answer["reply"] = row["answer"].strip()
    return json.dumps(answer, ensure_ascii=False)


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def conflicting_texts(rows: list[dict]) -> set[str]:
    """Нормализованные тексты, встречающиеся в источнике под разной разметкой.

    Консистентность (лекция 3): одинаковые вопросы не должны получать разные
    ответы. Выбрать из двух вариантов наугад нельзя — неизвестно, какой верен,
    поэтому выбрасываются все копии такого текста. Реестр строится по всему
    файлу-источнику, а не по срезу n_rows: иначе состав зависел бы от того,
    где обрезали.
    """
    seen: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        seen[normalize_text(f"{row['subject']} {row['body']}")].add(label_signature(row))
    return {text for text, signatures in seen.items() if len(signatures) > 1}


def main() -> None:
    params = load_params()
    cfg = params["collect"]
    paths = params["paths"]
    n_rows = cfg["n_rows"]
    variants = cfg["system_prompts"]
    if not variants:
        raise SystemExit("collect.system_prompts пуст: инструкцию брать неоткуда")
    queues = cfg["queues"]
    wanted = set(queues) if queues else None
    allowed_priorities = set(cfg["allowed_priorities"])
    allowed_types = set(cfg["allowed_types"])

    out = Path(paths["raw"])
    out.parent.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    scanned = written = unescaped = 0
    dropped_queue = dropped_fields = dropped_label = dropped_conflict = 0
    prompts_used: set[str] = set()
    topics: set[str] = set()

    with out.open("w", encoding="utf-8") as fh:
        for src in source_files(params):
            rows = read_jsonl(src)
            # Текст разворачивается до поиска противоречий: иначе две копии
            # одного тикета, размеченные по-разному, разойдутся по реестру
            # из-за разного экранирования, а не из-за разметки.
            for row in rows:
                original = [row.get(field) for field in ("subject", "body", "answer")]
                for field in ("subject", "body", "answer"):
                    row[field] = unescape(row.get(field) or "")
                if [row[f] for f in ("subject", "body", "answer")] != original:
                    unescaped += 1
            conflicts = conflicting_texts(rows) if cfg["verify_labels"] else set()
            taken = 0
            # Фильтры применяются ДО отсечки n_rows: иначе «первые 12000 строк»
            # и «12000 строк по очереди» — разные вещи, и сужение набора давало
            # бы случайный огрызок вместо заказанного объёма.
            for row in rows:
                if taken >= n_rows:
                    break
                scanned += 1
                if wanted is not None and row["queue"] not in wanted:
                    dropped_queue += 1
                    continue
                if cfg["verify_labels"]:
                    if any(not (row.get(field) or "").strip() for field in REQUIRED):
                        dropped_fields += 1
                        continue
                    if row["priority"] not in allowed_priorities or row["type"] not in allowed_types:
                        dropped_label += 1
                        continue
                    if normalize_text(f"{row['subject']} {row['body']}") in conflicts:
                        dropped_conflict += 1
                        continue
                example_id = make_id(row)
                prompt = pick_prompt(example_id, variants)
                prompts_used.add(prompt)
                topic = topic_of(row)
                topics.add(topic)
                record = {
                    "id": example_id,
                    "topic": topic,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {
                            "role": "user",
                            "content": f"Subject:\n{row['subject'].strip()}\n\n"
                            f"Message:\n{row['body'].strip()}",
                        },
                        {"role": "assistant", "content": build_answer(row, cfg["include_reply"])},
                    ],
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                taken += 1
                written += 1

    metrics = {
        "version": cfg["version"],
        "files": len(source_files(params)),
        "rows_scanned": scanned,
        "rows_written": written,
        "dropped_queue_filter": dropped_queue,
        "dropped_missing_fields": dropped_fields,
        "dropped_bad_label": dropped_label,
        "dropped_label_conflict": dropped_conflict,
        "rows_unescaped": unescaped,
        "queues_filter": len(wanted) if wanted else 0,
        "topics": len(topics),
        "system_prompt_variants": len(prompts_used),
        "reply_in_answer": cfg["include_reply"],
        "seconds": round(time.perf_counter() - started, 2),
    }
    mpath = Path(paths["metrics_collect"])
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"collect: версия {cfg['version']}, файлов {metrics['files']}, "
        f"просмотрено {scanned}, записано {written} "
        f"(фильтр очередей -{dropped_queue}, нет обязательных полей -{dropped_fields}, "
        f"значение вне набора -{dropped_label}, противоречивая разметка -{dropped_conflict}), "
        f"развёрнуто экранирование в {unescaped} строках, "
        f"тем {len(topics)}, вариантов инструкции {len(prompts_used)}, "
        f"{metrics['seconds']} с → {out}"
    )


if __name__ == "__main__":
    main()
