from flask import Flask, render_template, request, jsonify, session, redirect
from models.models import db, Worker, User, HealthStatus, LoginHistory
import os
import urllib.parse
import datetime
import re

app = Flask(__name__)
app.secret_key = "smartcare-secret-key-replace-with-env"

# ==========================================
# MySQL 데이터베이스 설정
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


def is_mobile_request():
    user_agent = request.headers.get('User-Agent', '').lower()
    mobile_keywords = ['android', 'iphone', 'ipad', 'ipod', 'mobile', 'webos', 'opera mini']
    return any(keyword in user_agent for keyword in mobile_keywords)

def extract_numbers(text):
    """전화번호에서 숫자만 추출 (01012345678)"""
    if not text:
        return ""
    return re.sub(r'\D', '', str(text))

def format_phone_display(phone_str):
    """01012345678 -> 010-1234-5678 변환"""
    if not phone_str:
        return "-"
    p = str(phone_str)
    if len(p) == 11:
        return f"{p[:3]}-{p[3:7]}-{p[7:]}"
    elif len(p) == 10:
        return f"{p[:3]}-{p[3:6]}-{p[6:]}"
    return p


# ==========================================
# 1. 페이지 라우트
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
# 2. 사회복지사 관제 API
# ==========================================

@app.route('/api/admin/login', methods=['POST'])
def api_admin_login():
    data = request.get_json() or {}
    admin_id = data.get('admin_id', '').strip()
    password = data.get('password', '').strip()

    if not admin_id or not password:
        return jsonify({"success": False, "message": "아이디와 비밀번호를 모두 입력해주세요."}), 400

    worker = Worker.query.filter_by(login_id=admin_id).first()
    if not worker or worker.password != password:
        return jsonify({"success": False, "message": "아이디 또는 비밀번호가 일치하지 않습니다."}), 401

    session['admin_id'] = worker.login_id
    session['admin_worker_id'] = worker.worker_id
    session['admin_name'] = worker.name

    return jsonify({
        "success": True,
        "message": f"{worker.name}님, 환영합니다.",
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
        return jsonify({"success": False, "message": "모든 필수 항목을 입력해주세요."}), 400

    if Worker.query.filter_by(login_id=admin_id).first():
        return jsonify({"success": False, "message": "이미 존재하는 아이디입니다."}), 409

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
        return jsonify({"success": True, "message": "회원가입이 완료되었습니다. 로그인해주세요."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"DB 저장 실패: {str(e)}"}), 500


@app.route('/api/admin/logout', methods=['POST'])
def api_admin_logout():
    session.clear()
    return jsonify({"success": True, "message": "로그아웃되었습니다."})


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
    """로그인한 사회복지사의 배정 어르신과 미배정 어르신을 분리하여 반환"""
    current_worker_id = session.get('admin_worker_id')
    if not current_worker_id:
        admin_login_id = session.get('admin_id')
        if admin_login_id:
            worker = Worker.query.filter_by(login_id=admin_login_id).first()
            if worker:
                current_worker_id = worker.worker_id

    # 1. 본인에게 배정된 어르신 조회
    assigned_users = User.query.filter_by(worker_id=current_worker_id, is_active=True).all() if current_worker_id else []
    
    # 2. 담당자가 없는 미배정 어르신 조회
    unassigned_users = User.query.filter(User.worker_id.is_(None), User.is_active.is_(True)).all()

    def process_elder_data(u):
        latest_health = HealthStatus.query.filter_by(user_id=u.user_id)\
            .order_by(HealthStatus.recorded_at.desc()).first()

        latest_login = LoginHistory.query.filter_by(user_id=u.user_id)\
            .order_by(LoginHistory.auth_time.desc()).first()

        condition = latest_health.condition_level if latest_health else 3
        
        if latest_health:
            meal = f"아침: {latest_health.breakfast_status} · 점심: {latest_health.lunch_status} · 저녁: {latest_health.dinner_status}"
            meal_short = f"아침: {latest_health.breakfast_status}<br>점심: {latest_health.lunch_status}<br>저녁: {latest_health.dinner_status}"
            last_input_str = latest_health.recorded_at.strftime("%m/%d %H:%M")
        else:
            meal = "미입력"
            meal_short = "미입력"
            last_input_str = "기록 없음"

        display_last_time = last_input_str if latest_health else (latest_login.auth_time.strftime("%m/%d %H:%M") if latest_login else "기록 없음")

        # 시간 비례 선형 감점 모델 점수 산출
        risk_score = 100
        score_breakdown = [{"item": "기본 만점", "score": "100점", "type": "base"}]

        if u.age >= 80:
            risk_score -= 10
            score_breakdown.append({"item": f"고령 페널티 ({u.age}세)", "score": "-10점", "type": "minus"})

        if u.has_underlying_disease:
            risk_score -= 10
            disease_name = u.note if u.note else "기저질환"
            score_breakdown.append({"item": f"기저질환 ({disease_name})", "score": "-10점", "type": "minus"})

        if latest_health:
            if '결식' in [latest_health.breakfast_status, latest_health.lunch_status, latest_health.dinner_status]:
                risk_score -= 20
                score_breakdown.append({"item": "식사 결식 페널티", "score": "-20점", "type": "minus"})

            elapsed_hours = int((datetime.datetime.now() - latest_health.recorded_at).total_seconds() // 3600)
            if elapsed_hours > 0:
                time_penalty = elapsed_hours * 2
                risk_score -= time_penalty
                score_breakdown.append({"item": f"미입력 ({elapsed_hours}시간)", "score": f"-{time_penalty}점", "type": "minus"})
        else:
            risk_score -= 40
            score_breakdown.append({"item": "건강 상태 미등록", "score": "-40점", "type": "minus"})

        risk_score = max(0, min(100, risk_score))
        risk_level = "danger" if risk_score < 50 else "warn" if risk_score < 70 else "safe"

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
            "chart": "0,120 110,115 220,118 330,110 440,116 550,112 700,108",
            "desc": f"최근 건강 상태는 {condition}단계이며 오늘 식사는 ({meal}) 상태입니다."
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
    """미배정 어르신을 현재 사회복지사에게 배정"""
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
        return jsonify({"success": False, "message": "배정 요청 정보가 올바르지 않습니다."}), 400

    user = User.query.get(user_id)
    if not user:
        return jsonify({"success": False, "message": "어르신 정보를 찾을 수 없습니다."}), 404

    try:
        user.worker_id = current_worker_id
        db.session.commit()
        return jsonify({"success": True, "message": f"'{user.name}' 어르신이 담당으로 배정되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"배정 실패: {str(e)}"}), 500


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
        return jsonify({"success": False, "message": "성함, 나이, 휴대폰 번호, 주소는 필수입니다."}), 400

    if User.query.filter_by(phone_number=phone_clean).first():
        return jsonify({"success": False, "message": "이미 등록된 휴대폰 번호입니다."}), 409

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
            worker_id=worker_id
        )
        db.session.add(new_elder)
        db.session.commit()
        return jsonify({"success": True, "message": f"'{name}' 어르신이 등록되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"등록 실패: {str(e)}"}), 500


# ==========================================
# 3. 사용자(어르신) 모바일 API
# ==========================================

@app.route('/api/user/login', methods=['POST'])
def api_user_login():
    data = request.get_json() or {}
    phone_clean = extract_numbers(data.get('phone_number', ''))

    if not phone_clean:
        return jsonify({"success": False, "message": "휴대폰 번호를 입력해주세요."}), 400

    user = User.query.filter_by(phone_number=phone_clean, is_active=True).first()
    if not user:
        return jsonify({"success": False, "message": "등록되지 않은 번호입니다."}), 404

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

    return jsonify({
        "success": True,
        "message": f"{user.name} 어르신, 로그인되었습니다.",
        "user_id": user.user_id,
        "user_name": user.name,
        "phone_number": format_phone_display(phone_clean)
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
        return jsonify({"success": False, "message": "모든 필수 항목을 입력해주세요."}), 400

    if User.query.filter_by(phone_number=phone_clean).first():
        return jsonify({"success": False, "message": "이미 등록된 휴대폰 번호입니다."}), 409

    try:
        new_user = User(
            name=name,
            age=int(age),
            address=address,
            phone_number=phone_clean,
            has_underlying_disease=bool(has_disease),
            note=disease_note
        )
        db.session.add(new_user)
        db.session.commit()
        return jsonify({"success": True, "message": "회원가입이 완료되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"가입 실패: {str(e)}"}), 500


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
    breakfast = data.get('breakfast', '완료')
    lunch = data.get('lunch', '완료')
    dinner = data.get('dinner', '완료')

    if not user_id or not condition_level:
        return jsonify({"success": False, "message": "사용자 정보 또는 건강 상태가 누락되었습니다."}), 400

    try:
        health_record = HealthStatus(
            user_id=user_id,
            condition_level=int(condition_level),
            breakfast_status=breakfast,
            lunch_status=lunch,
            dinner_status=dinner,
            target_date=datetime.date.today(),
            recorded_at=datetime.datetime.now()
        )
        db.session.add(health_record)
        db.session.commit()
        return jsonify({
            "success": True,
            "message": "건강 상태가 정상적으로 저장되었습니다.",
            "saved_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"저장 실패: {str(e)}"}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)