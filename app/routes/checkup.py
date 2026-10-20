"""담당 사회복지사의 검진표 판독 및 확인 API."""
import datetime
import os

from flask import Blueprint, current_app, jsonify, request, session, send_file
from sqlalchemy.exc import IntegrityError
from models.models import db, User, Worker, CheckupDocument, CheckupResult
from services.checkup_service import (
    CheckupError, Extraction, extract_document, parse_extraction,
    summarize, validate_extraction, merge_diseases, normalize_disease_names,
)
from pydantic import ValidationError
from services.ai_service import resolve_model, AIError

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


def _checkup_date(value):
    try:
        return datetime.date.fromisoformat(str(value or '').strip())
    except ValueError:
        return None


def disease_sync_status(record, user, extraction=None, lock=False):
    extraction = extraction or Extraction.model_validate(record.extraction).model_dump()
    checkup_date = _checkup_date(extraction.get('checkup_date'))
    if not extraction.get('diseases'):
        return {'eligible': False, 'reason': '검진표에서 기저질환 후보를 찾지 못해 기존 정보를 유지합니다.',
                'checkup_date': checkup_date.isoformat() if checkup_date else None,
                'latest_confirmed_checkup_date': None}
    if not checkup_date:
        return {'eligible': False, 'reason': '유효한 검진일이 없어 기존 기저질환 정보를 유지합니다.',
                'checkup_date': None, 'latest_confirmed_checkup_date': None}

    query = db.session.query(CheckupResult).join(
        CheckupDocument, CheckupDocument.doc_id == CheckupResult.doc_id
    ).filter(
        CheckupDocument.user_id == user.user_id,
        CheckupResult.result_id != record.result_id,
        CheckupResult.confirmed_result.isnot(None),
    )
    if lock:
        query = query.with_for_update()
    dates = [
        parsed for other in query.all()
        if (parsed := _checkup_date((other.confirmed_result or {}).get('checkup_date')))
    ]
    latest = max(dates) if dates else None
    if latest and latest > checkup_date:
        return {'eligible': False,
                'reason': f'{latest.isoformat()} 검진표가 더 최신이므로 기존 기저질환 정보를 유지합니다.',
                'checkup_date': checkup_date.isoformat(),
                'latest_confirmed_checkup_date': latest.isoformat()}
    return {'eligible': True, 'reason': '최신 검진표의 선택 질환으로 사용자 정보를 교체할 수 있습니다.',
            'checkup_date': checkup_date.isoformat(),
            'latest_confirmed_checkup_date': checkup_date.isoformat()}


def result_payload(record, user):
    extraction = Extraction.model_validate(record.extraction).model_dump()
    confirmed = (Extraction.model_validate(record.confirmed_result).model_dump()
                 if record.confirmed_result else None)
    return {
        'success': True, 'doc_id': record.doc_id,
        'current_disease': user.note or ('기저질환 있음' if user.has_underlying_disease else '없음'),
        'analysis': summarize(extraction),
        'extraction': extraction,
        'validation_issues': validate_extraction(extraction, user.name),
        'confirmed_result': confirmed,
        'disease_sync': disease_sync_status(record, user, extraction),
        'revision': record.revision, 'confirmed_revision': record.confirmed_revision,
        'analyzed_at': record.analyzed_at.isoformat(),
        'confirmed_at': record.confirmed_at.isoformat() if record.confirmed_at else None,
        'confirmed_by': record.confirmed_by,
        'identity_verified': record.identity_verified,
        'extraction_model': record.extraction_model, 'confirmed_model': record.confirmed_model,
    }


@checkup_bp.errorhandler(CheckupError)
def checkup_error(error):
    db.session.rollback()
    return jsonify(success=False, message=str(error)), error.status


@checkup_bp.route('/api/admin/checkup/analyze/<int:doc_id>', methods=['POST'])
def analyze_checkup(doc_id):
    doc, user, _ = owned_document(doc_id)
    body = request.get_json(silent=True)
    if request.data and not isinstance(body, dict):
        raise CheckupError('요청 형식을 확인해주세요.', 400)
    if isinstance(body, dict) and 'model' in body and body['model'] is None:
        raise CheckupError('모델을 선택해주세요.', 400)
    try:
        model = resolve_model(body.get('model') if body else None, os.getenv('GEMINI_CHECKUP_MODEL', 'gemini-3.6-flash'))
    except AIError as error:
        raise CheckupError(str(error), error.status) from error
    # HTTP 대기 중 DB 트랜잭션을 열어두지 않는다.
    expected_worker = user.worker_id
    path = os.path.join(current_app.config['UPLOAD_FOLDER'], os.path.basename(doc.file_path))
    db.session.rollback()
    extraction = extract_document(path, model=model)
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
        record.extraction_model = model
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
        return jsonify(success=True, doc_id=doc_id, extraction=None, confirmed_result=None,
                       current_disease=user.note or ('기저질환 있음' if user.has_underlying_disease else '없음'),
                       disease_sync={'eligible': False, 'reason': '먼저 검진표를 분석해주세요.',
                                     'checkup_date': None, 'latest_confirmed_checkup_date': None})
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
    user = User.query.filter_by(user_id=user.user_id).populate_existing().with_for_update().one()
    if user.worker_id != worker_id:
        raise CheckupError("담당자가 변경되었습니다. 다시 확인해주세요.", 409)
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
    original_diseases = Extraction.model_validate(record.extraction).model_dump()['diseases']
    if len(result['diseases']) != len(original_diseases) or any(
        item['raw_text'] != source['raw_text'] or item['page'] != source['page'] or
        item['status'] != source['status']
        for item, source in zip(result['diseases'], original_diseases)
    ):
        raise CheckupError("질환 상태, 근거 원문과 페이지 번호는 변경할 수 없습니다.")

    def selected_indices(value, message):
        if not isinstance(value, list) or len(value) > len(original_diseases) or any(
            type(index) is not int or not 0 <= index < len(original_diseases) for index in value
        ) or len(set(value)) != len(value):
            raise CheckupError(message)
        return value

    legacy_selected = selected_indices(body.get('selected_disease_indices', []), "추가할 질환 선택을 확인해주세요.")
    disease_update = body.get('disease_update')
    replace_selected = []
    if disease_update is not None:
        if not isinstance(disease_update, dict) or set(disease_update) != {'mode', 'selected_indices'} or \
                disease_update.get('mode') != 'replace':
            raise CheckupError("기저질환 최신화 요청을 확인해주세요.")
        replace_selected = selected_indices(disease_update.get('selected_indices'), "교체할 질환 선택을 확인해주세요.")
        if legacy_selected:
            raise CheckupError("기저질환 추가와 교체를 동시에 요청할 수 없습니다.")
    for disease in result['diseases']:
        disease['name'] = disease['name'].strip()
        if not disease['name']:
            raise CheckupError("질환명을 입력해주세요.")
    for item in result['items']:
        if item['unreadable']:
            item['value'] = None
        elif not item['value'] or not item['value'].strip():
            raise CheckupError("빈 결과는 판독 불가로 표시해주세요.")
    issues = validate_extraction(result, user.name)
    name_issue = next((issue for issue in issues if issue['code'] in ('missing_name', 'name_mismatch')), None)
    if name_issue:
        raise CheckupError("문서 이름을 담당 어르신 이름과 동일하게 수정해주세요.")
    if issues and body.get('issues_reviewed') is not True:
        raise CheckupError("표시된 확인 필요 사항을 원본과 대조하고 확인해주세요.")
    # 같은 사용자의 여러 검진표가 동시에 확정돼도 최신 검진표만 정보를 바꾼다.
    sync = disease_sync_status(record, user, result, lock=True)
    disease_updated = False
    if replace_selected and sync['eligible']:
        names = normalize_disease_names([result['diseases'][i]['name'] for i in replace_selected])
        if names:
            user.note = ', '.join(names)
            user.has_underlying_disease = True
            disease_updated = True
    elif legacy_selected:
        user.note, _ = merge_diseases(user.note, [result['diseases'][i]['name'] for i in legacy_selected])
        user.has_underlying_disease = True
        disease_updated = True
    record.confirmed_result = result
    record.confirmed_revision = record.revision
    record.confirmed_by = worker_id
    record.confirmed_at = datetime.datetime.now()
    record.confirmed_model = record.extraction_model
    record.identity_verified = True
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("검진표 확정 저장 실패")
        raise CheckupError("확정 결과를 저장하지 못했습니다.", 500)
    payload = result_payload(record, user)
    payload['disease_sync']['applied'] = disease_updated
    return jsonify(payload)
