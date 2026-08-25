import numpy as np
import datetime
from sklearn.ensemble import IsolationForest

def evaluate_and_record_risk(user, health_history, login_history, db_session, RiskAnalysisModel):
    """
    어르신의 최신 상태 및 시계열 기록을 기반으로 위험 점수를 계산하고,
    RISK_ANALYSIS 테이블에 새 레코드를 생성하여 저장합니다.
    """
    latest_health = health_history[0] if health_history else None

    # ==========================================================
    # 1단계: 규칙 기반 기본 점수 산출 (시간 비례 선형 감점 모델)
    # ==========================================================
    risk_score = 100
    score_breakdown = [{"item": "기본 만점", "score": "100점", "type": "base"}]

    if user.age >= 80:
        risk_score -= 10
        score_breakdown.append({"item": f"고령 페널티 ({user.age}세)", "score": "-10점", "type": "minus"})

    if user.has_underlying_disease:
        risk_score -= 10
        disease_name = user.note if user.note else "기저질환"
        score_breakdown.append({"item": f"기저질환 ({disease_name})", "score": "-10점", "type": "minus"})

    if latest_health:
        if '결식' in [latest_health.breakfast_status, latest_health.lunch_status, latest_health.dinner_status]:
            risk_score -= 20
            score_breakdown.append({"item": "식사 결식 페널티", "score": "-20점", "type": "minus"})

        elapsed_hours = int((datetime.datetime.now() - latest_health.recorded_at).total_seconds() // 3600)
        if elapsed_hours > 0:
            time_penalty = elapsed_hours * 2
            risk_score -= time_penalty
            score_breakdown.append({"item": f"미입력 경과 ({elapsed_hours}시간)", "score": f"-{time_penalty}점", "type": "minus"})
    else:
        risk_score -= 40
        score_breakdown.append({"item": "건강 상태 미등록", "score": "-40점", "type": "minus"})

    # ==========================================================
    # 2단계: 머신러닝 기반 시계열 이상 탐지 (Isolation Forest & Trend)
    # ==========================================================
    ai_penalty = 0
    anomalies = []
    trend_desc = []
    is_anomaly = False
    anomaly_types = []
    time_dev_minutes = 0

    if health_history and len(health_history) >= 4:
        # 1. 입력 시간대 이상치 탐지 (Isolation Forest)
        record_hours = [[r.recorded_at.hour + r.recorded_at.minute / 60.0] for r in health_history if r.recorded_at]
        if len(record_hours) >= 5:
            X = np.array(record_hours)
            iso = IsolationForest(contamination=0.1, random_state=42)
            iso.fit(X)
            
            latest_hour = record_hours[0]
            if iso.predict([latest_hour])[0] == -1:
                mean_hour = np.mean(X[1:])
                diff = abs(latest_hour[0] - mean_hour)
                if diff >= 3.0:
                    time_dev_minutes = int(diff * 60)
                    ai_penalty += 15
                    is_anomaly = True
                    anomaly_types.append("시간 불규칙성")
                    anomalies.append({
                        "item": f"AI 생활패턴 불규칙 ({diff:.1f}시간 편차)",
                        "score": "-15점",
                        "type": "minus"
                    })
                    trend_desc.append(f"평소 입력 시간대(평균 {int(mean_hour)}시)와 {diff:.1f}시간의 큰 시차가 발생했습니다.")

        # 2. 7일 건강 점수 연속 하락 추세 감지
        recent_conds = [r.condition_level for r in health_history[:7]]
        if len(recent_conds) >= 3:
            is_declining = all(recent_conds[i] <= recent_conds[i+1] for i in range(len(recent_conds)-1)) and (recent_conds[0] < recent_conds[-1])
            if is_declining:
                ai_penalty += 15
                is_anomaly = True
                anomaly_types.append("건강 연속 악화")
                anomalies.append({
                    "item": "AI 건강 척도 하락세 감지 (최근 연속 악화)",
                    "score": "-15점",
                    "type": "minus"
                })
                trend_desc.append("최근 건강 상태가 지속 하락하는 악화 흐름이 나타났습니다.")

        # 3. 7일 내 결식 빈도 급증 분석
        skip_count = sum(1 for r in health_history[:7] if '결식' in [r.breakfast_status, r.lunch_status, r.dinner_status])
        if skip_count >= 3:
            ai_penalty += 10
            is_anomaly = True
            anomaly_types.append("잦은 결식")
            anomalies.append({
                "item": f"AI 영양 불균형 경고 (최근 {skip_count}회 결식)",
                "score": "-10점",
                "type": "minus"
            })
            trend_desc.append(f"최근 7일 중 {skip_count}회의 결식 패턴이 감지되었습니다.")

    # AI 감점 합산
    risk_score = max(0, min(100, risk_score - ai_penalty))
    score_breakdown.extend(anomalies)

    # 4단계 위험도 분류
    if risk_score >= 80:
        risk_level_str = 'SAFE'
        risk_level_code = 'safe'
    elif risk_score >= 60:
        risk_level_str = 'WATCH'
        risk_level_code = 'watch'
    elif risk_score >= 40:
        risk_level_str = 'WARN'
        risk_level_code = 'warn'
    else:
        risk_level_str = 'DANGER'
        risk_level_code = 'danger'
        is_anomaly = True # 40점 미만 시 자동으로 이상 징후 확정

    ai_summary = " ".join(trend_desc) + " 사회복지사의 확인 및 관찰이 권장됩니다." if trend_desc else "최근 건강 상태와 입력 패턴이 안정적인 정상 생활을 유지하고 있습니다."

    # ==========================================================
    # 3단계: RISK_ANALYSIS 테이블에 분석 결과 적재 (Insert)
    # ==========================================================
    new_risk_analysis = RiskAnalysisModel(
        user_id=user.user_id,
        risk_score=risk_score,
        risk_level=risk_level_str,
        is_anomaly=is_anomaly,
        anomaly_type=", ".join(anomaly_types) if anomaly_types else "정상",
        time_deviation=time_dev_minutes,
        predicted_risk_prob=round(100.0 - risk_score, 2),
        ai_summary=ai_summary,
        analyzed_at=datetime.datetime.now()
    )
    db_session.add(new_risk_analysis)
    db_session.commit()

    return {
        "score": risk_score,
        "risk_level": risk_level_code,
        "score_breakdown": score_breakdown,
        "ai_summary": ai_summary,
        "analysis_id": new_risk_analysis.analysis_id
    }