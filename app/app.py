import os
import urllib.parse
import datetime
import subprocess
import shutil
import threading
import time
from flask import Flask, render_template, request, session, redirect
from dotenv import load_dotenv
from models.models import db, User, HealthStatus, LoginHistory, RiskAnalysis
from services.ai_service import evaluate_and_record_risk

# 작업자별 Blueprint 임포트
from routes.social_worker import worker_bp
from routes.user import user_bp

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(os.path.dirname(BASE_DIR), ".env"))

app = Flask(__name__)
app.secret_key = "smartcare-secret-key-replace-with-env"

# ==========================================
# MySQL 데이터베이스 설정
# ==========================================
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")

missing_db_settings = [
    name for name, value in {
        "DB_USER": DB_USER,
        "DB_PASSWORD": DB_PASSWORD,
        "DB_HOST": DB_HOST,
        "DB_PORT": DB_PORT,
        "DB_NAME": DB_NAME,
    }.items() if not value
]
if missing_db_settings:
    raise RuntimeError(
        "Missing database settings in .env: "
        + ", ".join(missing_db_settings)
    )

encoded_password = urllib.parse.quote_plus(DB_PASSWORD)
app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{DB_USER}:{encoded_password}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 건강검진표 업로드 경로 설정
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
app.register_blueprint(user_bp)

# ==========================================
# 공통 스케줄러 & 유틸리티
# ==========================================
MEAL_DEADLINES = {
    'breakfast': datetime.time(10, 0),  # 아침 마감 (10:00)
    'lunch': datetime.time(15, 0),      # 점심 마감 (15:00)
    'dinner': datetime.time(21, 0),     # 저녁 마감 (21:00)
}

def check_and_update_missed_meals():
    """마감 시각이 지난 식사 예정 상태를 HealthStatus ENUM의 '결식'으로 변경한다."""
    now = datetime.datetime.now()
    current_time = now.time()
    today = now.date()

    try:
        records = HealthStatus.query.filter(HealthStatus.target_date <= today).all()
        updated_users = set()

        for r in records:
            changed = False
            is_today = (r.target_date == today)

            # HealthStatus의 허용 상태값은 '완료', '예정', '결식'이다.
            for meal, deadline in MEAL_DEADLINES.items():
                status_field = f'{meal}_status'
                if getattr(r, status_field) != '예정':
                    continue

                if not is_today or current_time >= deadline:
                    setattr(r, status_field, '결식')
                    changed = True

            if changed:
                updated_users.add(r.user_id)

        if updated_users:
            db.session.commit()
            for uid in updated_users:
                user = db.session.get(User, uid)
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
            lt_path = shutil.which("lt")
            if not lt_path:
                print("\n" + "=" * 65)
                print("[Localtunnel 실행 불가]")
                print("lt 명령을 찾을 수 없습니다.")
                print("Node.js 설치 후 다음 명령으로 localtunnel을 설치하세요:")
                print("npm install -g localtunnel")
                print("=" * 65 + "\n")
                return

            cmd = [lt_path, "--port", "5000", "--print-uri"]
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
            
            print("\n" + "=" * 65)
            print("[Localtunnel 시작 중...]")
            print("=" * 65)
            
            for line in process.stdout:
                print(line.rstrip())
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
