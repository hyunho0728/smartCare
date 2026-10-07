"""조회용 추세와 분석 당시 추세. 관측값을 보간하거나 새 진단을 만들지 않는다."""
import datetime as dt
import re
from collections import defaultdict
from types import SimpleNamespace

from services.health_analysis_service import ITEMS, canonical_item, checkup_date
from services.health_comparison_service import number


def date_range(now, days):
    return [now.date() - dt.timedelta(days=i) for i in range(days - 1, -1, -1)]


def daily_life(rows, now, days):
    dates = date_range(now, days)
    latest = {}
    for row in rows:
        if not row.recorded_at or row.recorded_at > now:
            continue
        date = getattr(row, 'target_date', None) or row.recorded_at.date()
        if date < dates[0] or date > dates[-1]:
            continue
        key = (row.recorded_at, getattr(row, 'status_id', 0) or 0)
        if date not in latest or key > latest[date][0]:
            latest[date] = (key, row)
    result = []
    for date in dates:
        entry = {'date': date.isoformat(), 'record_id': None, 'recorded_at': None,
                 'condition': None, 'systolic': None, 'diastolic': None, 'blood_sugar': None,
                 'skip_percent': None, 'known_meals': 0, 'skipped_meals': 0}
        if date in latest:
            row = latest[date][1]
            condition = number(row.condition_level)
            entry.update(record_id=getattr(row, 'status_id', None), recorded_at=row.recorded_at.isoformat(),
                         condition=condition if condition is not None and 1 <= condition <= 5 else None,
                         blood_sugar=number(row.blood_sugar))
            bp = re.fullmatch(r'\s*(\d{1,3})\s*/\s*(\d{1,3})\s*', str(row.blood_pressure or ''))
            if bp:
                entry.update(systolic=float(bp[1]), diastolic=float(bp[2]))
            meals = [getattr(row, name + '_status') for name in ('breakfast', 'lunch', 'dinner')]
            known = sum(value in ('완료', '결식') for value in meals)
            skipped = meals.count('결식')
            entry.update(known_meals=known, skipped_meals=skipped,
                         skip_percent=round(100 * skipped / known, 2) if known else None)
        result.append(entry)
    return result


def daily_scores(rows, status, now, days):
    dates = date_range(now, days)
    latest = {}
    for row in rows:
        if not row.analyzed_at or row.analyzed_at > now or row.analyzed_at.date() not in dates:
            continue
        date = row.analyzed_at.date()
        key = (row.analyzed_at, row.analysis_id or 0)
        if date not in latest or key > latest[date][0]:
            latest[date] = (key, row)
    result = []
    for date in dates:
        item = {'date': date.isoformat(), 'value': None, 'as_of': None, 'kind': 'missing', 'level': None}
        if date in latest:
            row = latest[date][1]
            item.update(value=float(row.risk_score), as_of=row.analyzed_at.isoformat(), kind='stored', level=row.risk_level)
        if date == now.date() and status:
            item.update(value=status['score'], as_of=status['as_of'], kind='calculated', level=status['level'])
        result.append(item)
    return result


def checkup_series(documents, now, limit=20):
    confirmed = [(doc, record, checkup_date(record.confirmed_result.get('checkup_date')))
                 for doc, record in documents if record.confirmed_result and record.confirmed_result.get('is_checkup')]
    confirmed.sort(key=lambda x: (x[2] or x[0].uploaded_at.date(), x[0].uploaded_at, x[0].doc_id), reverse=True)
    groups = defaultdict(list)
    selected = []
    excluded_count = 0
    for doc, record, date in confirmed[:limit]:
        selected.append({'doc_id': doc.doc_id, 'confirmed_revision': record.confirmed_revision,
                         'checkup_date': date.isoformat() if date else None})
        for index, item in enumerate(record.confirmed_result.get('items', [])):
            name = canonical_item(item.get('name'))
            if not name:
                excluded_count += 1
                continue
            value = item.get('value')
            unit = str(item.get('unit') or '').strip()
            entry = {'date': date.isoformat() if date else None, 'raw_value': str(value) if value is not None else '',
                     'value': number(value), 'unit': unit, 'reference_range': item.get('reference_range') or '',
                     'doc_id': doc.doc_id, 'confirmed_revision': record.confirmed_revision,
                     'page': item.get('page'), 'item_index': index, 'reason': None,
                     'old': bool(date and (now.date() - date).days >= 365)}
            if item.get('unreadable'):
                entry['reason'] = '판독 불가'
            elif not date:
                entry['reason'] = '검진일 미상'
            elif date > now.date():
                entry['reason'] = '미래 검진일 확인 필요'
            elif entry['value'] is None:
                entry['reason'] = '부등호·비수치 또는 누락 값'
            if name == '혈압':
                bp = re.fullmatch(r'\s*(\d{1,3})\s*/\s*(\d{1,3})\s*', str(value or ''))
                if bp:
                    for label, component in [('수축기혈압', bp[1]), ('이완기혈압', bp[2])]:
                        split = dict(entry, raw_value=component, value=float(component))
                        if split['reason'] == '부등호·비수치 또는 누락 값':
                            split['reason'] = None
                        groups[(label, unit)].append(split)
                    continue
            groups[(name, unit)].append(entry)
    units_by_date = defaultdict(set)
    for (name, unit), entries in groups.items():
        for entry in entries:
            if entry['date'] and entry['reason'] != '판독 불가' and entry['raw_value'].strip():
                units_by_date[(name, entry['date'])].add(unit)
    output = []
    for (name, unit), entries in sorted(groups.items(), key=lambda pair: (list(ITEMS).index(pair[0][0]), pair[0][1])):
        by_date = defaultdict(list)
        for entry in entries:
            if entry['date']:
                by_date[entry['date']].append(entry)
        for same_date in by_date.values():
            readable = [entry for entry in same_date if entry['reason'] != '판독 불가' and entry['raw_value'].strip()]
            if len({entry['value'] if entry['value'] is not None else entry['raw_value'] for entry in readable}) > 1:
                for entry in same_date:
                    entry['reason'] = '같은 날짜의 상충 값'
        for entry in entries:
            if entry['reason'] != '판독 불가' and entry['date'] and len(units_by_date[(name, entry['date'])]) > 1:
                entry['reason'] = '같은 날짜의 단위 불일치'
        # 같은 항목의 날짜별 단위가 다른 자료는 별도 계열로 표시한다.
        entries.sort(key=lambda entry: (entry['date'] or '', entry['doc_id'], entry['item_index']))
        output.append({'name': name, 'unit': unit, 'entries': entries})
    return {'series': output, 'documents': selected, 'total_documents': len(confirmed),
            'document_limit': limit, 'unsupported_items': excluded_count}


def build_trends(health, risk, documents, status, now, days=30, document_limit=20):
    dates = date_range(now, days)
    return {'version': 1, 'as_of': now.isoformat(), 'days': days, 'start_date': dates[0].isoformat(),
            'end_date': dates[-1].isoformat(), 'scores_available': True,
            'scores': daily_scores(risk, status, now, days), 'life': daily_life(health, now, days),
            'checkups': checkup_series(documents, now, document_limit),
            'limitations': ['미기록은 정상 상태를 뜻하지 않으며 빈 구간은 연결하지 않습니다.',
                            '혈당 측정 조건은 확인되지 않았습니다. 숫자 증감으로 호전·악화를 판단하지 않습니다.',
                            '기준 날짜의 생활 기록은 해당 기준 시각까지이며 하루 전체 기록이 아닐 수 있습니다.']}


def legacy_trends(snapshot):
    """과거 스냅샷에 실제 남은 자료만 사용한다. 현재 DB로 보충하지 않는다."""
    now = dt.datetime.fromisoformat(snapshot['window_end'])
    health, docs = [], {}
    for doc in snapshot.get('documents', []):
        docs[doc['doc_id']] = {'is_checkup': True, 'checkup_date': doc.get('checkup_date'), 'items': []}
    for source in snapshot.get('sources', []):
        data = source['data']
        if source['kind'] == '생활기록':
            meals = data.get('meals', {})
            health.append(SimpleNamespace(status_id=source.get('record_id') or 0,
                target_date=dt.date.fromisoformat(source.get('record_date') or data['recorded_at'][:10]),
                recorded_at=dt.datetime.fromisoformat(data['recorded_at']), condition_level=data.get('condition_level'),
                blood_pressure=data.get('blood_pressure'), blood_sugar=data.get('blood_sugar'),
                **{name + '_status': meals.get(name) for name in ('breakfast', 'lunch', 'dinner')}))
        elif source['kind'] == '건강검진' and source.get('doc_id') in docs:
            docs[source['doc_id']]['items'].append(dict(data, page=source.get('page')))
    documents = []
    for doc in snapshot.get('documents', []):
        documents.append((SimpleNamespace(doc_id=doc['doc_id'], uploaded_at=dt.datetime.fromisoformat(doc.get('uploaded_at') or snapshot['window_end'])),
                          SimpleNamespace(confirmed_result=docs[doc['doc_id']], confirmed_revision=doc.get('confirmed_revision'))))
    result = build_trends(health, [], documents, None, now, document_limit=2)
    result['scores_available'] = False
    result['scores'] = []
    result['limitations'].append('이전 이력은 저장된 생활·검진 항목만 표시합니다. 당시 제외된 자료는 복원하지 않습니다.')
    return result
