"""Независимый offline-анализ witness-v1; только явно переданные данные."""
import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

KINDS = frozenset('login logout die kill blvup jlvup loadmap pos'.split())
FIELDS = frozenset('id ts char_id kind map x y a1 a2'.split())
MATCH_FIELDS = frozenset('char_id kind map x y'.split())
UTC = timezone.utc


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Повтор ключа JSON')
            result[key] = value
        return result

    def constant(_):
        raise ValueError('Недопустимое число JSON')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def instant(value):
    if not isinstance(value, str):
        raise ValueError('Ожидается время ISO 8601')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Нужен явный offset')
    return parsed.astimezone(UTC)


def db_instant(value, zone):
    """Не угадывает offset в повторённом/несуществующем часу DST."""
    if not isinstance(value, str):
        raise ValueError('Некорректный ts')
    local = datetime.fromisoformat(value)
    if local.tzinfo is not None:
        raise ValueError('Контракт требует naive DB ts')
    candidates = set()
    for fold in (0, 1):
        candidate = local.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if candidate.astimezone(zone).replace(tzinfo=None) == local:
            candidates.add(candidate)
    if len(candidates) != 1:
        raise ValueError('Неоднозначное или несуществующее DB время')
    return candidates.pop()


def integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def result(status, code, reason, **extra):
    return dict(status=status, code=code, reason=reason, **extra)


def report(findings, claims):
    statuses = [x['status'] for x in findings + claims]
    status = ('FAILED' if 'FAILED' in statuses else
              'UNVERIFIED' if not statuses or 'UNVERIFIED' in statuses else 'VERIFIED')
    return dict(schema_version=1, status=status, findings=findings, claims=claims,
                scope='Только предоставленные наблюдения; не live-state и не серверная приёмка',
                provenance='Происхождение заявлено поставщиком; криптографически не проверено')


def analyze(events_jsonl, metadata, *, now):
    """Чистая функция: now обязателен, не читает часы, файлы, сеть или БД."""
    findings, claims = [], []

    def add(status, code, reason, **extra):
        findings.append(result(status, code, reason, **extra))

    try:
        current = instant(now)
    except (ValueError, TypeError, OverflowError):
        add('UNVERIFIED', 'evaluation_time', 'Не задано корректное время оценки с offset')
        return report(findings, claims)
    if not isinstance(metadata, dict):
        add('UNVERIFIED', 'metadata', 'Отсутствует объект metadata')
        return report(findings, claims)
    required = {'source_id', 'source_type', 'synthetic', 'db_timezone',
                'window_start', 'window_end', 'collected_at', 'max_age_seconds',
                'id_start', 'id_end', 'claims'}
    if required - metadata.keys():
        add('UNVERIFIED', 'metadata_missing', 'Не заполнены обязательные metadata',
            fields=sorted(required - metadata.keys()))
        return report(findings, claims)
    try:
        if not isinstance(metadata['source_id'], str) or not metadata['source_id'].strip():
            raise ValueError()
        if type(metadata['synthetic']) is not bool:
            raise ValueError()
        age = metadata['max_age_seconds']
        if type(age) not in (int, float) or not math.isfinite(age) or age <= 0:
            raise ValueError()
        lo, hi = metadata['id_start'], metadata['id_end']
        if not integer(lo, 1, 2**63-1) or not integer(hi, lo, 2**63-1):
            raise ValueError()
        start, end, collected = (instant(metadata[k]) for k in
                                 ('window_start', 'window_end', 'collected_at'))
        if not start <= end <= collected <= current:
            raise ValueError()
        if not isinstance(metadata['claims'], list):
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        add('FAILED', 'metadata_invalid', 'Некорректные типы, границы ID или временные границы metadata')
        return report(findings, claims)
    try:
        zone = ZoneInfo(metadata['db_timezone'])
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        add('UNVERIFIED', 'timezone', 'Не задан известный IANA timezone БД; UTC не предполагается')
        return report(findings, claims)
    if metadata['synthetic']:
        add('UNVERIFIED', 'synthetic', 'Synthetic вход не является доказательством реальной игры')
    if metadata['source_type'] != 'rathena_witness':
        add('UNVERIFIED', 'source', 'Требуется свидетель rAthena; ACK команды не доказывает игру')
    if (current - start).total_seconds() > age:
        add('UNVERIFIED', 'stale', 'Начало окна старше допустимого возраста наблюдений')
    rows, seen, previous = {}, {}, None
    if not isinstance(events_jsonl, str):
        add('FAILED', 'input_type', 'Вход events должен быть текстом JSONL')
        return report(findings, claims)
    for line_no, line in enumerate(events_jsonl.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = strict_json(line)
        except (ValueError, TypeError, RecursionError):
            add('FAILED', 'json', 'Некорректный JSON; содержимое скрыто', line=line_no)
            continue
        if not isinstance(row, dict) or not FIELDS <= row.keys():
            add('FAILED', 'event_fields', 'Отсутствуют обязательные ключи witness-v1', line=line_no)
            continue
        valid = (integer(row['id'], 1, 2**63-1) and
                 integer(row['char_id'], 1, 2**31-1) and
                 isinstance(row['kind'], str) and row['kind'] in KINDS and
                 (row['map'] is None or isinstance(row['map'], str) and len(row['map']) <= 16) and
                 all(row[k] is None or integer(row[k], -32768, 32767) for k in ('x', 'y')) and
                 all(row[k] is None or integer(row[k], -2**31, 2**31-1) for k in ('a1', 'a2')))
        if not valid:
            add('FAILED', 'event_schema', 'Некорректное поле/тип или неизвестный kind (ACK не witness)', line=line_no)
            continue
        event_id = row['id']
        if event_id in seen:
            conflict = row != seen[event_id]
            add('FAILED' if conflict else 'UNVERIFIED',
                'duplicate_conflict' if conflict else 'duplicate',
                'Разные payload одного ID' if conflict else 'Повтор ID; не считается новым свидетельством',
                event_id=event_id)
        if previous is not None and event_id < previous:
            add('FAILED', 'id_order', 'Нарушен серверный порядок ID', event_id=event_id)
        previous = event_id
        seen.setdefault(event_id, row)
        if not lo <= event_id <= hi:
            add('FAILED', 'id_bounds', 'Событие вне заявленного интервала ID', event_id=event_id)
        if row.get('synthetic', False) is not False:
            add('UNVERIFIED', 'synthetic_event', 'Событие помечено synthetic', event_id=event_id)
        try:
            observed = db_instant(row['ts'], zone)
        except (ValueError, TypeError, OverflowError):
            add('UNVERIFIED', 'event_time', 'ts нельзя однозначно нормализовать по контракту', event_id=event_id)
            continue
        if not start <= observed <= end:
            add('FAILED', 'event_window', 'Событие вне временного окна metadata', event_id=event_id)
        if 'ts_utc' in row:
            try:
                consistent = instant(row['ts_utc']) == observed
            except (ValueError, TypeError, OverflowError):
                consistent = False
            if not consistent:
                add('FAILED', 'normalized_time_conflict', 'ts_utc противоречит ts и DB timezone', event_id=event_id)
        rows.setdefault(event_id, row)
    if not seen:
        add('UNVERIFIED', 'empty_events', 'Нет допустимых событий; отсутствие не означает успех')
    in_bounds = sorted(k for k in seen if lo <= k <= hi)
    if len(in_bounds) != hi - lo + 1:
        add('UNVERIFIED', 'id_gap', 'Интервал ID неполон; пропуск не доказывает потерю или отсутствие действия')
    if not metadata['claims']:
        add('UNVERIFIED', 'empty_claims', 'Нет явно заявленных результатов для проверки')
    blocked = any(f['status'] != 'VERIFIED' for f in findings)
    for index, claim in enumerate(metadata['claims']):
        # Единственный поддержанный claim — значение полей конкретного witness event.
        # Свободные утверждения, HP, a1/a2 и body actions не интерпретируются.
        if (not isinstance(claim, dict) or set(claim) != {'type', 'event_id', 'expected'} or
                claim.get('type') != 'event' or
                not integer(claim.get('event_id'), lo, hi) or
                not isinstance(claim.get('expected'), dict)):
            claims.append(result('UNVERIFIED', 'claim_unsupported', 'Неподдерживаемая или некорректная заявка', index=index))
            continue
        expected = claim['expected']
        if (not {'kind', 'char_id'} <= expected.keys() or not expected.keys() <= MATCH_FIELDS or
                not isinstance(expected['kind'], str) or expected['kind'] not in KINDS or
                not integer(expected['char_id'], 1, 2**31-1) or
                any(type(v) not in (str, int) for v in expected.values())):
            claims.append(result('UNVERIFIED', 'claim_fields', 'Нужны kind и char_id; HP, ACK и a1/a2 не подтверждаются', index=index))
            continue
        row = rows.get(claim['event_id'])
        if row is None:
            claims.append(result('UNVERIFIED', 'evidence_missing', 'Нет пригодного события с заявленным ID', index=index))
            continue
        missing = [k for k in expected if row[k] is None]
        different = [k for k, v in expected.items() if row[k] is not None and (type(row[k]) is not type(v) or row[k] != v)]
        if different:
            claims.append(result('FAILED', 'contradiction', 'Заявленные поля противоречат событию', index=index, fields=sorted(different)))
        elif missing or blocked:
            claims.append(result('UNVERIFIED', 'evidence_insufficient', 'Совпадения недостаточно: неполные поля или ограничения входа', index=index))
        else:
            claims.append(result('VERIFIED', 'event_matches', 'Заявленные поля совпадают со свежим событием в заданном окне', index=index, event_id=claim['event_id']))
    if not findings:
        add('VERIFIED', 'input_checks', 'Структура, ID, временные границы и заявленное происхождение прошли offline-проверку')
    return report(findings, claims)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--events', required=True, type=Path)
    parser.add_argument('--metadata', required=True, type=Path)
    parser.add_argument('--now', required=True, help='ISO 8601 с offset; явное время оценки')
    args = parser.parse_args(argv)
    try:
        output = analyze(args.events.read_text(encoding='utf-8'),
                         strict_json(args.metadata.read_text(encoding='utf-8')), now=args.now)
    except (OSError, UnicodeError, ValueError, RecursionError):
        output = report([result('FAILED', 'input_read', 'Не удалось прочитать вход; пути и содержимое скрыты')], [])
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return {'VERIFIED': 0, 'FAILED': 1, 'UNVERIFIED': 2}[output['status']]


if __name__ == '__main__':
    raise SystemExit(main())
