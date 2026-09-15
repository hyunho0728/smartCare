import os
import re
import uuid
import secrets
import datetime
from flask import Blueprint, render_template, request, jsonify, session, current_app
from models.models import db, User, HealthStatus, LoginHistory, RiskAnalysis, CheckupDocument
from services.ai_service import evaluate_and_record_risk

user_bp = Blueprint('user', __name__)

# --- 내부 유틸 함수 ---
def extract_numbers(text):
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

# --- 화면 뷰 ---
@user_bp.route('/user')
def user_view():
    """사용자(어르신) 전용 모바일 웹 화면"""
    return render_template('user_web.html')

# --- 사용자 인증 및 세션 API ---
@user_bp.route('/api/user/login', methods=['POST'])
def api_user_login():
    """어르신 전화번호 간편 로그인"""
    from app import check_and_update_missed_meals
    check_and_update_missed_meals()

    data = request.get_json() or {}
    phone_clean = extract_numbers(data.get('phone_number', ''))

    if not phone_clean:
        return jsonify({"success": False, "message": "전화번호를 입력해주세요."}), 400

    user = User.query.filter_by(phone_number=phone_clean, is_active=True).first()
    if not user:
        return jsonify({"success": False, "message": "등록되지 않은 사용자입니다."}), 404

    token = secrets.token_hex(16)
    user.session_token = token

    try:
        history = LoginHistory(
            user_id=user.user_id,
            phone_number=phone_clean,
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent', '')[:255]
        )
        db.session.add(history)
        db.session.commit()
    except Exception:
        db.session.rollback()

    session['user_id'] = user.user_id
    session['user_phone'] = phone_clean
    session['user_token'] = token

    today_date = datetime.datetime.now().date()
    today_health = HealthStatus.query.filter_by(user_id=user.user_id, target_date=today_date)\
        .order_by(HealthStatus.recorded_at.desc()).first()

    today_status_data = None
    if today_health:
        map_reverse_action = {'식사완료': 'yes', '식사예정': 'plan', '식사안함': 'no'}
        h_time = today_health.recorded_at
        time_str = f"{'오전' if h_time.hour < 12 else '오후'} {h_time.hour % 12 or 12}:{h_time.minute:02d}"
        
        today_status_data = {
            "health": today_health.condition_level,
            "breakfast": map_reverse_action.get(today_health.breakfast_status, 'yes'),
            "lunch": map_reverse_action.get(today_health.lunch_status, 'yes'),
            "dinner": map_reverse_action.get(today_health.dinner_status, 'yes'),
            "saved_time": time_str
        }

    return jsonify({
        "success": True,
        "message": f"{user.name}님 환영합니다.",
        "user_id": user.user_id,
        "user_name": user.name,
        "phone_number": format_phone_display(phone_clean),
        "session_token": token,
        "today_saved": bool(today_health is not None),
        "today_data": today_status_data
    })

@user_bp.route('/api/user/check-session', methods=['GET'])
def api_user_check_session():
    """사용자 자동 로그인 및 세션 유효성 검사"""
    from app import check_and_update_missed_meals
    check_and_update_missed_meals()

    user_id = session.get('user_id')
    user_token = session.get('user_token')

    if not user_id:
        return jsonify({"valid": False, "message": "세션 없음"})

    user = User.query.get(user_id)
    if not user or not user.is_active or not user.session_token or user.session_token != user_token:
        session.clear()
        return jsonify({"valid": False, "message": "유효하지 않은 세션"})

    today_date = datetime.datetime.now().date()
    today_health = HealthStatus.query.filter_by(user_id=user.user_id, target_date=today_date)\
        .order_by(HealthStatus.recorded_at.desc()).first()

    today_status_data = None
    if today_health:
        map_reverse_action = {'식사완료': 'yes', '식사예정': 'plan', '식사안함': 'no'}
        h_time = today_health.recorded_at
        time_str = f"{'오전' if h_time.hour < 12 else '오후'} {h_time.hour % 12 or 12}:{h_time.minute:02d}"
        today_status_data = {
            "health": today_health.condition_level,
            "breakfast": map_reverse_action.get(today_health.breakfast_status, 'yes'),
            "lunch": map_reverse_action.get(today_health.lunch_status, 'yes'),
            "dinner": map_reverse_action.get(today_health.dinner_status, 'yes'),
            "saved_time": time_str
        }

    return jsonify({
        "valid": True,
        "user_info": {
            "user_name": user.name,
            "phone_number": format_phone_display(user.phone_number)
        },
        "today_saved": bool(today_health is not None),
        "today_data": today_status_data
    })

@user_bp.route('/api/user/register', methods=['POST'])
def api_user_register():
    """사용자 웹 직접 회원가입"""
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    phone_clean = extract_numbers(data.get('phone_number', ''))
    address = data.get('address', '').strip()
    age = data.get('age')
    has_disease = data.get('has_disease', False)
    disease_note = data.get('disease_note', '')

    if not all([name, phone_clean, address, age]):
        return jsonify({"success": False, "message": "필수 정보를 입력해주세요."}), 400

    existing_user = User.query.filter_by(phone_number=phone_clean).first()
    if existing_user and existing_user.is_active:
        return jsonify({"success": False, "message": "이미 등록된 사용자입니다."}), 409

    try:
        if existing_user and not existing_user.is_active:
            existing_user.name = name
            existing_user.age = int(age)
            existing_user.address = address
            existing_user.has_underlying_disease = bool(has_disease)
            existing_user.note = disease_note
            existing_user.is_active = True
            existing_user.worker_id = None
            db.session.commit()
            return jsonify({"success": True, "message": "회원가입이 완료되었습니다."})

        new_user = User(
            name=name,
            age=int(age),
            address=address,
            phone_number=phone_clean,
            has_underlying_disease=bool(has_disease),
            note=disease_note,
            is_active=True
        )
        db.session.add(new_user)
        db.session.commit()
        return jsonify({"success": True, "message": "회원가입이 완료되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"가입 실패: {str(e)}"}), 500

# --- 일일 건강 및 식사 기록 등록 API ---
@user_bp.route('/api/user/health', methods=['POST'])
def api_record_health():
    """기분/건강 상태 및 식사 여부 등록 (1시간 이내 수정 시 UPDATE)"""
    data = request.get_json() or {}
    user_id = session.get('user_id')
    
    if not user_id:
        phone_clean = extract_numbers(data.get('phone_number'))
        if phone_clean:
            user = User.query.filter_by(phone_number=phone_clean).first()
            if user:
                user_id = user.user_id

    condition_level = data.get('condition_level')

    def normalize_meal_status(val):
        if not val:
            return '미입력'
        val = str(val).strip()
        if val in ['식사완료', '먹음', 'yes', '완료']:
            return '식사완료'
        elif val in ['식사예정', 'plan', '예정', '먹을예정']:
            return '식사예정'
        elif val in ['식사안함', 'no', '안먹음', '거름']:
            return '식사안함'
        return '미입력'

    breakfast = normalize_meal_status(data.get('breakfast'))
    lunch = normalize_meal_status(data.get('lunch'))
    dinner = normalize_meal_status(data.get('dinner'))

    if not user_id or not condition_level:
        return jsonify({"success": False, "message": "필수 항목이 누락되었습니다."}), 400

    try:
        now_dt = datetime.datetime.now()
        
        latest_health = HealthStatus.query.filter_by(user_id=user_id)\
            .order_by(HealthStatus.recorded_at.desc()).first()

        is_update = False
        if latest_health and latest_health.target_date == now_dt.date():
            diff_seconds = (now_dt - latest_health.recorded_at).total_seconds()
            if diff_seconds <= 3600:
                latest_health.condition_level = int(condition_level)
                latest_health.breakfast_status = breakfast
                latest_health.lunch_status = lunch
                latest_health.dinner_status = dinner
                latest_health.recorded_at = now_dt
                db.session.commit()
                is_update = True

        if not is_update:
            health_record = HealthStatus(
                user_id=user_id,
                condition_level=int(condition_level),
                breakfast_status=breakfast,
                lunch_status=lunch,
                dinner_status=dinner,
                target_date=now_dt.date(),
                recorded_at=now_dt
            )
            db.session.add(health_record)
            db.session.commit()

        user = User.query.get(user_id)
        health_history = HealthStatus.query.filter_by(user_id=user_id)\
            .order_by(HealthStatus.recorded_at.desc()).all()
        login_history = LoginHistory.query.filter_by(user_id=user_id)\
            .order_by(LoginHistory.auth_time.desc()).all()

        eval_res = evaluate_and_record_risk(user, health_history, login_history, db.session, RiskAnalysis)

        msg = "상태가 수정(UPDATE)되었습니다." if is_update else "상태가 성공적으로 저장(INSERT)되었습니다."
        return jsonify({
            "success": True,
            "message": msg,
            "is_update": is_update,
            "has_worker": bool(user.worker_id is not None),
            "saved_at": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "risk_score": eval_res["score"],
            "risk_level": eval_res["risk_level"]
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"저장 실패: {str(e)}"}), 500

# --- 건강검진표 사진/PDF 업로드 API ---
@user_bp.route('/api/user/checkup/upload', methods=['POST'])
def api_upload_checkup():
    """사용자 검진표 사진 업로드"""
    user_id = session.get('user_id')
    
    if not user_id:
        phone_clean = extract_numbers(request.form.get('phone_number'))
        if phone_clean:
            user = User.query.filter_by(phone_number=phone_clean).first()
            if user:
                user_id = user.user_id

    if not user_id:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    if 'file' not in request.files:
        return jsonify({"success": False, "message": "파일이 첨부되지 않았습니다."}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "선택된 파일이 없습니다."}), 400

    try:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ['.jpg', '.jpeg', '.png', '.webp', '.pdf']:
            return jsonify({"success": False, "message": "지원되지 않는 파일 형식입니다. (이미지 또는 PDF 전용)"}), 400

        filename = f"{uuid.uuid4().hex}{ext}"
        save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(save_path)

        web_path = f"/static/uploads/checkups/{filename}"

        doc = CheckupDocument(
            user_id=user_id,
            file_path=web_path,
            original_name=file.filename,
            uploaded_at=datetime.datetime.now()
        )
        db.session.add(doc)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "검진표가 등록되었습니다.",
            "file_path": web_path
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"업로드 실패: {str(e)}"}), 500