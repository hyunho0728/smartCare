"""최근 30일 시연 데이터 생성. 미리보기: python app/scenario/demo_data.py --preview"""

import argparse
import datetime as dt
import os
from pathlib import Path
import re
import secrets
import sys
from types import SimpleNamespace
from unittest.mock import patch

from dotenv import load_dotenv
from flask import Flask
from sqlalchemy.engine import URL

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from models.models import db, Worker, User, HealthStatus, LoginHistory, RiskAnalysis
from services import social_worker_ai_service as risk_service

LOGIN_ID = "demo_worker"
PASSWORD = "Demo1234!"
IRREGULAR_OFFSETS = (29, 27, 24, 20, 19, 15, 11, 10, 6, 4, 1, 0)
PROFILES = (
    ("김영숙", 74, "서울특별시 강남구 대치동", "관절염", "성실"),
    ("박정호", 78, "서울특별시 강남구 역삼동", "고혈압", "불규칙"),
    ("이순자", 82, "서울특별시 강남구 개포동", "당뇨병", "미입력"),
)


def create_demo_app():
    """기존 테이블만 연결하며 앱 초기화의 DDL/업로드 폴더 생성을 피한다."""
    load_dotenv(APP_DIR.parent / ".env")
    names = ("DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_NAME")
    settings = {name: os.getenv(name) for name in names}
    missing = [name for name, value in settings.items() if not value]
    if missing:
        raise RuntimeError("DB 설정 누락: " + ", ".join(missing))
    app = Flask("smartcare_demo")
    app.config["SQLALCHEMY_DATABASE_URI"] = URL.create(
        "mysql+pymysql", username=settings["DB_USER"],
        password=settings["DB_PASSWORD"], host=settings["DB_HOST"],
        port=int(settings["DB_PORT"]), database=settings["DB_NAME"],
        query={"charset": "utf8mb4"},
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"connect_args": {"connect_timeout": 10}}
    db.init_app(app)
    return app


def normalize_phone(value):
    return re.sub(r"\D", "", value or "")


def existing_phones():
    used = set()
    for model, column in (
        (Worker, Worker.phone_number), (User, User.phone_number),
        (User, User.emergency_contact), (LoginHistory, LoginHistory.phone_number),
    ):
        used.update(normalize_phone(value) for (value,) in db.session.query(column).all())
    return used


def allocate_phones(used, count=7):
    used = set(used)
    phones = []
    while len(phones) < count:
        phone = f"010{secrets.randbelow(100000000):08d}"
        if phone not in used:
            phones.append(phone)
            used.add(phone)
    return phones


def calculate_at(user, health, logins, at):
    """실제 계산 로직을 사용하되 서비스 내부 시계만 해당 날짜로 고정한다."""
    class HistoricalDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return at if tz is None else at.replace(tzinfo=dt.timezone(dt.timedelta(hours=9))).astimezone(tz)

    clock = SimpleNamespace(datetime=HistoricalDateTime, timedelta=dt.timedelta)
    with patch.object(risk_service, "datetime", clock):
        return risk_service.calculate_risk(
            user,
            sorted((h for h in health if h.recorded_at <= at), key=lambda h: h.recorded_at, reverse=True),
            sorted((l for l in logins if l.auth_time <= at), key=lambda l: l.auth_time, reverse=True),
        )


def build_records(now, phones):
    start = now.date() - dt.timedelta(days=29)
    created_at = dt.datetime.combine(start, dt.time.min)
    worker = Worker(
        login_id=LOGIN_ID, password=PASSWORD, name="최지은", phone_number=phones[0],
        email="demo_worker@smartcare.test", org="스마트케어 시연센터",
        address="서울특별시 강남구", created_at=created_at, updated_at=now,
    )
    bundles = []
    for index, (name, age, address, disease, pattern) in enumerate(PROFILES):
        user = User(
            name=name, age=age, address=address, phone_number=phones[1 + index * 2],
            emergency_contact=phones[2 + index * 2], has_underlying_disease=True,
            underlying_disease_severity=0, note=disease, is_active=True,
            created_at=created_at, updated_at=now,
        )
        offsets = range(29, -1, -1) if index == 0 else IRREGULAR_OFFSETS if index == 1 else ()
        health, logins, analyses = [], [], []
        for sequence, offset in enumerate(offsets):
            date = now.date() - dt.timedelta(days=offset)
            hour, minute = (8, 20 + sequence % 5) if index == 0 else ((7, 14, 9, 19, 11, 16)[sequence % 6], sequence * 7 % 60)
            recorded_at = dt.datetime.combine(date, dt.time(hour, minute)) if offset else now
            health.append(HealthStatus(
                condition_level=4 + sequence % 2 if index == 0 else 3 + sequence % 3,
                breakfast_status="완료",
                lunch_status="결식" if index == 1 and sequence % 3 == 2 else "완료",
                dinner_status="완료", target_date=date, recorded_at=recorded_at,
            ))
            logins.append(LoginHistory(
                phone_number=user.phone_number, ip_address="192.0.2.10",
                user_agent="SmartCare demo sample", auth_time=max(created_at, recorded_at - dt.timedelta(minutes=2)),
            ))
        for day in range(30):
            date = start + dt.timedelta(days=day)
            at = now if date == now.date() else dt.datetime.combine(date, dt.time(23, 59))
            result = calculate_at(user, health, logins, at)
            analyses.append(RiskAnalysis(
                risk_score=result["score"], risk_level=result["risk_level_db"],
                is_anomaly=result["is_anomaly"],
                anomaly_type=", ".join(result["anomaly_types"]) or "정상",
                time_deviation=result["time_deviation"],
                predicted_risk_prob=result["predicted_risk_prob"],
                ai_summary=result["ai_summary"], analyzed_at=at,
            ))
        bundles.append((user, health, logins, analyses))
    return worker, bundles


def create_demo_data(preview=False):
    # DB의 기존 naive datetime 관례를 유지하되 날짜는 한국 시간으로 정한다.
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).replace(tzinfo=None)
    try:
        if Worker.query.filter_by(login_id=LOGIN_ID).first():
            print("demo_worker 계정이 이미 존재합니다. 기존 데이터 변경 없이 종료합니다.")
            return False
        used = existing_phones()
        phones = allocate_phones(used)
        worker, bundles = build_records(now, phones)
        assert len(set(phones)) == 7 and not set(phones).intersection(used)
        print(f"기간: {now.date() - dt.timedelta(days=29)} ~ {now.date()} (30일)")
        print(f"사회복지사: {worker.name} / {worker.phone_number} / {LOGIN_ID}")
        for index, (user, health, logins, analyses) in enumerate(bundles):
            expected = (30, 12, 0)[index]
            assert len(health) == len(logins) == expected and len(analyses) == 30
            assert all(h.recorded_at <= now for h in health)
            print(f"{user.name} ({PROFILES[index][4]}, {user.age}세, {user.note}): "
                  f"전화 {user.phone_number}, 보호자 {user.emergency_contact}, "
                  f"건강 {len(health)} / 접속 {len(logins)} / 분석 {len(analyses)}건")
        print("전화번호 7개: 서로 및 기존 DB와 중복 없음")
        if preview:
            db.session.rollback()
            print("미리보기 완료: DB에 저장하지 않았습니다. 실제 생성 시 번호는 새로 배정됩니다.")
            return False
        db.session.add(worker)
        db.session.flush()
        for user, health, logins, analyses in bundles:
            user.worker_id = worker.worker_id
            db.session.add(user)
            db.session.flush()
            for record in health + logins + analyses:
                record.user_id = user.user_id
                db.session.add(record)
        db.session.flush()
        assert User.query.filter_by(worker_id=worker.worker_id).count() == 3
        for index, (user, _, _, _) in enumerate(bundles):
            assert HealthStatus.query.filter_by(user_id=user.user_id).count() == (30, 12, 0)[index]
            assert LoginHistory.query.filter_by(user_id=user.user_id).count() == (30, 12, 0)[index]
            assert RiskAnalysis.query.filter_by(user_id=user.user_id).count() == 30
        db.session.commit()
        print(f"저장 완료. 사회복지사 로그인: {LOGIN_ID} / {PASSWORD}")
        return True
    except Exception:
        db.session.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="DB에 저장하지 않고 생성 내용 확인")
    args = parser.parse_args()
    app = create_demo_app()
    with app.app_context():
        create_demo_data(preview=args.preview)


if __name__ == "__main__":
    main()
