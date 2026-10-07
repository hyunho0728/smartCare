"""가상 샘플의 실제 Gemini 추출 정확도 평가. --live 없이는 API를 호출하지 않는다."""
import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from services.checkup_service import CheckupError, extract_document


def normalized(value):
    return re.sub(r'[\s()（）·_\-]', '', str(value or '')).lower().replace('왼쪽', '좌').replace('오른쪽', '우')


def score(expected, actual):
    matches, total = 0, 0
    missing, incorrect = [], []
    for key in ('patient_name', 'checkup_date', 'institution'):
        total += 1
        if normalized(expected[key]) == normalized(actual.get(key)): matches += 1
        else: incorrect.append(key)
    actual_items = {normalized(item['name']): item for item in actual['items']}
    for item in expected['items']:
        candidate = actual_items.get(normalized(item['name']))
        total += 3  # name, value, unit. 참고범위는 별도 원문 대조 대상.
        if candidate is None:
            missing.append(item['name']); continue
        matches += 1
        for key in ('value', 'unit'):
            if normalized(candidate.get(key)) == normalized(item[key]): matches += 1
            else: incorrect.append(f"{item['name']}.{key}")
    expected_names = {normalized(item['name']) for item in expected['items']}
    extra = [item['name'] for item in actual['items'] if normalized(item['name']) not in expected_names]
    return {'matched_fields': matches, 'total_fields': total, 'accuracy': round(matches / total, 4),
            'missing_items': missing, 'incorrect_fields': incorrect, 'extra_items': extra}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--limit', type=int, default=15)
    parser.add_argument('--interval', type=float, default=13, help='무료 요청 한도를 고려한 호출 간격(초)')
    parser.add_argument('--retry-failures', action='store_true', help='기존 평가의 실패 항목만 다시 시도')
    parser.add_argument('--output', default=str(ROOT / 'tests' / 'fixtures' / 'checkups' / 'evaluation.json'))
    args = parser.parse_args()
    if not args.live:
        parser.error('실제 API 평가는 --live를 명시해주세요. 비용이 발생할 수 있습니다.')
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    directory = ROOT / 'tests' / 'fixtures' / 'checkups'
    expected = json.loads((directory / 'expected.json').read_text(encoding='utf-8'))
    files = [(key, directory / f'{key}{suffix}') for suffix in ('.pdf', '.png', '.jpg', '_rotated.jpg', '_blurred.jpg') for key in expected]
    report = {'evaluation_type': 'live_gemini', 'cases': [], 'clean_target': .95}
    if args.retry_failures and Path(args.output).exists():
        report = json.loads(Path(args.output).read_text(encoding='utf-8'))
        successful = {case['file'] for case in report['cases'] if 'accuracy' in case}
        files = [(key, path) for key, path in files if path.name not in successful]
    for key, path in files[:args.limit]:
        case = {'file': path.name}
        try:
            actual = extract_document(str(path))
            case.update(score(expected[key], actual)); case['extraction'] = actual
        except CheckupError as error:
            case['error'] = str(error); case['status'] = error.status
            case['cause_type'] = type(error.__cause__).__name__ if error.__cause__ else None
            case['provider_status'] = getattr(error.__cause__, 'code', None)
            details = getattr(error.__cause__, 'details', {})
            if isinstance(details, dict):
                violations = [violation for entry in details.get('error', {}).get('details', [])
                              for violation in entry.get('violations', [])]
                case['quota_ids'] = [violation.get('quotaId') for violation in violations if violation.get('quotaId')]
        report['cases'] = [previous for previous in report['cases'] if previous['file'] != path.name] + [case]
        clean = [case for case in report['cases'] if not any(tag in case['file'] for tag in ('rotated', 'blurred'))]
        passed = [case for case in clean if 'accuracy' in case]
        total = sum(case['total_fields'] for case in passed)
        report['summary'] = {'successful_documents': sum('accuracy' in case for case in report['cases']),
                             'failed_documents': sum('error' in case for case in report['cases']),
                             'clean_evaluated_documents': len(passed),
                             'clean_field_accuracy': round(sum(case['matched_fields'] for case in passed) / total, 4) if total else None}
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(path.name, case.get('accuracy', case.get('error')), flush=True)
        if case.get('status') == 503: break
        if case.get('provider_status') in (400, 429): break
        time.sleep(max(0, args.interval))


if __name__ == '__main__': main()
