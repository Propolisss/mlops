"""Реализуйте две функции. Контракт и примеры: EVALUATION.md и tests/test_eval.py."""
import json
import math

FIELDS = ('n', 'correct', 'valid', 'errors', 'accuracy', 'format_rate')


def _reject_constant(name):
    # json.loads по умолчанию принимает NaN/Infinity, а такой ответ не JSON по стандарту
    # и сломал бы запись отчёта с allow_nan=False.
    raise ValueError(name)


def _parse(raw, choices):
    """Метка из строгого ответа {"answer": "<метка>"} либо None."""
    if not isinstance(raw, str):
        return None
    try:
        obj = json.loads(raw, parse_constant=_reject_constant)
    except ValueError:
        return None
    if not isinstance(obj, dict) or set(obj) != {'answer'}:
        return None
    answer = obj['answer']
    return answer if isinstance(answer, str) and answer in choices else None


def _summary(items):
    n = len(items)
    correct = sum(i['correct'] for i in items)
    valid = sum(i['valid'] for i in items)
    errors = sum(i['error'] is not None for i in items)
    return dict(n=n, correct=correct, valid=valid, errors=errors,
                accuracy=correct / n if n else 0.0, format_rate=valid / n if n else 0.0)


def score(rows, outputs):
    """Верните overall, slices и per_item. Не удаляйте плохие ответы из знаменателя.

    Строгий ответ: JSON с единственным строковым полем answer из choices.
    outputs: [{id, raw, error, latency_seconds}, ...].
    В overall и каждом срезе: n, correct, valid, errors, accuracy, format_rate.
    В per_item: id, raw, parsed, valid, correct, error, slices.
    Несовпадающие/дублирующиеся id и пустой набор должны вызвать ValueError.
    """
    if not rows:
        raise ValueError('Пустой набор: оценивать нечего')
    row_ids = [r['id'] for r in rows]
    out_ids = [o['id'] for o in outputs]
    if len(set(row_ids)) != len(row_ids):
        raise ValueError('Дубли id в наборе')
    if len(set(out_ids)) != len(out_ids):
        raise ValueError('Дубли id в ответах')
    if set(row_ids) != set(out_ids):
        raise ValueError('id ответов не совпадают с id набора')
    by_id = {o['id']: o for o in outputs}
    per_item = []
    for row in rows:
        out = by_id[row['id']]
        error = out.get('error')
        raw = out.get('raw')
        # Ответ, полученный с ошибкой генерации, не засчитывается даже если похож на JSON.
        parsed = None if error is not None else _parse(raw, row['choices'])
        valid = parsed is not None
        per_item.append(dict(id=row['id'], raw=raw, parsed=parsed, valid=valid,
                             correct=valid and parsed == row['expected'], error=error,
                             slices=list(row['slices'])))
    slices = {}
    for item in per_item:
        for name in item['slices']:
            slices.setdefault(name, []).append(item)
    return dict(overall=_summary(per_item),
                slices={name: _summary(items) for name, items in sorted(slices.items())},
                per_item=per_item)


def _metrics(block, where):
    """Поля метрики на месте, конечны и согласованы со своими counts."""
    if not isinstance(block, dict) or any(k not in block for k in FIELDS):
        raise ValueError(f'{where}: нет полей {FIELDS}')
    for k in FIELDS:
        v = block[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f'{where}.{k}: не конечное число')
    n, correct, valid, errors = (block[k] for k in ('n', 'correct', 'valid', 'errors'))
    if any(int(x) != x for x in (n, correct, valid, errors)) or n <= 0:
        raise ValueError(f'{where}: counts должны быть целыми, n > 0')
    if not 0 <= correct <= valid <= n or not 0 <= errors <= n:
        raise ValueError(f'{where}: counts противоречат друг другу')
    if abs(block['accuracy'] - correct / n) > 1e-9 or abs(block['format_rate'] - valid / n) > 1e-9:
        raise ValueError(f'{where}: accuracy/format_rate не соответствуют counts')
    return block


def _report(report, role):
    """Полный отчёт по golden: метрики, срезы и per_item согласованы между собой."""
    if not isinstance(report, dict) or report.get('status') != 'complete':
        raise ValueError(f'{role}: отчёт неполный')
    if not isinstance(report.get('model'), dict) or not report['model']:
        raise ValueError(f'{role}: не указана модель')
    overall = _metrics(report.get('overall'), f'{role}.overall')
    per_item = report.get('per_item')
    if not isinstance(per_item, list) or len(per_item) != overall['n']:
        raise ValueError(f'{role}: per_item не совпадает с n')
    ids = [item.get('id') for item in per_item]
    if len(set(ids)) != len(ids):
        raise ValueError(f'{role}: дубли id в per_item')
    recount = _summary([dict(correct=bool(i.get('correct')), valid=bool(i.get('valid')), error=i.get('error'))
                        for i in per_item])
    if any(overall[k] != recount[k] for k in ('n', 'correct', 'valid', 'errors')):
        raise ValueError(f'{role}: overall не соответствует per_item')
    slices = report.get('slices')
    if not isinstance(slices, dict) or not slices:
        raise ValueError(f'{role}: нет срезов')
    sizes = {}
    for item in per_item:
        for name in item.get('slices') or []:
            sizes[name] = sizes.get(name, 0) + 1
    if set(sizes) != set(slices):
        raise ValueError(f'{role}: имена срезов не соответствуют per_item')
    for name, block in slices.items():
        if _metrics(block, f'{role}.slices.{name}')['n'] != sizes[name]:
            raise ValueError(f'{role}: размер среза {name} не соответствует per_item')
    return overall, slices, ids


def quality_gate(baseline, candidate, critical_slices):
    """Верните {passed: True, ...} или вызовите ValueError с причиной отказа.

    Проверьте совместимость протокола/набора/baseline, метрики и counts,
    все id и срезы, общую accuracy/format_rate, критические срезы и behavior.
    Missing, NaN, пустой набор и ошибки генерации означают отказ.
    Пороги задаются до измерения; учебный допуск регрессии равен нулю.
    """
    for key in ('protocol_sha256', 'golden_sha256', 'baseline_spec_sha256'):
        a, b = (r.get(key) if isinstance(r, dict) else None for r in (baseline, candidate))
        if not isinstance(a, str) or not a or a != b:
            raise ValueError(f'Несовместимые отчёты: разный или пустой {key}')
    base, base_slices, base_ids = _report(baseline, 'baseline')
    cand, cand_slices, cand_ids = _report(candidate, 'candidate')
    if base_ids != cand_ids:
        raise ValueError('Разные id вопросов у baseline и candidate')
    if base['n'] < 50:
        raise ValueError(f'Golden set: {base["n"]} строк, нужно не меньше 50')
    if len(base_slices) < 3:
        raise ValueError('Нужно не меньше трёх срезов golden set')
    if set(base_slices) != set(cand_slices) or any(base_slices[s]['n'] != cand_slices[s]['n'] for s in base_slices):
        raise ValueError('Срезы baseline и candidate различаются по именам или размерам')
    if not critical_slices:
        raise ValueError('Не задан ни один критический срез')
    missing = [s for s in critical_slices if s not in base_slices]
    if missing:
        raise ValueError(f'Критический срез отсутствует: {missing}')
    for role, overall in (('baseline', base), ('candidate', cand)):
        if overall['errors']:
            raise ValueError(f'{role}: ошибок генерации {overall["errors"]}')
    regressions = [f'{k}: {cand[k]:.4f} < {base[k]:.4f}' for k in ('accuracy', 'format_rate') if cand[k] < base[k]]
    regressions += [f'срез {s}: {cand_slices[s]["accuracy"]:.4f} < {base_slices[s]["accuracy"]:.4f}'
                    for s in critical_slices if cand_slices[s]['accuracy'] < base_slices[s]['accuracy']]
    if regressions:
        raise ValueError('Регрессия относительно baseline: ' + '; '.join(regressions))
    behavior = _metrics((candidate.get('behavioral') or {}).get('overall'), 'candidate.behavioral')
    if behavior['n'] < 3:
        raise ValueError('Behavioral: нужно не меньше трёх кейсов')
    if behavior['errors'] or behavior['correct'] != behavior['n']:
        raise ValueError(f'Behavioral: прошло {behavior["correct"]} из {behavior["n"]}, ошибок {behavior["errors"]}')
    return dict(passed=True, n=cand['n'],
                accuracy=dict(baseline=base['accuracy'], candidate=cand['accuracy']),
                format_rate=dict(baseline=base['format_rate'], candidate=cand['format_rate']),
                critical_slices={s: dict(n=cand_slices[s]['n'], baseline=base_slices[s]['accuracy'],
                                         candidate=cand_slices[s]['accuracy']) for s in critical_slices},
                behavioral=dict(n=behavior['n'], passed=behavior['correct']))
