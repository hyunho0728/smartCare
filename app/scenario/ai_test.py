import os
import sys

# 현재 파일 기준 상위 폴더(app 폴더) 경로를 파이썬 검색 경로에 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

import datetime
from app import app
from models.models import db, User, HealthStatus, Worker

def create_ai_test_data():
    with app.app_context():
        # 1. AI 테스트 담당 사회복지사 생성/갱신
        worker_data = {
            "login_id": "ai_worker",
            "password": "1234",
            "name": "AI테스트복지사_김케어",
            "phone_number": "01077771111",
            "email": "ai_worker@smartcare.test",
            "org": "스마트케어 AI테스트센터",
            "address": "서울시 강남구"
        }

        worker = Worker.query.filter_by(login_id=worker_data["login_id"]).first()
        if not worker:
            worker = Worker.query.filter_by(phone_number=worker_data["phone_number"]).first()

        if worker:
            worker.login_id = worker_data["login_id"]
            worker.password = worker_data["password"]
            worker.name = worker_data["name"]
            worker.phone_number = worker_data["phone_number"]
            worker.email = worker_data["email"]
            worker.org = worker_data["org"]
            worker.address = worker_data["address"]
        else:
            worker = Worker(**worker_data)
            db.session.add(worker)

        db.session.commit()
        worker_id = worker.worker_id

        now = datetime.datetime.now()

        # ----------------------------------------------------
        # 시나리오 A: [이상 징후 발생] 홍길동 어르신
        # - 평소 아침 8시에 입력하다가 갑자기 오후 3시 입력 (Isolation Forest 감지)
        # - 최근 5일간 건강 척도 지속 하락 (5 -> 4 -> 3 -> 2 -> 1)
        # - 최근 3회 이상 결식 발생
        # ----------------------------------------------------
        elder_a = User.query.filter_by(phone_number="01099991111").first()
        if not elder_a:
            elder_a = User(
                name="AI이상탐지_홍길동",
                age=82,  # 고령 페널티 대상
                address="서울시 강남구 역삼동",
                phone_number="01099991111",
                has_underlying_disease=True,  # 기저질환 페널티 대상
                note="고혈압, 당뇨",
                worker_id=worker_id
            )
            db.session.add(elder_a)
            db.session.commit()
        elder_a.worker_id = worker_id

        # 과거 6일간 기록 (평소 08:00 입력, 건강 5에서 점차 악화)
        # 기존 기록 초기화
        HealthStatus.query.filter_by(user_id=elder_a.user_id).delete()
        
        # 6일 전 ~ 1일 전 데이터 (08:00 규칙적 입력)
        for i, cond in zip(range(6, 0, -1), [5, 4, 3, 2, 2, 1]):
            past_date = now - datetime.timedelta(days=i)
            rec_time = past_date.replace(hour=8, minute=10, second=0)
            db.session.add(HealthStatus(
                user_id=elder_a.user_id,
                condition_level=cond,
                breakfast_status="결식" if i in [1, 2, 3] else "완료",
                lunch_status="완료",
                dinner_status="완료",
                target_date=past_date.date(),
                recorded_at=rec_time
            ))

        # 오늘 데이터: 평소 8시가 아닌 오후 15:30에 늦게 입력 (시간 이상치 유발)
        db.session.add(HealthStatus(
            user_id=elder_a.user_id,
            condition_level=1,
            breakfast_status="결식",
            lunch_status="결식",
            dinner_status="완료",
            target_date=now.date(),
            recorded_at=now.replace(hour=15, minute=30, second=0)
        ))

        # ----------------------------------------------------
        # 시나리오 B: [안정적인 정상 패턴] 이순신 어르신
        # - 매일 아침 08:30경 규칙적 입력
        # - 건강 척도 '좋음(4~5)' 유지, 결식 없음
        # ----------------------------------------------------
        elder_b = User.query.filter_by(phone_number="01099992222").first()
        if not elder_b:
            elder_b = User(
                name="AI정상_이순신",
                age=74,
                address="서울시 강남구 삼성동",
                phone_number="01099992222",
                has_underlying_disease=False,
                note="없음",
                worker_id=worker_id
            )
            db.session.add(elder_b)
            db.session.commit()
        elder_b.worker_id = worker_id

        HealthStatus.query.filter_by(user_id=elder_b.user_id).delete()
        for i in range(6, -1, -1):
            past_date = now - datetime.timedelta(days=i)
            rec_time = past_date.replace(hour=8, minute=30, second=0)
            db.session.add(HealthStatus(
                user_id=elder_b.user_id,
                condition_level=5,
                breakfast_status="완료",
                lunch_status="완료",
                dinner_status="완료",
                target_date=past_date.date(),
                recorded_at=rec_time
            ))

        # ----------------------------------------------------
        # 시나리오 C: [6일 전까지만 건강 상태 입력] 강감찬 어르신
        # - 마지막 건강 상태 입력이 6일 전
        # - 이후 5일 전 ~ 오늘까지 건강 상태 입력 없음
        # - 장기 미입력 위험도 및 안부 확인 필요 케이스 검증
        # ----------------------------------------------------
        elder_c = User.query.filter_by(phone_number="01099995555").first()
        if not elder_c:
            elder_c = User(
                name="AI미입력_강감찬",
                age=79,
                address="서울시 강남구 역삼동",
                phone_number="01099995555",
                has_underlying_disease=True,
                note="고혈압",
                worker_id=worker_id
            )
            db.session.add(elder_c)
            db.session.commit()
        elder_c.worker_id = worker_id

        HealthStatus.query.filter_by(user_id=elder_c.user_id).delete()
        last_input_date = now - datetime.timedelta(days=6)
        db.session.add(HealthStatus(
            user_id=elder_c.user_id,
            condition_level=4,
            breakfast_status="완료",
            lunch_status="완료",
            dinner_status="완료",
            target_date=last_input_date.date(),
            recorded_at=last_input_date.replace(hour=8, minute=20, second=0)
        ))

        # ----------------------------------------------------
        # 시나리오 D: [최근 6일간 성실 입력] 을지문덕 어르신
        # - 최근 6일 동안 매일 아침 08:20경 규칙적 입력
        # - 건강 척도 '좋음(4~5)' 유지, 결식 없음
        # - 기록 누적 기간이 6일치인 정상 입력 케이스 검증
        # ----------------------------------------------------
        elder_d = User.query.filter_by(phone_number="01099994444").first()
        if not elder_d:
            elder_d = User(
                name="AI성실6일_을지문덕",
                age=76,
                address="서울시 강남구 대치동",
                phone_number="01099994444",
                has_underlying_disease=False,
                note="없음",
                worker_id=worker_id
            )
            db.session.add(elder_d)
            db.session.commit()
        elder_d.worker_id = worker_id

        HealthStatus.query.filter_by(user_id=elder_d.user_id).delete()
        for i in range(5, -1, -1):
            past_date = now - datetime.timedelta(days=i)
            rec_time = past_date.replace(hour=8, minute=20, second=0)
            db.session.add(HealthStatus(
                user_id=elder_d.user_id,
                condition_level=5 if i % 2 == 0 else 4,
                breakfast_status="완료",
                lunch_status="완료",
                dinner_status="완료",
                target_date=past_date.date(),
                recorded_at=rec_time
            ))

        # ----------------------------------------------------
        # 시나리오 E: [최근 6일간 성실 입력] 박영자 어르신
        # - 최근 6일 동안 매일 오전 09:00경 규칙적 입력
        # - 건강 척도 '보통~좋음(3~4)' 유지, 결식 없음
        # - 실제 사람 이름 형태의 정상 입력 케이스 검증
        # ----------------------------------------------------
        elder_e = User.query.filter_by(phone_number="01099996666").first()
        if not elder_e:
            elder_e = User(
                name="박영자",
                age=81,
                address="서울시 강남구 논현동",
                phone_number="01099996666",
                has_underlying_disease=True,
                note="관절염",
                worker_id=worker_id
            )
            db.session.add(elder_e)
            db.session.commit()
        elder_e.worker_id = worker_id

        HealthStatus.query.filter_by(user_id=elder_e.user_id).delete()
        for i in range(5, -1, -1):
            past_date = now - datetime.timedelta(days=i)
            rec_time = past_date.replace(hour=9, minute=0, second=0)
            db.session.add(HealthStatus(
                user_id=elder_e.user_id,
                condition_level=4 if i % 2 == 0 else 3,
                breakfast_status="완료",
                lunch_status="완료",
                dinner_status="완료",
                target_date=past_date.date(),
                recorded_at=rec_time
            ))

        db.session.commit()
        print("✅ AI 테스트 시나리오 데이터가 성공적으로 생성되었습니다!")
        print(" - 테스트 복지사 로그인 ID: ai_worker")
        print(" - 테스트 복지사 비밀번호: 1234")

if __name__ == "__main__":
    create_ai_test_data()
