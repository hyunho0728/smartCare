"""가상의 일반검진표 3종과 촬영 변형을 재현한다 (실제 개인정보 없음).

실행: .venv/Scripts/python.exe tests/fixtures/checkups/generate_samples.py
추가 제작 의존성: reportlab. PDF 렌더 검증: pymupdf.
"""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

ROOT = Path(__file__).resolve().parent
FONT = Path('C:/Windows/Fonts/malgun.ttf')
ROWS = [
    ('신장', '162.0', 'cm', '-'), ('체중', '58.5', 'kg', '-'),
    ('허리둘레', '78.0', 'cm', '-'), ('BMI', '22.3', 'kg/m²', '18.5~24.9'),
    ('수축기혈압', '118', 'mmHg', '<120'), ('이완기혈압', '76', 'mmHg', '<80'),
    ('시력(좌)', '0.7', '-', '-'), ('시력(우)', '0.9', '-', '-'),
    ('청력(좌)', '정상', '-', '정상'), ('청력(우)', '정상', '-', '정상'),
    ('혈색소', '13.2', 'g/dL', '12.0~15.5'), ('공복혈당', '94', 'mg/dL', '70~99'),
    ('총콜레스테롤', '185', 'mg/dL', '<200'), ('HDL콜레스테롤', '56', 'mg/dL', '≥40'),
    ('LDL콜레스테롤', '104', 'mg/dL', '<130'), ('중성지방', '118', 'mg/dL', '<150'),
    ('AST', '24', 'U/L', '≤40'), ('ALT', '20', 'U/L', '≤35'),
    ('감마GTP', '22', 'U/L', '8~35'), ('크레아티닌', '0.8', 'mg/dL', '0.5~1.1'),
    ('eGFR', '78', 'mL/min/1.73m²', '≥60'), ('요단백', '음성', '-', '음성'),
]
SCENARIOS = [
    ('normal', '가상김어르신', '1951-03-12', {}),
    ('abnormal', '가상이어르신', '1948-07-09', {
        '수축기혈압': '152', '이완기혈압': '94', '공복혈당': '≥126',
        '총콜레스테롤': '248', 'LDL콜레스테롤': '167', '중성지방': '215',
        'AST': '52', 'ALT': '48', '크레아티닌': '1.3', 'eGFR': '48', '요단백': '양성(1+)',
    }),
    ('missing', '가상박어르신', '1945-11-23', {'HDL콜레스테롤': None, 'LDL콜레스테롤': None, '감마GTP': None}),
]


def generate():
    if not FONT.exists():
        raise RuntimeError('맑은 고딕 폰트 경로를 설정해주세요.')
    pdfmetrics.registerFont(TTFont('Korean', str(FONT)))
    font = ImageFont.truetype(str(FONT), 23)
    heading = ImageFont.truetype(str(FONT), 36)
    truth = {}
    for key, name, birth, changes in SCENARIOS:
        rows = [(n, changes.get(n, v), u, r) for n, v, u, r in ROWS if changes.get(n, v) is not None]
        image = Image.new('RGB', (1240, 1754), 'white')
        draw = ImageDraw.Draw(image)
        pdf = canvas.Canvas(str(ROOT / f'{key}.pdf'), pagesize=(620, 877))
        def text(x, y, value, title=False):
            draw.text((x, y), value, fill='#17212b', font=heading if title else font)
            pdf.setFont('Korean', 18 if title else 11.5)
            pdf.drawString(x / 2, 877 - y / 2 - (18 if title else 11.5), value)
        def line(x1, y1, x2, y2):
            draw.line((x1, y1, x2, y2), fill='#64748b', width=2)
            pdf.setStrokeColorRGB(.4, .45, .5); pdf.setLineWidth(.7)
            pdf.line(x1 / 2, 877 - y1 / 2, x2 / 2, 877 - y2 / 2)
        text(65, 50, '일반건강검진 결과통보서', True)
        text(65, 110, '테스트 전용 가상 자료 - 실제 검진 결과가 아닙니다')
        text(65, 170, f'성명: {name}     생년월일: {birth}     성별: 여')
        text(65, 215, '검진일: 2026-09-01     검진기관: 가상검진센터')
        text(65, 265, '검사 결과 (참고범위는 테스트용 예시이며 개인별 진단 기준이 아님)')
        xs = [65, 425, 610, 870, 1175]
        line(xs[0], 320, xs[-1], 320)
        for x, label in zip(xs, ['검사 항목', '결과', '단위', '참고범위']): text(x + 10, 330, label)
        line(xs[0], 375, xs[-1], 375)
        for index, row in enumerate(rows):
            y = 390 + index * 49
            for x, value in zip(xs, row): text(x + 10, y, value)
            line(xs[0], y + 38, xs[-1], y + 38)
        for x in xs: line(x, 320, x, 390 + (len(rows)-1)*49 + 38)
        text(65, 1550, '판정 및 소견: 원본 수치 대조용 샘플. 실제 질환을 진단하지 않습니다.')
        text(65, 1610, '가상검진센터 / 문의번호 없음 / 개인정보는 모두 가상')
        text(1100, 1680, '1 / 1')
        pdf.showPage(); pdf.save()
        image.save(ROOT / f'{key}.png')
        image.save(ROOT / f'{key}.jpg', quality=92)
        image.rotate(90, expand=True).save(ROOT / f'{key}_rotated.jpg', quality=90)
        image.filter(ImageFilter.GaussianBlur(1.5)).save(ROOT / f'{key}_blurred.jpg', quality=80)
        truth[key] = {
            'is_checkup': True, 'patient_name': name, 'checkup_date': '2026-09-01', 'institution': '가상검진센터',
            'items': [{'name': n, 'value': v, 'unit': None if u == '-' else u,
                       'reference_range': None if r == '-' else r,
                       'raw_text': f'{n} {v} {u} {r}', 'page': 1, 'unreadable': False} for n, v, u, r in rows],
        }
    (ROOT / 'expected.json').write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Generated 3 synthetic cases, 15 documents and expected.json')


if __name__ == '__main__': generate()
