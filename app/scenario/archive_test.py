import os
import sys
import datetime

# app 폴더 경로 등록
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from app import app, archive_old_records
from models.models import db, User, HealthStatus, RiskAnalysis, HealthStatusHistory, RiskAnalysisHistory

def run_archive_test():
    with app.app_context():
        print("=" * 60)
        print("🧪 [req_03_01_04] 14일 초과 데이터 아카이빙 이관 테스트 시작")
        print("=" * 60)

        # 1. 테스트용 어르신 준비
        test_user = User.query.filter_by(phone_number="01088887777").first()
        if not test_user:
            test_user = User(
                name="아카이빙테스터",
                age=75,
                address="서울시 강남구 역삼동",
                phone_number="01088887777",
                is_active=True
            )
            db.session.add(test_user)
            db.session.commit()

        # 기존 테스트 기록 정리
        HealthStatus.query.filter_by(user_id=test_user.user_id).delete()
        RiskAnalysis.query.filter_by(user_id=test_user.user_id).delete()
        HealthStatusHistory.query.filter_by(user_id=test_user.user_id).delete()
        RiskAnalysisHistory.query.filter_by(user_id=test_user.user_id).delete()
        db.session.commit()

        now = datetime.datetime.now()

        # 2. 더미 데이터 생성
        # (A) 15일 전 데이터: 14일 초과 -> 아카이빙 대상
        past_15d = now - datetime.timedelta(days=15)
        old_h = HealthStatus(
            user_id=test_user.user_id,
            condition_level=3,
            breakfast_status="완료", lunch_status="완료", dinner_status="완료",
            target_date=past_15d.date(),
            recorded_at=past_15d
        )
        db.session.add(old_h)
        db.session.flush()

        old_r = RiskAnalysis(
            user_id=test_user.user_id,
            risk_score=90.0,
            risk_level="SAFE",
            predicted_risk_prob=10.0,
            analyzed_at=past_15d
        )
        db.session.add(old_r)

        # (B) 3일 전 데이터: 14일 이내 -> 원본 유지 대상
        past_3d = now - datetime.timedelta(days=3)
        recent_h = HealthStatus(
            user_id=test_user.user_id,
            condition_level=5,
            breakfast_status="완료", lunch_status="완료", dinner_status="완료",
            target_date=past_3d.date(),
            recorded_at=past_3d
        )
        db.session.add(recent_h)
        db.session.flush()

        recent_r = RiskAnalysis(
            user_id=test_user.user_id,
            risk_score=95.0,
            risk_level="SAFE",
            predicted_risk_prob=5.0,
            analyzed_at=past_3d
        )
        db.session.add(recent_r)
        db.session.commit()

        print("1. 이관 전 상태 확인:")
        print(f" - HEALTH_STATUS (원본) 건수: {HealthStatus.query.filter_by(user_id=test_user.user_id).count()}건 (15일 전 1건 + 3일 전 1건)")
        print(f" - RISK_ANALYSIS (원본) 건수: {RiskAnalysis.query.filter_by(user_id=test_user.user_id).count()}건")
        print(f" - HEALTH_STATUS_HISTORY 건수: {HealthStatusHistory.query.filter_by(user_id=test_user.user_id).count()}건")

        # 3. 아카이빙 로직 실행
        print("\n2. 아카이빙 로직(archive_old_records) 실행 중...")
        result = archive_old_records()
        print(f" - 실행 결과: {result}")

        # 4. 이관 후 결과 검증
        remain_h = HealthStatus.query.filter_by(user_id=test_user.user_id).all()
        remain_r = RiskAnalysis.query.filter_by(user_id=test_user.user_id).all()
        hist_h = HealthStatusHistory.query.filter_by(user_id=test_user.user_id).all()
        hist_r = RiskAnalysisHistory.query.filter_by(user_id=test_user.user_id).all()

        print("\n3. 이관 후 최종 상태 검증:")
        print(f" - HEALTH_STATUS (원본 남은 건수): {len(remain_h)}건 (3일 전 데이터만 남음)")
        print(f" - HEALTH_STATUS_HISTORY (이관된 건수): {len(hist_h)}건 (15일 전 데이터 이관됨)")
        print(f" - RISK_ANALYSIS_HISTORY (이관된 건수): {len(hist_r)}건")

        # 검증 판정
        if len(remain_h) == 1 and len(hist_h) == 1 and len(hist_r) == 1:
            print("\n🎉 [성공] 14일 초과 데이터만 HISTORY 테이블로 안전하게 이관되고 원본은 정상 삭제되었습니다!")
        else:
            print("\n❌ [실패] 데이터 이관 건수가 일치하지 않습니다.")

if __name__ == '__main__':
    run_archive_test()