import os
import urllib.parse
import datetime
import subprocess
import threading
import time
from flask import Flask, render_template, request, session, redirect
from dotenv import load_dotenv
from models.models import db, User, HealthStatus, LoginHistory, RiskAnalysis
from services.ai_service import evaluate_and_record_risk

# 작업자별 Blueprint 임포트
from routes.social_worker import worker_bp
#from routes.user import user_bp

load_dotenv()

app = Flask(__name__)
app.secret_key = "smartcare-secret-key-replace-with-env"

# ==========================================
# MySQL 데이터베이스 설정
# ==========================================
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "8888") # root 비밀번호는 다시 0728로
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "elder_care_DB")
#1mki;pnikn h;aaaaa

encoded_password = urllib.parse.quote_plus(DB_PASSWORD)
app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 건강검진표 업로드 경로 설정
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads', 'checkups')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# DB 초기화 및 테이블 생성
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

# Blueprint 등록
app.register_blueprint(worker_bp)
# app.register_blueprint(user_bp)

# ==========================================
# 공통 스케줄러 & 유틸리티
# ==========================================
MEAL_DEADLINES = {
    'breakfast': datetime.time(10, 0),  # 아침 마감 (10:00)
    'lunch': datetime.time(15, 0),      # 점심 마감 (15:00)
    'dinner': datetime.time(21, 0),     # 저녁 마감 (21:00)
}

def check_and_update_missed_meals():
    """식사 마감 시간이 지났는데 미입력 상태인 경우 '식사안함'으로 자동 변경."""
    now = datetime.datetime.now()
    current_time = now.time()
    today = now.date()

    try:
        records = HealthStatus.query.filter(HealthStatus.target_date <= today).all()
        updated_users = set()

        for r in records:
            changed = False
            is_today = (r.target_date == today)

            if r.breakfast_status == '식사예정':
                if not is_today or current_time >= MEAL_DEADLINES['breakfast']:
                    r.breakfast_status = '식사안함'
                    changed = True

            if r.lunch_status == '식사예정':
                if not is_today or current_time >= MEAL_DEADLINES['lunch']:
                    r.lunch_status = '식사안함'
                    changed = True

            if r.dinner_status == '식사예정':
                if not is_today or current_time >= MEAL_DEADLINES['dinner']:
                    r.dinner_status = '식사안함'
                    changed = True

            if changed:
                updated_users.add(r.user_id)

        if updated_users:
            db.session.commit()
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
    """접속 기기 User-Agent 판별."""
    user_agent = request.headers.get('User-Agent', '').lower()
    mobile_keywords = ['android', 'iphone', 'ipad', 'ipod', 'mobile', 'webos', 'opera mini']
    return any(keyword in user_agent for keyword in mobile_keywords)

# ==========================================
# 기본 루트 접속 라우트
# ==========================================
@app.route('/')
def index():
    """접속 환경(모바일/PC)에 따라 첫 화면 자동 분기"""
    if is_mobile_request():
        return render_template('user_web.html')
    return render_template('admin_web.html')

# ==========================================
# LocalTunnel 터널링 실행 스레드
# ==========================================
def start_localtunnel():
    """Flask 서버 실행 후 백그라운드에서 localtunnel 실행."""
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        time.sleep(2.0)
        try:
            cmd = ["lt", "--port", "5000", "--print-uri"]
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, shell=True)
            
            print("\n" + "=" * 65)
            print("[Localtunnel 시작 중...]")
            print("=" * 65)
            
            for line in process.stdout:
                if "https://" in line:
                    url = line.strip()
                    print(f"\n외부 접속 주소 생성 성공!")
                    print(f"공유 링크 : {url}")
                    print("=" * 65 + "\n")
                    break
        except Exception as e:
            print(f"Localtunnel 실행 실패: {e}")

if __name__ == '__main__':
    threading.Thread(target=start_localtunnel, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=True)