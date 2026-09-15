import os
import datetime
from flask import Blueprint, render_template, request, jsonify, session, current_app
from models.models import db, Worker, User, HealthStatus, LoginHistory, RiskAnalysis, PostManagement, CheckupDocument
from services.ai_service import evaluate_and_record_risk, analyze_checkup_document_with_gemini

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

# --- 화면 뷰 ---
@worker_bp.route('/admin')
def admin_view():
    """사회복지사 관리자 화면"""
    return render_template('admin_web.html')

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
    if not admin_login_id:
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

    def process_elder_data(u):
        health_history = HealthStatus.query.filter_by(user_id=u.user_id)\
            .order_by(HealthStatus.recorded_at.desc()).all()
             
        login_history = LoginHistory.query.filter_by(user_id=u.user_id)\
            .order_by(LoginHistory.auth_time.desc()).all()

        latest_health = health_history[0] if health_history else None
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
            eval_res = evaluate_and_record_risk(u, health_history, login_history, db.session, RiskAnalysis)
            risk_score = eval_res["score"]
            risk_level = eval_res["risk_level"].lower()
            score_breakdown = eval_res["score_breakdown"]
            ai_desc = eval_res["ai_summary"]
        except Exception:
            risk_score = 50
            risk_level = "watch"
            score_breakdown = [{"item": "기본 점수 (데이터 부족)", "score": "-50점", "type": "minus"}]
            ai_desc = "상태 데이터 분석 중입니다."

        if not latest_health:
            ai_desc = "아직 입력된 건강/식사 기록이 없습니다."

        recent_risks = RiskAnalysis.query.filter_by(user_id=u.user_id)\
            .order_by(RiskAnalysis.analyzed_at.asc()).all()
        chart_points = generate_svg_chart_points([float(r.risk_score) for r in recent_risks])

        checkup_docs = CheckupDocument.query.filter_by(user_id=u.user_id)\
            .order_by(CheckupDocument.uploaded_at.desc()).all()
         
        docs_list = [{
            "doc_id": d.doc_id,
            "file_path": d.file_path,
            "original_name": d.original_name or "건강검진표",
            "uploaded_at": d.uploaded_at.strftime("%Y-%m-%d %H:%M")
        } for d in checkup_docs]

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
            "risk": risk_level,
            "last": display_last_time,
            "lastInput": last_input_str,
            "created_at": u.created_at.strftime("%Y-%m-%d") if u.created_at else "-",
            "chart": chart_points,
            "desc": ai_desc,
            "has_recorded": bool(latest_health is not None),
            "checkup_docs": docs_list
        }

    assigned_list = [process_elder_data(u) for u in assigned_users]
    unassigned_list = [process_elder_data(u) for u in unassigned_users]

    return jsonify({
        "success": True, 
        "data": assigned_list,
        "unassigned": unassigned_list
    })

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

@worker_bp.route('/api/admin/checkup/analyze/<int:doc_id>', methods=['POST'])
def api_analyze_checkup(doc_id):
    """업로드된 검진표 문서를 Gemini AI로 판독 분석"""
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
        filename = os.path.basename(doc.file_path)
        local_file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        if not os.path.exists(local_file_path):
            return jsonify({"success": False, "message": "서버에 파일이 존재하지 않습니다."}), 404

        analysis_result = analyze_checkup_document_with_gemini(local_file_path)
        return jsonify({
            "success": True,
            "analysis": analysis_result,
            "document_name": doc.original_name or "건강검진표"
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"AI 분석 실패: {str(e)}"}), 500