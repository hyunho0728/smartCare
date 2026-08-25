from flask import Flask, render_template, request, jsonify, session, redirect
from models.models import db, Worker, User, HealthStatus, LoginHistory
import os
import urllib.parse
import datetime

app = Flask(__name__)
app.secret_key = "smartcare-secret-key-replace-with-env"

# ==========================================
# MySQL 데이터베이스 설정
# ==========================================
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "0728")  # 본인의 MySQL root 비밀번호
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "elder_care_DB")

# 비밀번호 특수문자 안전 인코딩
encoded_password = urllib.parse.quote_plus(DB_PASSWORD)

app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

with app.app_context():
    db.create_all()  # 테이블 자동 생성


def is_mobile_request():
    """요청 헤더(User-Agent)를 검사하여 모바일 기기 여부 판별"""
    user_agent = request.headers.get('User-Agent', '').lower()
    mobile_keywords = [
        'android', 'iphone', 'ipad', 'ipod', 'blackberry',
        'windows phone', 'iemobile', 'mobile', 'webos', 'opera mini'
    ]
    return any(keyword in user_agent for keyword in mobile_keywords)


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
# 2. 사회복지사(관리자) 인증 및 관제 API
# ==========================================

@app.route('/api/admin/login', methods=['POST'])
def api_admin_login():
    """사회복지사 로그인 처리 (MySQL WORKER 테이블 조회)"""
    data = request.get_json() or {}
    admin_id = data.get('admin_id', '').strip()
    password = data.get('password', '').strip()

    if not admin_id or not password:
        return jsonify({"success": False, "message": "아이디와 비밀번호를 모두 입력해주세요."}), 400

    worker = Worker.query.filter_by(worker_id=admin_id).first()
    if not worker or worker.password != password:
        return jsonify({"success": False, "message": "아이디 또는 비밀번호가 일치하지 않습니다."}), 401

    # 세션 등록
    session['admin_id'] = worker.worker_id
    session['admin_name'] = worker.name
    session['admin_region'] = worker.region

    return jsonify({
        "success": True,
        "message": f"{worker.name}님, 환영합니다.",
        "admin": {
            "name": worker.name,
            "org": worker.org,
            "region": worker.region
        }
    })


@app.route('/api/admin/signup', methods=['POST'])
def api_admin_signup():
    """사회복지사 회원가입 처리 (MySQL WORKER 테이블에 INSERT)"""
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    org = data.get('org', '').strip()
    admin_id = data.get('admin_id', '').strip()
    phone = data.get('phone', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()
    region = data.get('region', '').strip()

    if not all([name, org, admin_id, phone, email, password, region]):
        return jsonify({"success": False, "message": "모든 필수 항목을 입력해주세요."}), 400

    # 아이디 중복 확인
    existing_worker = Worker.query.filter_by(worker_id=admin_id).first()
    if existing_worker:
        return jsonify({"success": False, "message": "이미 존재하는 아이디입니다."}), 409

    try:
        new_worker = Worker(
            worker_id=admin_id,
            password=password,
            name=name,
            org=org,
            phone_number=phone,
            email=email,
            region=region
        )
        db.session.add(new_worker)
        db.session.commit()
        return jsonify({"success": True, "message": "회원가입이 완료되었습니다. 로그인해주세요."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"DB 저장 중 오류가 발생했습니다: {str(e)}"}), 500


@app.route('/api/admin/logout', methods=['POST'])
def api_admin_logout():
    """사회복지사 로그아웃"""
    session.pop('admin_id', None)
    session.pop('admin_name', None)
    session.pop('admin_region', None)
    return jsonify({"success": True, "message": "로그아웃되었습니다."})


@app.route('/api/admin/check-session', methods=['GET'])
def api_admin_check_session():
    """새로고침 시 사회복지사 세션 유지 여부 확인"""
    admin_id = session.get('admin_id')
    if not admin_id:
        return jsonify({"is_logged_in": False})

    worker = Worker.query.filter_by(worker_id=admin_id).first()
    if not worker:
        session.clear()
        return jsonify({"is_logged_in": False})

    return jsonify({
        "is_logged_in": True,
        "admin": {
            "name": worker.name,
            "org": worker.org,
            "region": worker.region
        }
    })


@app.route('/api/admin/elders', methods=['GET'])
def api_get_elders():
    """MySQL DB에서 전체 어르신 및 최근 건강 상태 조회 (대시보드 실시간 목록용)"""
    users = User.query.all()
    elders_data = []

    for u in users:
        # 최근 건강 기록 조회
        latest_health = HealthStatus.query.filter_by(phone_number=u.phone_number)\
            .order_by(HealthStatus.timestamp.desc()).first()
        
        # 최근 접속 기록 조회
        latest_login = LoginHistory.query.filter_by(phone_number=u.phone_number)\
            .order_by(LoginHistory.auth_time.desc()).first()

        condition = latest_health.condition_level if latest_health else 3
        meal = latest_health.meal_status if latest_health else "미입력"
        last_str = latest_login.auth_time.strftime("%m/%d %H:%M") if latest_login else "기록 없음"

        # 위험 점수 및 단계 산출 로직
        risk_score = 80
        if condition <= 2:
            risk_score -= 30
        if u.has_underlying_disease:
            risk_score -= 15
        
        risk_level = "danger" if risk_score < 50 else "warn" if risk_score < 70 else "safe"

        elders_data.append({
            "name": u.name,
            "age": u.age,
            "phone": u.phone_number,
            "address": u.address,
            "disease": u.note if u.has_underlying_disease and u.note else ("기저질환 보유" if u.has_underlying_disease else "없음"),
            "emergency_contact": u.emergency_contact,
            "health": condition,
            "meal": meal,
            "score": risk_score,
            "risk": risk_level,
            "last": last_str,
            "created_at": u.created_at.strftime("%Y-%m-%d") if u.created_at else "-",
            "chart": "0,120 110,115 220,118 330,110 440,116 550,112 700,108",
            "desc": f"최근 건강 상태는 {condition}단계이며 식사 상태는 {meal}입니다."
        })

    return jsonify({"success": True, "data": elders_data})


@app.route('/api/admin/elders/register', methods=['POST'])
def api_admin_register_elder():
    """사회복지사 대시보드 내 신규 어르신 직접 등록 API"""
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    age = data.get('age')
    phone_number = data.get('phone_number', '').strip()
    address = data.get('address', '').strip()
    emergency_contact = data.get('emergency_contact', '').strip()
    disease_note = data.get('disease_note', '없음').strip()
    has_disease = disease_note != '없음' and len(disease_note) > 0

    if not all([name, age, phone_number, address]):
        return jsonify({"success": False, "message": "성함, 나이, 휴대폰 번호, 주소는 필수 입력값입니다."}), 400

    # 중복 등록 검사
    existing_user = User.query.filter_by(phone_number=phone_number).first()
    if existing_user:
        return jsonify({"success": False, "message": "이미 등록된 휴대폰 번호의 어르신입니다."}), 409

    # 현재 로그인된 사회복지사 번호 연동
    current_worker_id = session.get('admin_id')
    worker_phone = None
    if current_worker_id:
        worker = Worker.query.filter_by(worker_id=current_worker_id).first()
        if worker:
            worker_phone = worker.phone_number

    try:
        new_elder = User(
            phone_number=phone_number,
            name=name,
            age=int(age),
            address=address,
            emergency_contact=emergency_contact if emergency_contact else None,
            has_underlying_disease=has_disease,
            note=disease_note if has_disease else None,
            worker_phone_number=worker_phone
        )
        db.session.add(new_elder)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": f"'{name}' 어르신 계정이 성공적으로 등록되었습니다."
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"등록 실패: {str(e)}"}), 500


# ==========================================
# 3. 사용자(어르신) 관련 API 엔드포인트
# ==========================================

@app.route('/api/user/login', methods=['POST'])
def api_user_login():
    """어르신 전화번호 로그인 처리 & 접속 이력 기록"""
    data = request.get_json() or {}
    phone_number = data.get('phone_number', '').strip()

    if not phone_number:
        return jsonify({"success": False, "message": "휴대폰 번호를 입력해주세요."}), 400

    user = User.query.filter_by(phone_number=phone_number).first()
    if not user:
        return jsonify({"success": False, "message": "등록되지 않은 번호입니다. 회원가입을 먼저 진행해주세요."}), 404

    # 접속 이력 기록
    try:
        history = LoginHistory(
            phone_number=user.phone_number,
            ip_address=request.remote_addr
        )
        db.session.add(history)
        db.session.commit()
    except Exception:
        db.session.rollback()

    session['user_phone'] = user.phone_number
    return jsonify({
        "success": True,
        "message": f"{user.name} 어르신, 로그인되었습니다.",
        "user_name": user.name,
        "phone_number": user.phone_number
    })


@app.route('/api/user/register', methods=['POST'])
def api_user_register():
    """어르신 사용자 직접 회원가입"""
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    phone_number = data.get('phone_number', '').strip()
    address = data.get('address', '').strip()
    age = data.get('age')
    has_disease = data.get('has_disease', False)
    disease_note = data.get('disease_note', '')

    if not all([name, phone_number, address, age]):
        return jsonify({"success": False, "message": "모든 필수 항목을 입력해주세요."}), 400

    existing_user = User.query.filter_by(phone_number=phone_number).first()
    if existing_user:
        return jsonify({"success": False, "message": "이미 등록된 휴대폰 번호입니다."}), 409

    try:
        new_user = User(
            phone_number=phone_number,
            name=name,
            age=int(age),
            address=address,
            has_underlying_disease=bool(has_disease),
            note=disease_note
        )
        db.session.add(new_user)
        db.session.commit()
        return jsonify({"success": True, "message": "어르신 회원가입이 완료되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"가입 처리 실패: {str(e)}"}), 500


@app.route('/api/user/health', methods=['POST'])
def api_record_health():
    """건강 상태 및 식사 여부 기록"""
    data = request.get_json() or {}
    phone_number = session.get('user_phone') or data.get('phone_number')
    condition_level = data.get('condition_level')
    meal_status = data.get('meal_status')

    if not phone_number or not condition_level:
        return jsonify({"success": False, "message": "필수 입력 값이 누락되었습니다."}), 400

    try:
        health_record = HealthStatus(
            phone_number=phone_number,
            condition_level=int(condition_level),
            meal_status=meal_status
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