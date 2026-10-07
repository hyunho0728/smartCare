"""관측값 비교와 시스템 점수의 설명 자료. 건강 진단이나 새 등급을 만들지 않는다."""
import datetime as dt
import math
import re
from collections import defaultdict

LEVEL_LABELS = {'SAFE': '안전', 'WATCH': '주의', 'WARN': '경고', 'DANGER': '위험'}


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    if not re.fullmatch(r'\d{1,4}(?:\.\d{1,4})?', text):
        return None
    result = float(text)
    return result if math.isfinite(result) else None


def system_status(risk, now):
    """기존 계산값을 보존하되 감점 설명에 포함된 자유 메모/정확한 나이는 복사하지 않는다."""
    breakdown = []
    categories = [('고령', 'age', '고령 감점'), ('기저질환', 'disease', '등록 기저질환 감점'),
                  ('식사 결식', 'meal', '최신 기록 결식'), ('미입력 경과', 'elapsed', '건강 기록 미입력 경과'),
                  ('건강 상태 미등록', 'missing_health', '건강 기록 없음'), ('장기 미접속', 'login_elapsed', '장기 미접속'),
                  ('앱 미접속 경과', 'login_elapsed', '장기 미접속'),
                  ('로그인 기록 없음', 'missing_login', '접속 기록 없음'), ('생활패턴 불규칙', 'irregular', '입력 시간 불규칙'),
                  ('건강 척도 하락', 'condition', '건강 상태 연속 하락'), ('영양 불균형', 'nutrition', '최근 잦은 결식')]
    for item in risk['score_breakdown']:
        if item['type'] != 'minus':
            continue
        category = next(((key, label) for keyword, key, label in categories if keyword in item['item']), None)
        if category is None:
            raise ValueError('설명할 수 없는 점수 산정 항목입니다.')
        match = re.fullmatch(r'-(\d+(?:\.\d+)?)점', item['score'])
        if not match:
            raise ValueError('점수 산정 항목의 형식이 올바르지 않습니다.')
        breakdown.append({'code': category[0], 'item': category[1], 'points': -float(match[1])})
    return {'score': float(risk['score']), 'level': risk['risk_level_db'],
            'label': LEVEL_LABELS[risk['risk_level_db']], 'as_of': now.isoformat(), 'breakdown': breakdown}


def material_signature(snapshot):
    """변하는 조회 시각/기간 표식/AI 문구를 제외한 실제 입력만 비교한다."""
    sources = []
    for source in snapshot.get('sources', []):
        if source['kind'] not in ('생활기록', '접속기록', '등록기저질환', '건강검진'):
            continue
        data = {k: v for k, v in source['data'].items() if k != 'period'}
        sources.append({'kind': source['kind'], 'data': data, 'record_id': source.get('record_id'),
                        'doc_id': source.get('doc_id'), 'item_index': source.get('item_index')})
    return {'sources': sources, 'documents': snapshot.get('documents', []), 'age_band': snapshot.get('age_band')}


def _stats(sources, start, end, login_times):
    daily = {}
    for source in sources:
        if source['kind'] != '생활기록':
            continue
        data = source['data']
        at = dt.datetime.fromisoformat(data['recorded_at'])
        # 마지막 입력을 먼저 고른 뒤 기간에 넣어 날짜별 중복을 제거한다.
        day = source.get('record_date') or at.date().isoformat()
        if day not in daily or data['recorded_at'] >= daily[day]['data']['recorded_at']:
            daily[day] = source
    rows = [s for s in daily.values() if start <= dt.datetime.fromisoformat(s['data']['recorded_at']) < end]
    values = defaultdict(list)
    known_meals = skipped = 0
    for source in rows:
        data = source['data']
        for key in ('condition_level', 'blood_sugar'):
            value = number(data.get(key))
            if value is not None:
                values[key].append(value)
        bp = re.fullmatch(r'\s*(\d{1,3})\s*/\s*(\d{1,3})\s*', str(data.get('blood_pressure') or ''))
        if bp:
            values['systolic'].append(float(bp[1])); values['diastolic'].append(float(bp[2]))
        for meal in data['meals'].values():
            # 예정은 실제 섭취 여부가 확인되지 않으므로 분모에 포함하지 않는다.
            if meal in ('완료', '결식'):
                known_meals += 1
                skipped += meal == '결식'
    dates = set()
    cursor = start.date()
    while cursor <= (end - dt.timedelta(microseconds=1)).date():
        dates.add(cursor.isoformat()); cursor += dt.timedelta(days=1)
    login_days = {at.date().isoformat() for at in login_times if start <= at < end}
    return {'start': start.isoformat(), 'end_exclusive': end.isoformat(), 'records': len(rows),
            'recorded_days': sorted(s.get('record_date') or s['data']['recorded_at'][:10] for s in rows),
            'missing_days': sorted(dates - {s.get('record_date') or s['data']['recorded_at'][:10] for s in rows}),
            'metrics': {key: {'mean': round(sum(values[key]) / len(values[key]), 2) if values[key] else None,
                              'count': len(values[key])} for key in ('condition_level', 'blood_sugar', 'systolic', 'diastolic')},
            'meals': {'skipped': skipped, 'known': known_meals,
                      'skip_percent': round(skipped / known_meals * 100, 2) if known_meals else None},
            'login_days': len(login_days), 'calendar_days': len(dates),
            'login_day_percent': round(len(login_days) / len(dates) * 100, 2) if dates else None,
            'source_refs': [s['ref'] for s in rows]}


def enrich_snapshot(snapshot, status, previous=None, login_times=()):
    """비교값은 요청 시작 시 고정하며 같은 이력을 다시 볼 때 재계산하지 않는다."""
    now = dt.datetime.fromisoformat(snapshot['window_end'])
    boundary = now - dt.timedelta(days=7)
    old = previous.input_snapshot if previous else None
    comparisons = {'life': {'previous': _stats(snapshot['sources'], now - dt.timedelta(days=30), boundary, login_times),
                            'recent': _stats(snapshot['sources'], boundary, now + dt.timedelta(microseconds=1), login_times)},
                   'checkups': [], 'previous_analysis': {'analysis_id': previous.analysis_id if previous else None,
                                                       'available': bool(old), 'same_inputs': False}}
    groups = defaultdict(lambda: defaultdict(list))
    for source in snapshot['sources']:
        if source['kind'] == '건강검진':
            groups[source['data']['name']][source['doc_id']].append(source)
    documents = snapshot['documents']
    if len(documents) == 2:
        recent_doc, old_doc = documents
        for name, by_doc in groups.items():
            latest = by_doc.get(recent_doc['doc_id'], [])
            earlier = by_doc.get(old_doc['doc_id'], [])
            reason = None
            if not latest or not earlier:
                reason = '한쪽 검진에 비교할 항목 없음'
            elif not recent_doc['checkup_date'] or not old_doc['checkup_date']:
                reason = '검진일 미상'
            elif recent_doc['checkup_date'] == old_doc['checkup_date']:
                reason = '동일 검진일: 상충 여부를 원본에서 확인'
            elif len({(s['data']['value'], s['data']['unit']) for s in latest}) > 1 or len({(s['data']['value'], s['data']['unit']) for s in earlier}) > 1:
                reason = '같은 검진의 상충 값'
            elif (not latest[0]['data']['unit'] and name not in ('시력(좌)', '시력(우)')) or (latest[0]['data']['unit'] or '').lower() != (earlier[0]['data']['unit'] or '').lower():
                reason = '단위 미상 또는 불일치: 환산하지 않음'
            elif number(latest[0]['data']['value']) is None or number(earlier[0]['data']['value']) is None:
                reason = '정확한 숫자가 아닌 값: 차이를 계산하지 않음'
            if recent_doc['checkup_date'] and recent_doc['checkup_date'] > now.date().isoformat():
                reason = '미래 검진일: 날짜 확인 필요'
            comparisons['checkups'].append({'name': name,
                'previous': [s['data'] for s in earlier], 'recent': [s['data'] for s in latest],
                'delta': None if reason else round(number(latest[0]['data']['value']) - number(earlier[0]['data']['value']), 4),
                'reason': reason, 'source_refs': [s['ref'] for s in earlier + latest]})
    previous_comparison = comparisons['previous_analysis']
    if old:
        previous_comparison['same_inputs'] = material_signature(snapshot) == material_signature(old)
        old_health = {json_key(s) for s in old.get('sources', []) if s['kind'] == '생활기록'}
        previous_comparison['new_life_records'] = sum(json_key(s) not in old_health for s in snapshot['sources'] if s['kind'] == '생활기록')
        previous_comparison['documents_changed'] = [(d['doc_id'], d['confirmed_revision']) for d in documents] != [(d['doc_id'], d['confirmed_revision']) for d in old.get('documents', [])]
        diseases = lambda s: [x['data'] for x in s.get('sources', []) if x['kind'] == '등록기저질환']
        previous_comparison['diseases_changed'] = diseases(snapshot) != diseases(old)
        previous_comparison['analyzed_at'] = previous.analyzed_at.isoformat()
        old_status = old.get('system_status')
        if old_status:
            before = {b['code']: b for b in old_status['breakdown']}
            after = {b['code']: b for b in status['breakdown']}
            previous_comparison['status'] = {'previous': old_status, 'recent': status,
                'delta': round(status['score'] - old_status['score'], 2),
                'breakdown_changes': [{'item': (after.get(key) or before[key])['item'],
                    'previous': before.get(key, {}).get('points', 0), 'recent': after.get(key, {}).get('points', 0)}
                    for key in sorted(before.keys() | after.keys()) if before.get(key, {}).get('points', 0) != after.get(key, {}).get('points', 0)]}
    snapshot.update(version=2, system_status=status, comparisons=comparisons,
                    previous_analysis_id=previous.analysis_id if previous else None)
    snapshot['limitations'] += ['생활 혈당의 측정 조건은 확인되지 않았습니다. 숫자 증감만으로 호전·악화를 단정하지 않습니다.',
        '미기록은 정상 상태를 뜻하지 않습니다. 식사 예정은 결식 비율의 분모에서 제외합니다.',
        '점수는 높을수록 안전합니다. 검진 수치는 시스템 점수를 변경하지 않습니다.',
        '점수의 0~100 보정으로 감점 항목 변화 합계와 실제 점수 차이가 다를 수 있습니다.']
    # 비교 자료에 자체 참조 ID를 부여한다. 로컬 문서/이력 ID를 외부 payload에 복사하지 않는다.
    def add(kind, data, refs=None):
        snapshot['sources'].append({'ref': f"s{len(snapshot['sources']) + 1}", 'kind': kind, 'data': data,
                                    'source_refs': refs or []})
    add('시스템점수', status)
    add('생활기간비교', {k: {field: value for field, value in stats.items() if field != 'source_refs'} for k, stats in comparisons['life'].items()})
    for comparison in comparisons['checkups']:
        add('검진변화', {k: v for k, v in comparison.items() if k != 'source_refs'}, comparison['source_refs'])
    if old:
        add('직전분석비교', {k: v for k, v in previous_comparison.items() if k != 'analysis_id'})
    return snapshot


def json_key(source):
    import json
    return json.dumps({k: v for k, v in source['data'].items() if k != 'period'}, sort_keys=True, ensure_ascii=False)
