from flask import Blueprint, jsonify
from services.auth_service import current_role
from services.ai_service import AIError, model_catalog, resolve_model, usage_summary

ai_bp = Blueprint('ai', __name__)


@ai_bp.get('/api/admin/ai/models')
def models():
    if current_role() != 'worker':
        return jsonify(success=False, message='사회복지사 로그인이 필요합니다.'), 401
    try:
        return jsonify(success=True, models=model_catalog(), default_model=resolve_model())
    except AIError as error:
        return jsonify(success=False, message=str(error)), error.status


@ai_bp.get('/api/admin/ai/usage')
def usage():
    if current_role() != 'worker':
        return jsonify(success=False, message='사회복지사 로그인이 필요합니다.'), 401
    try:
        return jsonify(success=True, **usage_summary())
    except AIError as error:
        return jsonify(success=False, message=str(error)), error.status
    except Exception:
        return jsonify(success=False, message='사용량을 불러오지 못했습니다. 다시 시도해주세요.'), 503
