"""실제 DB/API 키 없이 검진표 파이프라인의 실패·보존·권한을 검사한다."""
import copy
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from PIL import Image
from pypdf import PdfWriter
from flask import Flask
from models.models import db, Worker, User, CheckupDocument, CheckupResult
from routes.checkup import checkup_bp
from routes.user import user_bp
from services.checkup_service import (
    CheckupError, MAX_BYTES, prepare_document, parse_extraction, validate_extraction,
)


def result(name='가상김어르신'):
    return {'is_checkup': True, 'patient_name': name, 'checkup_date': '2026-09-01',
            'institution': '가상검진센터', 'items': [
                {'name': '공복혈당', 'value': '98', 'unit': 'mg/dL', 'reference_range': '70~99',
                 'raw_text': '공복혈당 98 mg/dL 70~99', 'page': 1, 'unreadable': False},
                {'name': '시력(좌)', 'value': '0.7', 'unit': None, 'reference_range': None,
                 'raw_text': '시력(좌) 0.7', 'page': 1, 'unreadable': False},
            ]}


class DocumentTests(unittest.TestCase):
    def image(self, fmt='JPEG', exif=None):
        output = io.BytesIO()
        options = {'exif': exif} if exif else {}
        Image.new('RGB', (80, 40), 'white').save(output, format=fmt, **options)
        return output.getvalue()

    def pdf(self, pages=1, encrypted=False):
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=100, height=100)
        if encrypted:
            writer.encrypt('password')
        output = io.BytesIO(); writer.write(output)
        return output.getvalue()

    def test_supported_formats(self):
        for fmt, ext in [('JPEG', '.jpg'), ('PNG', '.png'), ('WEBP', '.webp')]:
            data, mime, suffix, pages = prepare_document(self.image(fmt), 'file' + ext)
            self.assertEqual((mime, suffix, pages), ('image/png', '.png', 1))
            self.assertEqual(Image.open(io.BytesIO(data)).size, (80, 40))
        self.assertEqual(prepare_document(self.pdf(10), 'a.pdf')[3], 10)

    def test_rotation(self):
        exif = Image.Exif(); exif[274] = 6
        data = prepare_document(self.image(exif=exif), 'a.jpg')[0]
        self.assertEqual(Image.open(io.BytesIO(data)).size, (40, 80))

    def test_reject_bad_files(self):
        cases = [(b'', 'a.jpg'), (b'bad', 'a.pdf'), (b'bad', 'a.jpg'),
                 (self.image(), 'a.png'), (self.image(), 'a.heic'),
                 (self.pdf(11), 'a.pdf'), (self.pdf(encrypted=True), 'a.pdf'),
                 (b'x' * (MAX_BYTES + 1), 'a.jpg'), (b'%PDF-1.7\nbroken', 'a.pdf')]
        for data, filename in cases:
            with self.subTest(filename=filename, size=len(data)), self.assertRaises(CheckupError):
                prepare_document(data, filename)

    def test_parser(self):
        self.assertEqual(parse_extraction(json.dumps(result()))['items'][1]['value'], '0.7')
        for payload in ['invalid', '{}', json.dumps({**result(), 'is_checkup': False}),
                        json.dumps({**result(), 'items': []})]:
            with self.assertRaises(CheckupError): parse_extraction(payload)

    def test_validation(self):
        data = result('다른이름'); data['checkup_date'] = '2026-99-01'
        data['items'][0]['value'] = '98..5'; data['items'][0]['unit'] = 'kg'
        data['items'].append({**data['items'][0], 'value': '100'})
        codes = {item['code'] for item in validate_extraction(data, '가상김어르신')}
        self.assertTrue({'name_mismatch', 'invalid_date', 'invalid_number', 'unexpected_unit', 'conflicting_values'} <= codes)

    def test_missing_and_inequality(self):
        data = result(); data['items'][0]['value'] = '≥100'
        self.assertFalse(validate_extraction(data, data['patient_name']))
        data['items'][0]['value'] = None; data['items'][0]['unreadable'] = True
        self.assertIn('unreadable', {i['code'] for i in validate_extraction(data, data['patient_name'])})

    def test_missing_api_key(self):
        from services.checkup_service import extract_document
        with patch.dict(os.environ, {'GEMINI_API_KEY': ''}), self.assertRaises(CheckupError) as error:
            extract_document('unused')
        self.assertEqual(error.exception.status, 503)

    def test_sdk_request_and_response_contract(self):
        from services.checkup_service import extract_document
        from unittest.mock import MagicMock
        client = MagicMock()
        client.__enter__.return_value = client
        client.models.generate_content.return_value.text = json.dumps(result())
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'fake-test-key'}), patch('services.checkup_service.genai.Client', return_value=client):
            extracted = extract_document(str(Path(__file__).parent / 'fixtures' / 'checkups' / 'normal.pdf'))
        self.assertEqual(extracted, result())
        config = client.models.generate_content.call_args.kwargs['config']
        self.assertIsNone(config.response_schema.additional_properties)
        self.assertEqual(config.response_mime_type, 'application/json')

    def test_provider_failures(self):
        from services.checkup_service import extract_document
        from unittest.mock import MagicMock
        client = MagicMock(); client.__enter__.return_value = client
        for code, expected in [(429, 429), (403, 503), (503, 502), (None, 502)]:
            error = RuntimeError('provider failure'); error.code = code
            client.models.generate_content.side_effect = error
            with patch.dict(os.environ, {'GEMINI_API_KEY': 'fake-test-key'}), patch('services.checkup_service.genai.Client', return_value=client):
                with self.assertRaises(CheckupError) as raised:
                    extract_document(str(Path(__file__).parent / 'fixtures' / 'checkups' / 'normal.pdf'))
            self.assertEqual(raised.exception.status, expected)

    def test_invalid_response_and_page(self):
        from services.checkup_service import extract_document
        from unittest.mock import MagicMock
        client = MagicMock(); client.__enter__.return_value = client
        bad_page = result(); bad_page['items'][0]['page'] = 2
        for payload in ['not json', json.dumps(bad_page)]:
            client.models.generate_content.return_value.text = payload
            with patch.dict(os.environ, {'GEMINI_API_KEY': 'fake-test-key'}), patch('services.checkup_service.genai.Client', return_value=client):
                with self.assertRaises(CheckupError):
                    extract_document(str(Path(__file__).parent / 'fixtures' / 'checkups' / 'normal.pdf'))

    def test_all_generated_documents_validate(self):
        directory = Path(__file__).parent / 'fixtures' / 'checkups'
        for name in ('normal', 'abnormal', 'missing'):
            for suffix in ('.pdf', '.png', '.jpg', '_rotated.jpg', '_blurred.jpg'):
                path = directory / (name + suffix)
                with self.subTest(file=path.name): prepare_document(path.read_bytes(), path.name)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI='sqlite://',
                               UPLOAD_FOLDER=str(Path(__file__).parent / 'fixtures' / 'checkups'))
        db.init_app(self.app); self.app.register_blueprint(checkup_bp); self.app.register_blueprint(user_bp)
        self.context = self.app.app_context(); self.context.push(); db.create_all()
        worker = Worker(worker_id=1, login_id='one', password='p', name='복지사', phone_number='0101', address='a')
        other = Worker(worker_id=2, login_id='two', password='p', name='다른복지사', phone_number='0102', address='a')
        user = User(user_id=1, worker_id=1, name='가상김어르신', age=75, phone_number='0103', address='a')
        doc = CheckupDocument(doc_id=1, user_id=1, file_path='/static/uploads/checkups/normal.pdf', original_name='normal.pdf')
        db.session.add_all([worker, other, user, doc]); db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session: session['admin_worker_id'] = 1

    def tearDown(self):
        db.session.remove(); db.engine.dispose(); self.context.pop()

    def analyze(self, data=None):
        with patch('routes.checkup.extract_document', return_value=data or result()):
            return self.client.post('/api/admin/checkup/analyze/1')

    def confirm(self, data=None, **overrides):
        body = {'revision': 1, 'result': data or result(), 'identity_verified': False, 'issues_reviewed': True}
        body.update(overrides)
        return self.client.put('/api/admin/checkup/1/confirm', json=body)

    def test_no_result_and_missing_document(self):
        self.assertIsNone(self.client.get('/api/admin/checkup/1/result').json['extraction'])
        self.assertEqual(self.client.get('/api/admin/checkup/999/result').status_code, 404)
        self.assertEqual(self.confirm().status_code, 404)

    def test_unauthorized(self):
        for worker_id, expected in [(2, 403), (None, 401)]:
            with self.client.session_transaction() as session:
                session.clear()
                if worker_id: session['admin_worker_id'] = worker_id
            for method, url in [('post', '/api/admin/checkup/analyze/1'), ('get', '/api/admin/checkup/1/result'),
                                ('put', '/api/admin/checkup/1/confirm'), ('get', '/api/admin/checkup/1/original')]:
                with self.subTest(url=url, worker=worker_id):
                    self.assertEqual(getattr(self.client, method)(url).status_code, expected)

    def test_save_reload_and_preserve_confirmation(self):
        self.assertEqual(self.analyze().status_code, 200)
        edited = result(); edited['items'][0]['value'] = '99'
        saved = self.confirm(edited).json
        self.assertEqual(saved['confirmed_result']['items'][0]['value'], '99')
        self.assertEqual(saved['confirmed_by'], 1); self.assertIsNotNone(saved['confirmed_at'])
        self.assertEqual(self.client.get('/api/admin/checkup/1/result').json['confirmed_result'], edited)
        new = result(); new['items'][0]['value'] = '110'
        response = self.analyze(new).json
        self.assertEqual(response['revision'], 2)
        self.assertEqual(response['confirmed_revision'], 1)
        self.assertEqual(response['confirmed_result'], edited)
        self.assertEqual(self.confirm().status_code, 409)

    def test_name_mismatch_requires_confirmation(self):
        data = result('다른이름'); self.analyze(data)
        self.assertEqual(self.confirm(data).status_code, 400)
        self.assertEqual(self.confirm(data, identity_verified=True).status_code, 200)

    def test_unreadable_and_review(self):
        self.analyze()
        data = result(); data['items'][0]['value'] = None
        self.assertEqual(self.confirm(data).status_code, 400)
        data['items'][0]['unreadable'] = True
        self.assertEqual(self.confirm(data, issues_reviewed=False).status_code, 400)
        self.assertEqual(self.confirm(data).status_code, 200)

    def test_source_and_schema_protected(self):
        self.analyze()
        for field, value in [('raw_text', 'changed'), ('page', 2), ('name', '')]:
            data = result(); data['items'][0][field] = value
            self.assertEqual(self.confirm(data).status_code, 400)

    def test_analysis_failure_preserves_saved_data(self):
        self.analyze(); self.confirm()
        for code in (422, 502, 503):
            with patch('routes.checkup.extract_document', side_effect=CheckupError('failed', code)):
                self.assertEqual(self.client.post('/api/admin/checkup/analyze/1').status_code, code)
            saved = self.client.get('/api/admin/checkup/1/result').json
            self.assertEqual(saved['revision'], 1); self.assertEqual(saved['confirmed_result'], result())

    def test_upload_rejects_without_writing(self):
        with self.client.session_transaction() as session: session['user_id'] = 1
        response = self.client.post('/api/user/checkup/upload', data={'file': (io.BytesIO(b'bad'), 'bad.jpg')})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(CheckupDocument.query.count(), 1)

    def test_oversized_upload_json_error(self):
        with self.client.session_transaction() as session: session['user_id'] = 1
        response = self.client.post('/api/user/checkup/upload', data=b'x', content_type='multipart/form-data; boundary=test',
                                    environ_overrides={'CONTENT_LENGTH': str(MAX_BYTES + 1024 * 1024 + 1)})
        self.assertEqual(response.status_code, 413)
        self.assertFalse(response.json['success'])


if __name__ == '__main__': unittest.main()
