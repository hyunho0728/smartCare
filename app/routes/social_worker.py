import os
import datetime
from flask import Blueprint, render_template, request, jsonify, session, current_app
from models.models import db, Worker, User, HealthStatus, LoginHistory, RiskAnalysis, PostManagement, CheckupDocument, CheckupResult, HealthAnalysis, EmergencyAlert
from services.social_worker_ai_service import (
    evaluate_and_record_risk,
    calculate_risk,
)
from services.auth_service import select_role, current_role, role_redirect

from services.health_analysis_service import build_input, analyze, summarize, MODEL, HealthAnalysisError
from services.ai_service import resolve_model, AIError
from services.health_comparison_service import system_status, enrich_snapshot, material_signature
from services.health_trend_service import build_trends, legacy_trends

worker_bp = Blueprint('worker', __name__)

# --- 내부 유틸 함수 ---
def extract_numbers(text):
    import re
    if not text:
        return ""
    return re.sub(r'\D', '', str(text))

def format_phone_display(phone_str):
    if not phone_str:
        return "-"
    p = str(phone_str)
    if len(p) == 11:
        return f"{p[:3]}-{p[3:7]}-{p[7:]}"
    elif len(p) == 10:
        return f"{p[:3]}-{p[3:6]}-{p[6:]}"
    return p

def generate_svg_chart_points(scores_7days):
    x_coords = [0, 112, 224, 336, 448, 560, 650]
    while len(scores_7days) < 7:
        scores_7days.insert(0, scores_7days[0] if scores_7days else 100)
    scores_7days = scores_7days[-7:]
    
    points = []
    for x, s in zip(x_coords, scores_7days):
        y = int(170 - (float(s) / 100.0) * 140)
        points.append(f"{x},{y}")
    return " ".join(points)

def build_daily_risk_scores(risk_records, today=None):
    """최근 7개 분석 레코드가 아니라 최근 7일의 날짜별 최신 위험 점수를 만듭니다."""
    today = today or datetime.datetime.now().date()
    target_dates = [today - datetime.timedelta(days=offset) for offset in range(6, -1, -1)]
    target_date_set = set(target_dates)
    latest_by_date = {}

    for record in risk_records:
        if not record.analyzed_at:
            continue

        record_date = record.analyzed_at.date()
        if record_date not in target_date_set:
            continue

        previous = latest_by_date.get(record_date)
        if not previous or record.analyzed_at > previous.analyzed_at:
            latest_by_date[record_date] = record

    return [
        float(latest_by_date[date].risk_score) if date in latest_by_date else 0
        for date in target_dates
    ]

# --- 화면 뷰 ---
@worker_bp.route('/admin')
def admin_view():
    """사회복지사 관리자 화면"""
    return role_redirect('worker') or render_template('admin_web.html', registration_mode=False)


@worker_bp.route('/register/worker')
def worker_registration_view():
    if current_role():
        return role_redirect(None)
    return render_template('admin_web.html', registration_mode=True)

# --- 관리자 인증 및 계정 API ---
@worker_bp.route('/api/admin/login', methods=['POST'])
def api_admin_login():
    """관리자 로그인"""
    data = request.get_json() or {}
    admin_id = data.get('admin_id', '').strip()
    password = data.get('password', '').strip()

    if not admin_id or not password:
        return jsonify({"success": False, "message": "아이디와 비밀번호를 입력해주세요."}), 400

    worker = Worker.query.filter_by(login_id=admin_id).first()
    if not worker or worker.password != password:
        return jsonify({"success": False, "message": "아이디 또는 비밀번호가 올바르지 않습니다."}), 401

    select_role('worker')
    session['admin_id'] = worker.login_id
    session['admin_worker_id'] = worker.worker_id
    session['admin_name'] = worker.name

    return jsonify({
        "success": True,
        "message": f"{worker.name} 복지사님, 환영합니다.",
        "admin": {
            "name": worker.name,
            "region": worker.address
        }
    })

@worker_bp.route('/api/admin/signup', methods=['POST'])
def api_admin_signup():
    """관리자 회원가입"""
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    org = data.get('org', '').strip()
    admin_id = data.get('admin_id', '').strip()
    phone = extract_numbers(data.get('phone', ''))
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    region = data.get('region', '').strip()

    if not all([name, admin_id, phone, password, region]):
        return jsonify({"success": False, "message": "필수 정보를 모두 입력해주세요."}), 400

    if Worker.query.filter_by(login_id=admin_id).first():
        return jsonify({"success": False, "message": "이미 사용 중인 아이디입니다."}), 409

    try:
        new_worker = Worker(
            login_id=admin_id,
            password=password,
            name=name,
            org=org if org else None,
            phone_number=phone,
            email=email if email else None,
            address=region
        )
        db.session.add(new_worker)
        db.session.commit()
        return jsonify({"success": True, "message": "회원가입이 완료되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"DB 저장 실패: {str(e)}"}), 500

@worker_bp.route('/api/admin/logout', methods=['POST'])
def api_admin_logout():
    """관리자 로그아웃"""
    session.clear()
    return jsonify({"success": True, "message": "로그아웃 되었습니다."})

@worker_bp.route('/api/admin/check-session', methods=['GET'])
def api_admin_check_session():
    """관리자 세션 검증"""
    admin_login_id = session.get('admin_id')
    if current_role() != 'worker':
        return jsonify({"is_logged_in": False})
    
    worker = Worker.query.filter_by(login_id=admin_login_id).first()
    if not worker:
        session.clear()
        return jsonify({"is_logged_in": False})

    return jsonify({
        "is_logged_in": True,
        "admin": {
            "name": worker.name,
            "region": worker.address
        }
    })

# --- 대상 어르신 관리 API ---
@worker_bp.route('/api/admin/elders', methods=['GET'])
def api_get_elders():
    """대상 어르신 목록 조회 및 통계/위험도 계산"""
    from app import check_and_update_missed_meals
    check_and_update_missed_meals()

    current_worker_id = session.get('admin_worker_id')
    if not current_worker_id:
        admin_login_id = session.get('admin_id')
        if admin_login_id:
            worker = Worker.query.filter_by(login_id=admin_login_id).first()
            if worker:
                current_worker_id = worker.worker_id

    assigned_users = User.query.filter_by(worker_id=current_worker_id, is_active=True).all() if current_worker_id else []
    unassigned_users = User.query.filter(User.worker_id.is_(None), User.is_active.is_(True)).all()
    today = datetime.datetime.now().date()

    def process_elder_data(u):
        health_history = HealthStatus.query.filter_by(user_id=u.user_id)\
            .order_by(HealthStatus.recorded_at.desc()).all()
             
        login_history = LoginHistory.query.filter_by(user_id=u.user_id)\
            .order_by(LoginHistory.auth_time.desc()).all()

        latest_health = health_history[0] if health_history else None
        today_health = next((h for h in health_history if h.target_date == today), None)
        if latest_health:
            condition = latest_health.condition_level
            meal = f"아침 : {latest_health.breakfast_status}  점심 : {latest_health.lunch_status}  저녁 : {latest_health.dinner_status}"
            meal_short = f"아침 : {latest_health.breakfast_status}<br>점심 : {latest_health.lunch_status}<br>저녁 : {latest_health.dinner_status}"
            last_input_str = latest_health.recorded_at.strftime("%m/%d %H:%M")
            display_last_time = last_input_str
        else:
            condition = 3
            meal = "미입력"
            meal_short = "미입력"
            last_input_str = "미입력"
            display_last_time = "미입력"

        try:
            eval_res = evaluate_and_record_risk(u, health_history, login_history, db.session, RiskAnalysis, force=False)
            risk_score = eval_res["score"]
            risk_level = eval_res["risk_level"].lower()
            score_breakdown = eval_res["score_breakdown"]
            ai_desc = eval_res["ai_summary"]
            confidence = eval_res.get("confidence", {"label": "보통", "score": 60})
            evidence = eval_res.get("evidence", [])
            pattern_insights = eval_res.get("pattern_insights", [])
        except Exception:
            risk_score = 50
            risk_level = "watch"
            score_breakdown = [{"item": "기본 점수 (데이터 부족)", "score": "-50점", "type": "minus"}]
            ai_desc = "상태 데이터 분석 중입니다."
            confidence = {"label": "낮음", "score": 35}
            evidence = ["분석 데이터 확인 필요"]
            pattern_insights = [{
                "title": "생활 패턴 분석",
                "detail": "분석 데이터를 불러오는 중입니다.",
                "level": "watch"
            }]

        if not latest_health:
            ai_desc = "아직 입력된 건강/식사 기록이 없습니다."

        recent_risks = RiskAnalysis.query.filter_by(user_id=u.user_id)\
            .order_by(RiskAnalysis.analyzed_at.asc()).all()
        latest_risk = recent_risks[-1] if recent_risks else None
        chart_points = generate_svg_chart_points(build_daily_risk_scores(recent_risks))

        checkup_docs = CheckupDocument.query.filter_by(user_id=u.user_id)\
            .order_by(CheckupDocument.uploaded_at.desc()).all()
         
        docs_list = [{
            "doc_id": d.doc_id,
            "file_path": d.file_path,
            "original_name": d.original_name or "건강검진표",
            "uploaded_at": d.uploaded_at.strftime("%Y-%m-%d %H:%M")
        } for d in checkup_docs]

        # 최근 조치 기록 조회 >> 길동 추가
        recent_actions = PostManagement.query.filter_by(
            user_id=u.user_id
        ).order_by(
            PostManagement.action_time.desc()
        ).limit(1).all()

        action_history = []

        for action in recent_actions:
            action_worker = Worker.query.get(action.worker_id)

            action_history.append({
                "management_id": action.management_id,
                "action_type": action.action_type,
                "feedback": action.action_feedback,
                "worker_name": action_worker.name if action_worker else "-",
                "alert_time": (
                    action.alert_time.strftime("%Y-%m-%d %H:%M")
                    if action.alert_time else "-"
                ),
                "action_time": (
                    action.action_time.strftime("%Y-%m-%d %H:%M")
                    if action.action_time else "-"
                )
            })

        return {
            "id": u.user_id,
            "name": u.name,
            "age": u.age,
            "phone": format_phone_display(u.phone_number),
            "address": u.address,
            "disease": u.note if u.has_underlying_disease and u.note else ("기저질환 있음" if u.has_underlying_disease else "없음"),
            "emergency_contact": format_phone_display(u.emergency_contact),
            "health": condition,
            "meal": meal,
            "meal_short": meal_short,
            "score": risk_score,
            "score_breakdown": score_breakdown,
            "confidence": confidence,
            "evidence": evidence,
            "pattern_insights": pattern_insights,
            "risk": risk_level,
            "alert_time": latest_risk.analyzed_at.strftime("%Y-%m-%d %H:%M") if latest_risk and latest_risk.analyzed_at else "-",
            "status_as_of": latest_risk.analyzed_at.isoformat() if latest_risk and latest_risk.analyzed_at else None,
            "last": display_last_time,
            "lastInput": last_input_str,
            "created_at": u.created_at.strftime("%Y-%m-%d") if u.created_at else "-",
            "chart": chart_points,
            "desc": ai_desc,
            "has_recorded": bool(latest_health is not None),
            "has_recorded_today": bool(today_health is not None),
            "checkup_docs": docs_list,
            "action_history": action_history #added by 길동
        }

    assigned_list = [process_elder_data(u) for u in assigned_users]
    unassigned_list = [process_elder_data(u) for u in unassigned_users]

    alert_history = []
    if current_worker_id:
        alert_records = RiskAnalysis.query.join(
            User, RiskAnalysis.user_id == User.user_id
        ).filter(
            User.worker_id == current_worker_id,
            User.is_active.is_(True),
            RiskAnalysis.risk_level.in_(("SAFE", "WATCH", "WARN", "DANGER"))
        ).order_by(
            RiskAnalysis.analyzed_at.desc()
        ).limit(100).all()

        for record in alert_records:
            user = User.query.get(record.user_id)
            alert_history.append({
                "id": record.user_id,
                "name": user.name if user else "-",
                "phone": format_phone_display(user.phone_number) if user else "-",
                "risk": record.risk_level.lower(),
                "score": float(record.risk_score),
                "alert_time": (
                    record.analyzed_at.strftime("%Y-%m-%d %H:%M")
                    if record.analyzed_at else "-"
                )
            })

    return jsonify({
        "success": True, 
        "data": assigned_list,
        "unassigned": unassigned_list,
        "alert_history": alert_history
    })

@worker_bp.route('/api/admin/emergency-alerts', methods=['GET'])
def api_get_emergency_alerts():
    """현재 로그인한 복지사의 긴급호출 알림 목록 조회"""
    current_worker_id = session.get('admin_worker_id')
    if not current_worker_id:
        admin_login_id = session.get('admin_id')
        if admin_login_id:
            worker = Worker.query.filter_by(login_id=admin_login_id).first()
            if worker:
                current_worker_id = worker.worker_id

    if not current_worker_id:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    unread_only = str(request.args.get('unread_only', '')).lower() in ('1', 'true', 'yes')

    try:
        limit = int(request.args.get('limit', 50))
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 100))

    query = EmergencyAlert.query.filter_by(worker_id=current_worker_id)
    if unread_only:
        query = query.filter_by(is_read=False)

    alerts = query.order_by(EmergencyAlert.created_at.desc()).limit(limit).all()

    return jsonify({
        "success": True,
        "data": [{
            "alert_id": alert.alert_id,
            "user_id": alert.user_id,
            "user_name": alert.user_name,
            "user_phone": format_phone_display(alert.user_phone),
            "emergency_contact": format_phone_display(alert.emergency_contact),
            "message": alert.message,
            "created_at": alert.created_at.strftime("%Y-%m-%d %H:%M") if alert.created_at else "-",
            "is_read": bool(alert.is_read)
        } for alert in alerts],
        "unread_count": EmergencyAlert.query.filter_by(
            worker_id=current_worker_id,
            is_read=False
        ).count()
    })

@worker_bp.route('/api/admin/emergency-alerts/<int:alert_id>/read', methods=['POST', 'PATCH'])
def api_mark_emergency_alert_read(alert_id):
    """현재 로그인한 복지사의 긴급호출 알림을 읽음 처리"""
    current_worker_id = session.get('admin_worker_id')
    if not current_worker_id:
        admin_login_id = session.get('admin_id')
        if admin_login_id:
            worker = Worker.query.filter_by(login_id=admin_login_id).first()
            if worker:
                current_worker_id = worker.worker_id

    if not current_worker_id:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    alert = EmergencyAlert.query.filter_by(
        alert_id=alert_id,
        worker_id=current_worker_id
    ).first()

    if not alert:
        return jsonify({"success": False, "message": "긴급알림을 찾을 수 없습니다."}), 404

    try:
        alert.is_read = True
        db.session.commit()

        unread_count = EmergencyAlert.query.filter_by(
            worker_id=current_worker_id,
            is_read=False
        ).count()

        return jsonify({
            "success": True,
            "message": "긴급알림을 읽음 처리했습니다.",
            "alert_id": alert.alert_id,
            "is_read": bool(alert.is_read),
            "unread_count": unread_count
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"읽음 처리 실패: {str(e)}"}), 500

@worker_bp.route('/api/admin/elders/assign', methods=['POST'])
def api_assign_elder():
    """미배정 어르신을 현재 로그인한 복지사에게 배정"""
    data = request.get_json() or {}
    user_id = data.get('user_id')

    current_worker_id = session.get('admin_worker_id')
    if not current_worker_id:
        admin_login_id = session.get('admin_id')
        if admin_login_id:
            worker = Worker.query.filter_by(login_id=admin_login_id).first()
            if worker:
                current_worker_id = worker.worker_id

    if not current_worker_id or not user_id:
        return jsonify({"success": False, "message": "잘못된 요청입니다."}), 400

    user = User.query.get(user_id)
    if not user:
        return jsonify({"success": False, "message": "대상자를 찾을 수 없습니다."}), 404

    try:
        user.worker_id = current_worker_id
        db.session.commit()
        return jsonify({"success": True, "message": f"'{user.name}' 어르신이 배정되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"배정 실패: {str(e)}"}), 500

@worker_bp.route('/api/admin/actions/save', methods=['POST'])
def api_save_post_management():
    """복지사 사후 조치 피드백 저장"""
    data = request.get_json() or {}
    user_id = data.get('user_id')
    name = data.get('name')
    action_type = data.get('action_type', '전화상담')
    feedback = data.get('feedback', '').strip()

    current_worker_id = session.get('admin_worker_id')
    if not current_worker_id:
        admin_login_id = session.get('admin_id')
        if admin_login_id:
            worker = Worker.query.filter_by(login_id=admin_login_id).first()
            if worker:
                current_worker_id = worker.worker_id

    if not current_worker_id:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    if not user_id and name:
        user = User.query.filter_by(name=name).first()
        if user:
            user_id = user.user_id

    if not user_id or not feedback:
        return jsonify({"success": False, "message": "필수 항목이 누락되었습니다."}), 400

    latest_risk = RiskAnalysis.query.filter_by(user_id=user_id)\
        .order_by(RiskAnalysis.analyzed_at.desc()).first()

    try:
        new_action = PostManagement(
            user_id=user_id,
            worker_id=current_worker_id,
            analysis_id=latest_risk.analysis_id if latest_risk else None,
            alert_time=latest_risk.analyzed_at if latest_risk else datetime.datetime.now(),
            action_type=action_type,
            action_feedback=feedback,
            action_time=datetime.datetime.now()
        )
        db.session.add(new_action)
        db.session.commit()
        return jsonify({"success": True, "message": "조치 내역이 저장되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"저장 실패: {str(e)}"}), 500

# 조치 기록 조회
@worker_bp.route('/api/admin/actions', methods=['GET'])
def api_get_action_history():
    """현재 사회복지사가 담당한 조치 기록 조회"""

    current_worker_id = session.get('admin_worker_id')

    if not current_worker_id:
        admin_login_id = session.get('admin_id')

        if admin_login_id:
            worker = Worker.query.filter_by(
                login_id=admin_login_id
            ).first()

            if worker:
                current_worker_id = worker.worker_id

    if not current_worker_id:
        return jsonify({
            "success": False,
            "message": "로그인이 필요합니다."
        }), 401

    actions = PostManagement.query.filter_by(
        worker_id=current_worker_id
    ).order_by(
        PostManagement.action_time.desc()
    ).all()

    result = []

    for action in actions:
        user = User.query.get(action.user_id)

        result.append({
            "management_id": action.management_id,
            "user_id": action.user_id,
            "user_name": user.name if user else "알 수 없음",
            "action_type": action.action_type,
            "feedback": action.action_feedback,
            "alert_time": (
                action.alert_time.strftime("%Y-%m-%d %H:%M")
                if action.alert_time else "-"
            ),
            "action_time": (
                action.action_time.strftime("%Y-%m-%d %H:%M")
                if action.action_time else "-"
            )
        })

    return jsonify({
        "success": True,
        "data": result
    })

@worker_bp.route('/api/admin/elders/register', methods=['POST'])
def api_admin_register_elder():
    """복지사가 새로운 어르신 직접 등록"""
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    age = data.get('age')
    phone_clean = extract_numbers(data.get('phone_number', ''))
    address = data.get('address', '').strip()
    emergency_contact = extract_numbers(data.get('emergency_contact', ''))
    disease_note = data.get('disease_note', '없음').strip()
    has_disease = disease_note != '없음' and len(disease_note) > 0

    if not all([name, age, phone_clean, address]):
        return jsonify({"success": False, "message": "필수 정보를 입력해주세요."}), 400

    if User.query.filter_by(phone_number=phone_clean).first():
        return jsonify({"success": False, "message": "이미 등록된 전화번호입니다."}), 409

    worker_id = session.get('admin_worker_id')

    try:
        new_elder = User(
            name=name,
            age=int(age),
            address=address,
            phone_number=phone_clean,
            emergency_contact=emergency_contact if emergency_contact else None,
            has_underlying_disease=has_disease,
            note=disease_note if has_disease else None,
            worker_id=worker_id,
            is_active=True
        )
        db.session.add(new_elder)
        db.session.commit()
        return jsonify({"success": True, "message": f"'{name}' 어르신이 등록되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"등록 실패: {str(e)}"}), 500

@worker_bp.route('/api/admin/elders/<int:user_id>', methods=['DELETE'])
def api_admin_delete_elder(user_id):
    """어르신 비활성화 (삭제 처리)"""
    current_worker_id = session.get('admin_worker_id')
    if not current_worker_id:
        admin_login_id = session.get('admin_id')
        if admin_login_id:
            worker = Worker.query.filter_by(login_id=admin_login_id).first()
            if worker:
                current_worker_id = worker.worker_id

    if not current_worker_id:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    user = User.query.get(user_id)
    if not user:
        return jsonify({"success": False, "message": "대상자를 찾을 수 없습니다."}), 404

    try:
        user.is_active = False
        user.worker_id = None
        user.session_token = None
        db.session.commit()
        return jsonify({"success": True, "message": f"'{user.name}' 어르신이 삭제되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"삭제 실패: {str(e)}"}), 500

@worker_bp.route('/api/admin/users/logout', methods=['POST'])
def api_admin_remote_logout():
    """복지사가 특정 어르신 계정을 원격 로그아웃 처리"""
    data = request.get_json() or {}
    user_id = data.get('user_id')

    current_worker_id = session.get('admin_worker_id')
    if not current_worker_id:
        admin_login_id = session.get('admin_id')
        if admin_login_id:
            worker = Worker.query.filter_by(login_id=admin_login_id).first()
            if worker:
                current_worker_id = worker.worker_id

    if not current_worker_id:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    if not user_id:
        return jsonify({"success": False, "message": "대상자 ID가 누락되었습니다."}), 400

    user = User.query.get(user_id)
    if not user:
        return jsonify({"success": False, "message": "대상자를 찾을 수 없습니다."}), 404

    try:
        user.session_token = None
        db.session.commit()
        return jsonify({"success": True, "message": f"'{user.name}' 어르신이 로그아웃 처리되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"로그아웃 처리 실패: {str(e)}"}), 500

# --- 검진 문서 삭제 및 AI 분석 API ---
@worker_bp.route('/api/admin/checkup/<int:doc_id>', methods=['DELETE'])
def api_delete_checkup(doc_id):
    """검진 문서 로컬 파일 및 DB 레코드 삭제"""
    current_worker_id = session.get('admin_worker_id')
    if not current_worker_id:
        admin_login_id = session.get('admin_id')
        if admin_login_id:
            worker = Worker.query.filter_by(login_id=admin_login_id).first()
            if worker:
                current_worker_id = worker.worker_id

    if not current_worker_id:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    doc = CheckupDocument.query.get(doc_id)
    if not doc:
        return jsonify({"success": False, "message": "해당 문서를 찾을 수 없습니다."}), 404

    try:
        if doc.file_path:
            filename = os.path.basename(doc.file_path)
            local_file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
            if os.path.exists(local_file_path):
                os.remove(local_file_path)
        
        db.session.delete(doc)
        db.session.commit()
        return jsonify({"success": True, "message": "검진 문서가 삭제되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"삭제 실패: {str(e)}"}), 500

def _analysis_target(user_id):
    if current_role() != 'worker':
        raise HealthAnalysisError('사회복지사 로그인이 필요합니다.', 401)
    worker = Worker.query.filter_by(login_id=session['admin_id']).first()
    user = db.session.get(User, user_id)
    if not user or not user.is_active:
        raise HealthAnalysisError('대상자를 찾을 수 없습니다.', 404)
    if user.worker_id != worker.worker_id:
        raise HealthAnalysisError('현재 담당 대상자만 분석·조회할 수 있습니다.', 403)
    return user, worker.worker_id


def _health_analysis_json(record, current_snapshot=None):
    if not record:
        return None
    data = {'analysis_id': record.analysis_id, 'analyzed_at': record.analyzed_at.isoformat(),
            'model': record.model, 'analysis': record.summary, 'result': record.result,
            'input_snapshot': record.input_snapshot}
    data['trends'] = record.input_snapshot.get('trends') or legacy_trends(record.input_snapshot)
    if current_snapshot is not None:
        data['needs_reanalysis'] = material_signature(record.input_snapshot) != material_signature(current_snapshot)
    return data


def _health_context(user, now):
    # 기존 점수 계산은 전체 이력을 사용한다. 외부 전송은 build_input의 최근 30일로 제한한다.
    health = HealthStatus.query.filter(HealthStatus.user_id == user.user_id, HealthStatus.recorded_at <= now).order_by(HealthStatus.recorded_at.desc(), HealthStatus.status_id.desc()).all()
    logins = LoginHistory.query.filter(LoginHistory.user_id == user.user_id, LoginHistory.auth_time <= now).order_by(LoginHistory.auth_time.desc(), LoginHistory.history_id.desc()).all()
    documents = db.session.query(CheckupDocument, CheckupResult).join(CheckupResult, CheckupResult.doc_id == CheckupDocument.doc_id).filter(CheckupDocument.user_id == user.user_id).all()
    return health, logins, documents


@worker_bp.route('/api/admin/elders/<int:user_id>/life-pattern-ai', methods=['POST'])
def api_analyze_life_pattern(user_id):
    """기존 주소/analysis 응답을 유지하는 건강 종합 분석."""
    try:
        user, worker_id = _analysis_target(user_id)
        body = request.get_json(silent=True)
        if request.data and not isinstance(body, dict):
            raise HealthAnalysisError('요청 형식을 확인해주세요.', 400)
        if isinstance(body, dict) and 'model' in body and body['model'] is None:
            raise HealthAnalysisError('모델을 선택해주세요.', 400)
        model = resolve_model(body.get('model') if body else None)
        now = datetime.datetime.now()
        health, logins, documents = _health_context(user, now)
        snapshot = build_input(user, health, logins, documents, now)
        status = system_status(calculate_risk(user, health, logins, now=now), now)
        previous = HealthAnalysis.query.filter_by(user_id=user_id).order_by(HealthAnalysis.analyzed_at.desc(), HealthAnalysis.analysis_id.desc()).first()
        enrich_snapshot(snapshot, status, previous, [row.auth_time for row in logins])
        selected_ids = {doc['doc_id'] for doc in snapshot['documents']}
        risk_history = _trend_risks(user_id, now, 30)
        snapshot['trends'] = build_trends(health, risk_history,
            [(doc, result) for doc, result in documents if doc.doc_id in selected_ids], status, now,
            document_limit=2)
        db.session.rollback()
        result = analyze(snapshot, model=model)
        # 응답 대기 중 담당자 또는 활성 상태가 바뀌면 저장하지 않는다.
        db.session.refresh(user)
        if user.worker_id != worker_id or not user.is_active:
            raise HealthAnalysisError('담당 대상자 정보가 변경되었습니다. 다시 확인해주세요.', 403)
        record = HealthAnalysis(user_id=user_id, worker_id=worker_id, model=model,
                                input_snapshot=snapshot, result=result, summary=summarize(result))
        # 완료 중 입력이 바뀌었을 수 있으므로 현재 자료와 다시 대조한다.
        current_now = datetime.datetime.now()
        current_health, current_logins, current_docs = _health_context(user, current_now)
        try:
            current_snapshot = build_input(user, current_health, current_logins, current_docs, current_now)
        except HealthAnalysisError as error:
            if error.status != 422:
                raise
            current_snapshot = {}
        current_status = system_status(calculate_risk(user, current_health, current_logins, now=current_now), current_now)
        db.session.add(record)
        db.session.commit()
        return jsonify(success=True, **_health_analysis_json(record, current_snapshot), current_status=current_status)
    except (HealthAnalysisError, AIError) as error:
        db.session.rollback()
        return jsonify(success=False, message=str(error)), error.status
    except Exception:
        db.session.rollback()
        current_app.logger.exception('건강 종합 분석 또는 저장 실패')
        return jsonify(success=False, message='건강 종합 분석을 완료·저장하지 못했습니다. 이전 결과는 유지됩니다. 다시 시도해주세요.'), 500


@worker_bp.route('/api/admin/elders/<int:user_id>/health-analysis', methods=['GET'])
def api_get_health_analysis(user_id):
    try:
        user, _ = _analysis_target(user_id)
        query = HealthAnalysis.query.filter_by(user_id=user_id)
        history = query.order_by(HealthAnalysis.analyzed_at.desc(), HealthAnalysis.analysis_id.desc()).limit(20).all()
        selected = history[0] if history else None
        if 'analysis_id' in request.args:
            analysis_id = request.args.get('analysis_id', type=int)
            if not analysis_id or analysis_id < 1:
                raise HealthAnalysisError('분석 이력 번호를 확인해주세요.', 400)
            selected = query.filter_by(analysis_id=analysis_id).first()
            if not selected:
                raise HealthAnalysisError('분석 이력을 찾을 수 없습니다.', 404)
        now = datetime.datetime.now()
        health, logins, documents = _health_context(user, now)
        try:
            current_snapshot = build_input(user, health, logins, documents, now)
        except HealthAnalysisError as error:
            if error.status != 422:
                raise
            current_snapshot = {}
        current_status_error = None
        try:
            current_status = system_status(calculate_risk(user, health, logins, now=now), now)
        except Exception:
            # 조회용 현재 계산이 실패해도 과거의 성공 결과는 열람할 수 있게 한다.
            current_status = None
            current_status_error = '현재 시스템 상태를 계산하지 못했습니다. 저장된 분석 당시 결과를 표시합니다.'
            current_app.logger.exception('현재 시스템 상태 조회 계산 실패')
        return jsonify(success=True, latest=_health_analysis_json(history[0] if history else None, current_snapshot),
                       selected=_health_analysis_json(selected, current_snapshot), current_status=current_status, current_status_error=current_status_error,
                       history=[{'analysis_id': r.analysis_id, 'analyzed_at': r.analyzed_at.isoformat()} for r in history])
    except HealthAnalysisError as error:
        return jsonify(success=False, message=str(error)), error.status
    except Exception:
        current_app.logger.exception('건강 종합 분석 조회 실패')
        return jsonify(success=False, message='저장된 분석을 불러오지 못했습니다. 다시 시도해주세요.'), 500


def _trend_risks(user_id, now, days):
    start = datetime.datetime.combine(now.date() - datetime.timedelta(days=days - 1), datetime.time.min)
    return RiskAnalysis.query.filter(RiskAnalysis.user_id == user_id,
        RiskAnalysis.analyzed_at >= start, RiskAnalysis.analyzed_at <= now).all()


@worker_bp.route('/api/admin/elders/<int:user_id>/health-trends', methods=['GET'])
def api_get_health_trends(user_id):
    try:
        user, _ = _analysis_target(user_id)
        days = request.args.get('days', '30')
        if days not in ('7', '30'):
            raise HealthAnalysisError('조회 기간은 7일 또는 30일을 선택해주세요.', 400)
        days = int(days)
        now = datetime.datetime.now()
        health, logins, documents = _health_context(user, now)
        status = system_status(calculate_risk(user, health, logins, now=now), now)
        trends = build_trends(health, _trend_risks(user_id, now, days), documents, status, now, days)
        return jsonify(success=True, trends=trends, current_status=status)
    except HealthAnalysisError as error:
        return jsonify(success=False, message=str(error)), error.status
    except Exception:
        current_app.logger.exception('건강 추세 조회 실패')
        return jsonify(success=False, message='건강 추세를 불러오지 못했습니다. 다시 시도해주세요.'), 500
