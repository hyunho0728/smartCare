from flask import Flask, render_template, request, jsonify, session, redirect
from models.models import db, Worker, User, HealthStatus, LoginHistory, RiskAnalysis, PostManagement
from services.ai_service import evaluate_and_record_risk
import os
import urllib.parse
import datetime
import re
import secrets
import subprocess
import threading
import time

app = Flask(__name__)
app.secret_key = "smartcare-secret-key-replace-with-env"

# ==========================================
# MySQL 연결 설정
# ==========================================
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "0728")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "elder_care_DB")

encoded_password = urllib.parse.quote_plus(DB_PASSWORD)
app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

with app.app_context():
    db.create_all()
    try:
        from sqlalchemy import text
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE USER ADD COLUMN session_token VARCHAR(64) NULL;"))
            conn.commit()
    except Exception:
        pass

# ==========================================
# 유틸리티 및 식사 자동 판정 함수
# ==========================================
MEAL_DEADLINES = {
    'breakfast': datetime.time(10, 0),  # 아침 마감 시간 (10:00)
    'lunch': datetime.time(15, 0),      # 점심 마감 시간 (15:00)
    'dinner': datetime.time(21, 0),     # 저녁 마감 시간 (21:00)
}

def check_and_update_missed_meals():
    """
    지정된 시간이 지나도록 '예정' 상태로 남아있는 식사를 '결식'으로 자동 전환하고
    영향을 받은 대상자의 AI 위험도를 재평가합니다.
    """
    now = datetime.datetime.now()
    current_time = now.time()
    today = now.date()

    try:
        records = HealthStatus.query.filter(HealthStatus.target_date <= today).all()
        updated_users = set()

        for r in records:
            changed = False
            is_today = (r.target_date == today)

            if r.breakfast_status == '예정':
                if not is_today or current_time >= MEAL_DEADLINES['breakfast']:
                    r.breakfast_status = '결식'
                    changed = True

            if r.lunch_status == '예정':
                if not is_today or current_time >= MEAL_DEADLINES['lunch']:
                    r.lunch_status = '결식'
                    changed = True

            if r.dinner_status == '예정':
                if not is_today or current_time >= MEAL_DEADLINES['dinner']:
                    r.dinner_status = '결식'
                    changed = True

            if changed:
                updated_users.add(r.user_id)

        if updated_users:
            db.session.commit()

            # 상태가 변경된 대상자의 AI 위험도 재계산
            for uid in updated_users:
                user = User.query.get(uid)
                if user:
                    h_history = HealthStatus.query.filter_by(user_id=uid)\
                        .order_by(HealthStatus.recorded_at.desc()).all()
                    l_history = LoginHistory.query.filter_by(user_id=uid)\
                        .order_by(LoginHistory.auth_time.desc()).all()
                    try:
                        evaluate_and_record_risk(user, h_history, l_history, db.session, RiskAnalysis)
                    except Exception as e:
                        print(f"[MealCheck] Risk evaluation failed for user {uid}: {e}")
    except Exception as e:
        db.session.rollback()
        print(f"[MealCheck] Error updating missed meals: {e}")

def is_mobile_request():
    user_agent = request.headers.get('User-Agent', '').lower()
    mobile_keywords = ['android', 'iphone', 'ipad', 'ipod', 'mobile', 'webos', 'opera mini']
    return any(keyword in user_agent for keyword in mobile_keywords)

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

def generate_svg_chart_points(scores_7days):
    """SVG Polyline 좌표 생성 (0~100 -> Y:170~30)"""
    x_coords = [0, 112, 224, 336, 448, 560, 650]
    while len(scores_7days) < 7:
        scores_7days.insert(0, scores_7days[0] if scores_7days else 100)
    scores_7days = scores_7days[-7:]
    
    points = []
    for x, s in zip(x_coords, scores_7days):
        y = int(170 - (float(s) / 100.0) * 140)
        points.append(f"{x},{y}")
    return " ".join(points)

# ==========================================
# 1. 뷰 라우트
# ==========================================
@app.route('/')
def index():
    if is_mobile_request():
        return render_template('user_web.html')
    return render_template('admin_web.html')

@app.route('/user')
def user_view():
    return render_template('user_web.html')

@app.route('/admin')
def admin_view():
    return render_template('admin_web.html')

# ==========================================
# 2. 관리자 API
# ==========================================
@app.route('/api/admin/login', methods=['POST'])
def api_admin_login():
    data = request.get_json() or {}
    admin_id = data.get('admin_id', '').strip()
    password = data.get('password', '').strip()

    if not admin_id or not password:
        return jsonify({"success": False, "message": "아이디와 비밀번호를 입력해주세요."}), 400

    worker = Worker.query.filter_by(login_id=admin_id).first()
    if not worker or worker.password != password:
        return jsonify({"success": False, "message": "로그인 정보가 일치하지 않습니다."}), 401

    session['admin_id'] = worker.login_id
    session['admin_worker_id'] = worker.worker_id
    session['admin_name'] = worker.name

    return jsonify({
        "success": True,
        "message": f"{worker.name}님 환영합니다.",
        "admin": {
            "name": worker.name,
            "region": worker.address
        }
    })

@app.route('/api/admin/signup', methods=['POST'])
def api_admin_signup():
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
        return jsonify({"success": False, "message": f"DB 등록 실패: {str(e)}"}), 500

@app.route('/api/admin/logout', methods=['POST'])
def api_admin_logout():
    session.clear()
    return jsonify({"success": True, "message": "로그아웃 되었습니다."})

@app.route('/api/admin/check-session', methods=['GET'])
def api_admin_check_session():
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

@app.route('/api/admin/elders', methods=['GET'])
def api_get_elders():
    # 조회 전 예정 상태를 시간 기준으로 결식 자동 변환
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
            meal = f"아침: {latest_health.breakfast_status} / 점심: {latest_health.lunch_status} / 저녁: {latest_health.dinner_status}"
            meal_short = f"아침: {latest_health.breakfast_status}<br>점심: {latest_health.lunch_status}<br>저녁: {latest_health.dinner_status}"
            last_input_str = latest_health.recorded_at.strftime("%m/%d %H:%M")
            display_last_time = last_input_str
        else:
            condition = 3
            meal = "미기록"
            meal_short = "미기록"
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
            score_breakdown = [{"item": "데이터 부족", "score": "-50점", "type": "minus"}]
            ai_desc = "데이터 분석 준비 중"

        if not latest_health:
            ai_desc = "아직 입력된 건강 상태 정보가 없습니다."

        recent_risks = RiskAnalysis.query.filter_by(user_id=u.user_id)\
            .order_by(RiskAnalysis.analyzed_at.asc()).all()
        chart_points = generate_svg_chart_points([float(r.risk_score) for r in recent_risks])

        return {
            "id": u.user_id,
            "name": u.name,
            "age": u.age,
            "phone": format_phone_display(u.phone_number),
            "address": u.address,
            "disease": u.note if u.has_underlying_disease and u.note else ("있음" if u.has_underlying_disease else "없음"),
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
            "has_recorded": bool(latest_health is not None)
        }

    assigned_list = [process_elder_data(u) for u in assigned_users]
    unassigned_list = [process_elder_data(u) for u in unassigned_users]

    return jsonify({
        "success": True, 
        "data": assigned_list,
        "unassigned": unassigned_list
    })

@app.route('/api/admin/elders/assign', methods=['POST'])
def api_assign_elder():
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
        return jsonify({"success": False, "message": "필수 정보가 누락되었습니다."}), 400

    user = User.query.get(user_id)
    if not user:
        return jsonify({"success": False, "message": "대상자를 찾을 수 없습니다."}), 404

    try:
        user.worker_id = current_worker_id
        db.session.commit()
        return jsonify({"success": True, "message": f"'{user.name}' 대상자가 배정되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"배정 실패: {str(e)}"}), 500

@app.route('/api/admin/actions/save', methods=['POST'])
def api_save_post_management():
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
        return jsonify({"success": False, "message": "필수 정보가 누락되었습니다."}), 400

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
        return jsonify({"success": True, "message": "사후조치가 저장되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"저장 실패: {str(e)}"}), 500

@app.route('/api/admin/elders/register', methods=['POST'])
def api_admin_register_elder():
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    age = data.get('age')
    phone_clean = extract_numbers(data.get('phone_number', ''))
    address = data.get('address', '').strip()
    emergency_contact = extract_numbers(data.get('emergency_contact', ''))
    disease_note = data.get('disease_note', '없음').strip()

    has_disease = disease_note != '없음' and len(disease_note) > 0

    if not all([name, age, phone_clean, address]):
        return jsonify({"success": False, "message": "필수 정보를 모두 입력해주세요."}), 400

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
        return jsonify({"success": True, "message": f"'{name}' 대상자가 등록되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"등록 실패: {str(e)}"}), 500

@app.route('/api/admin/elders/<int:user_id>', methods=['DELETE'])
def api_admin_delete_elder(user_id):
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
        return jsonify({"success": True, "message": f"'{user.name}' 대상자가 삭제되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"삭제 실패: {str(e)}"}), 500

@app.route('/api/admin/users/logout', methods=['POST'])
def api_admin_remote_logout():
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
        return jsonify({"success": False, "message": "대상자 정보가 없습니다."}), 400

    user = User.query.get(user_id)
    if not user:
        return jsonify({"success": False, "message": "대상자를 찾을 수 없습니다."}), 404

    try:
        user.session_token = None
        db.session.commit()
        return jsonify({"success": True, "message": f"'{user.name}' 대상자가 원격 로그아웃 되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"원격 로그아웃 실패: {str(e)}"}), 500

# ==========================================
# 3. 사용자 API
# ==========================================
@app.route('/api/user/login', methods=['POST'])
def api_user_login():
    # 로그인 시점에 예정 상태 갱신
    check_and_update_missed_meals()

    data = request.get_json() or {}
    phone_clean = extract_numbers(data.get('phone_number', ''))

    if not phone_clean:
        return jsonify({"success": False, "message": "전화번호를 입력해주세요."}), 400

    user = User.query.filter_by(phone_number=phone_clean, is_active=True).first()
    if not user:
        return jsonify({"success": False, "message": "등록되지 않은 번호입니다."}), 404

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
        map_reverse_action = {'식사': 'yes', '예정': 'plan', '결식': 'no'}
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

@app.route('/api/user/check-session', methods=['GET'])
def api_user_check_session():
    # 주기적인 세션 확인 시점에도 예정 상태 갱신
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
        map_reverse_action = {'식사': 'yes', '예정': 'plan', '결식': 'no'}
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

@app.route('/api/user/register', methods=['POST'])
def api_user_register():
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    phone_clean = extract_numbers(data.get('phone_number', ''))
    address = data.get('address', '').strip()
    age = data.get('age')
    has_disease = data.get('has_disease', False)
    disease_note = data.get('disease_note', '')

    if not all([name, phone_clean, address, age]):
        return jsonify({"success": False, "message": "필수 정보를 모두 입력해주세요."}), 400

    existing_user = User.query.filter_by(phone_number=phone_clean).first()
    if existing_user and existing_user.is_active:
        return jsonify({"success": False, "message": "이미 등록된 전화번호입니다."}), 409

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
            return jsonify({"success": True, "message": "재등록이 완료되었습니다."})

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
        return jsonify({"success": True, "message": "등록이 완료되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"등록 실패: {str(e)}"}), 500

@app.route('/api/user/health', methods=['POST'])
def api_record_health():
    data = request.get_json() or {}
    user_id = session.get('user_id')
    
    if not user_id:
        phone_clean = extract_numbers(data.get('phone_number'))
        if phone_clean:
            user = User.query.filter_by(phone_number=phone_clean).first()
            if user:
                user_id = user.user_id

    condition_level = data.get('condition_level')
    breakfast = data.get('breakfast', '식사')
    lunch = data.get('lunch', '식사')
    dinner = data.get('dinner', '식사')

    if not user_id or not condition_level:
        return jsonify({"success": False, "message": "필수 입력 정보가 누락되었습니다."}), 400

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

        msg = "상태가 수정(UPDATE) 되었습니다." if is_update else "상태가 등록(INSERT) 되었습니다."
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

def start_localtunnel():
    """Flask 실행 후 백그라운드에서 localtunnel 터널링을 시작합니다."""
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        time.sleep(2.0)
        try:
            cmd = ["lt", "--port", "5000", "--print-uri"]
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, shell=True)
            
            print("\n" + "=" * 65)
            print("[CareLink Pro - 외부 접속 주소 발급 중...]")
            print("=" * 65)
            
            for line in process.stdout:
                if "https://" in line:
                    url = line.strip()
                    print(f"\n[★ 스마트폰 접속 URL 발급 완료!]")
                    print(f"👉 접속 링크: {url}")
                    print("=" * 65 + "\n")
                    break
        except Exception as e:
            print(f"LocalTunnel 에러: {e}")

if __name__ == '__main__':
    threading.Thread(target=start_localtunnel, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=True)