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
        # 1. 담당 복지사 확인 (첫 번째 복지사 지정)
        worker = Worker.query.first()
        worker_id = worker.worker_id if worker else None

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

        db.session.commit()
        print("✅ AI 테스트 시나리오 데이터가 성공적으로 생성되었습니다!")

if __name__ == "__main__":
    create_ai_test_data()