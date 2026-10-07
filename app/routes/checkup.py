"""담당 사회복지사의 검진표 판독 및 확인 API."""
import datetime
import os

from flask import Blueprint, current_app, jsonify, request, session, send_file
from sqlalchemy.exc import IntegrityError
from models.models import db, User, Worker, CheckupDocument, CheckupResult
from services.checkup_service import (
    CheckupError, Extraction, extract_document, parse_extraction,
    summarize, validate_extraction,
)
from pydantic import ValidationError

checkup_bp = Blueprint('checkup', __name__)


@checkup_bp.errorhandler(413)
def result_too_large(error):
    return jsonify(success=False, message="확인 결과가 너무 큽니다. 항목 내용을 확인해주세요."), 413


def owned_document(doc_id):
    worker_id = session.get('admin_worker_id')
    if not worker_id and session.get('admin_id'):
        worker = Worker.query.filter_by(login_id=session['admin_id']).first()
        worker_id = worker.worker_id if worker else None
    if not worker_id:
        raise CheckupError("로그인이 필요합니다.", 401)
    doc = db.session.get(CheckupDocument, doc_id)
    if not doc:
        raise CheckupError("문서를 찾을 수 없습니다.", 404)
    user = db.session.get(User, doc.user_id)
    if not user or user.worker_id != worker_id:
        raise CheckupError("담당 어르신의 문서만 사용할 수 있습니다.", 403)
    return doc, user, worker_id


def result_payload(record, user):
    return {
        'success': True, 'doc_id': record.doc_id,
        'analysis': summarize(record.extraction),
        'extraction': record.extraction,
        'validation_issues': validate_extraction(record.extraction, user.name),
        'confirmed_result': record.confirmed_result,
        'revision': record.revision, 'confirmed_revision': record.confirmed_revision,
        'analyzed_at': record.analyzed_at.isoformat(),
        'confirmed_at': record.confirmed_at.isoformat() if record.confirmed_at else None,
        'confirmed_by': record.confirmed_by,
        'identity_verified': record.identity_verified,
    }


@checkup_bp.errorhandler(CheckupError)
def checkup_error(error):
    db.session.rollback()
    return jsonify(success=False, message=str(error)), error.status


@checkup_bp.route('/api/admin/checkup/analyze/<int:doc_id>', methods=['POST'])
def analyze_checkup(doc_id):
    doc, user, _ = owned_document(doc_id)
    # HTTP 대기 중 DB 트랜잭션을 열어두지 않는다.
    expected_worker = user.worker_id
    path = os.path.join(current_app.config['UPLOAD_FOLDER'], os.path.basename(doc.file_path))
    db.session.rollback()
    extraction = extract_document(path)
    # 네트워크 호출 사이 담당자가 바뀌었을 경우 다시 검사한다.
    doc, user, _ = owned_document(doc_id)
    if user.worker_id != expected_worker:
        raise CheckupError("담당자가 변경되었습니다. 다시 확인해주세요.", 409)
    try:
        record = CheckupResult.query.filter_by(doc_id=doc_id).with_for_update().first()
        if record is None:
            record = CheckupResult(doc_id=doc_id, extraction=extraction, revision=1)
            db.session.add(record)
        else:
            record.extraction = extraction
            record.revision += 1
            record.analyzed_at = datetime.datetime.now()
        db.session.commit()
        payload = result_payload(record, user)
        payload['document_name'] = doc.original_name or '건강검진표'
        return jsonify(payload)
    except IntegrityError:
        raise CheckupError("다른 판독 요청이 처리되었습니다. 결과를 다시 열어주세요.", 409)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("검진표 판독 저장 실패")
        raise CheckupError("판독 결과를 저장하지 못했습니다. 다시 시도해주세요.", 500)


@checkup_bp.route('/api/admin/checkup/<int:doc_id>/result', methods=['GET'])
def get_checkup_result(doc_id):
    doc, user, _ = owned_document(doc_id)
    record = CheckupResult.query.filter_by(doc_id=doc_id).first()
    if not record:
        return jsonify(success=True, doc_id=doc_id, extraction=None, confirmed_result=None)
    return jsonify(result_payload(record, user))


@checkup_bp.route('/api/admin/checkup/<int:doc_id>/original', methods=['GET'])
def get_original(doc_id):
    doc, _, _ = owned_document(doc_id)
    path = os.path.join(current_app.config['UPLOAD_FOLDER'], os.path.basename(doc.file_path))
    if not os.path.isfile(path):
        raise CheckupError("문서 원본을 찾을 수 없습니다.", 404)
    return send_file(path, download_name=doc.original_name, conditional=True)


@checkup_bp.route('/api/admin/checkup/<int:doc_id>/confirm', methods=['PUT'])
def confirm_checkup(doc_id):
    _, user, worker_id = owned_document(doc_id)
    request.max_content_length = 1024 * 1024
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise CheckupError("확인할 결과를 JSON으로 보내주세요.")
    record = CheckupResult.query.filter_by(doc_id=doc_id).with_for_update().first()
    if not record:
        raise CheckupError("먼저 검진표를 분석해주세요.", 404)
    if type(body.get('revision')) is not int or body['revision'] != record.revision:
        raise CheckupError("판독 결과가 변경되었습니다. 다시 열어 확인해주세요.", 409)
    try:
        result = Extraction.model_validate(body.get('result')).model_dump()
    except ValidationError:
        raise CheckupError("항목 형식과 페이지 번호를 확인해주세요.")
    if not result['is_checkup'] or not result['items']:
        raise CheckupError("검진 항목이 없는 결과는 확정할 수 없습니다.")
    # 수정 화면은 원본 행을 보존한다. 새 행이나 원문 조작은 허용하지 않는다.
    original = record.extraction['items']
    if len(result['items']) != len(original) or any(
        item['raw_text'] != source['raw_text'] or item['page'] != source['page']
        for item, source in zip(result['items'], original)
    ):
        raise CheckupError("원문과 페이지 번호는 변경할 수 없습니다.")
    for item in result['items']:
        if item['unreadable']:
            item['value'] = None
        elif not item['value'] or not item['value'].strip():
            raise CheckupError("빈 결과는 판독 불가로 표시해주세요.")
    issues = validate_extraction(result, user.name)
    identity_verified = body.get('identity_verified') is True
    if any(issue['code'] in ('missing_name', 'name_mismatch') for issue in issues) and not identity_verified:
        raise CheckupError("문서 이름을 수정하거나 담당 대상 확인을 체크해주세요.")
    if issues and body.get('issues_reviewed') is not True:
        raise CheckupError("표시된 확인 필요 사항을 원본과 대조하고 확인해주세요.")
    record.confirmed_result = result
    record.confirmed_revision = record.revision
    record.confirmed_by = worker_id
    record.confirmed_at = datetime.datetime.now()
    record.identity_verified = identity_verified
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("검진표 확정 저장 실패")
        raise CheckupError("확정 결과를 저장하지 못했습니다.", 500)
    return jsonify(result_payload(record, user))
