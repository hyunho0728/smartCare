import numpy as np
import datetime
import os
import time
from google import genai
from sklearn.ensemble import IsolationForest
from services.ai_service import generate_content, resolve_model, AIError


class LifePatternAIError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


DISEASE_PENALTY_RULES = [
    {"standard_name": "치매 / 인지장애", "penalty": 20, "keywords": ["치매", "인지장애", "알츠하이머"]},
    {"standard_name": "뇌졸중", "penalty": 18, "keywords": ["뇌졸중", "중풍", "뇌경색", "뇌출혈"]},
    {"standard_name": "심혈관질환", "penalty": 15, "keywords": ["심혈관", "심장", "협심증", "심근경색", "부정맥"]},
    {"standard_name": "파킨슨병", "penalty": 15, "keywords": ["파킨슨"]},
    {"standard_name": "암", "penalty": 15, "keywords": ["암", "악성종양"]},
    {"standard_name": "당뇨병", "penalty": 12, "keywords": ["당뇨", "혈당"]},
    {"standard_name": "고혈압", "penalty": 10, "keywords": ["고혈압", "혈압"]},
    {"standard_name": "관절염", "penalty": 5, "keywords": ["관절염", "관절", "류마티스"]},
]
DEFAULT_DISEASE_PENALTY = 10


def _normalize_disease_text(value):
    return "".join(ch for ch in str(value or "").lower() if ch not in " /-_")


def _to_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def calculate_underlying_disease_penalty(user):
    """기저질환 종류에 따라 위험 점수 감점 폭을 산정합니다."""
    if not getattr(user, "has_underlying_disease", False):
        return 0, None

    disease_name = (getattr(user, "note", None) or "기저질환").strip()
    normalized_disease_name = _normalize_disease_text(disease_name)
    matched_rules = []

    for rule in DISEASE_PENALTY_RULES:
        keywords = rule.get("keywords") or []
        normalized_keywords = [_normalize_disease_text(keyword) for keyword in keywords]
        if any(keyword and keyword in normalized_disease_name for keyword in normalized_keywords):
            matched_rules.append(rule)

    if not matched_rules:
        return DEFAULT_DISEASE_PENALTY, disease_name

    matched_rule = max(matched_rules, key=lambda rule: _to_int(rule.get("penalty"), 0))
    penalty = _to_int(matched_rule.get("penalty"), DEFAULT_DISEASE_PENALTY)
    standard_name = matched_rule.get("standard_name") or disease_name
    normalized_standard_name = _normalize_disease_text(standard_name)
    is_same_category = (
        normalized_standard_name in normalized_disease_name or
        normalized_disease_name in normalized_standard_name
    )
    display_name = disease_name if is_same_category else f"{disease_name} / {standard_name}"
    return penalty, display_name


def _build_ai_summary(latest_health, elapsed_days, recent_7d_count, risk_score, trend_desc):
    """분석 데이터의 충분성, 입력 지연, 위험 점수를 함께 반영해 생활 패턴 문구를 생성합니다."""
    if not latest_health:
        return "건강 상태 입력 기록이 없어 생활 패턴을 분석할 수 없습니다. 첫 건강 상태 입력 및 안부 확인이 필요합니다."

    if elapsed_days is not None and elapsed_days >= 7:
        return f"최근 {elapsed_days}일간 건강 상태 입력이 없습니다. 현재 생활 패턴을 정상으로 판단하기 어려우며 사회복지사의 안부 확인이 필요합니다."

    if elapsed_days is not None and elapsed_days >= 3:
        return f"마지막 건강 상태 입력 후 {elapsed_days}일이 경과했습니다. 최근 생활 상태 확인이 필요합니다."

    if recent_7d_count < 3:
        return "최근 7일간 생활 패턴을 판단하기 위한 데이터가 부족합니다. 지속적인 건강 상태 입력이 필요합니다."

    if trend_desc:
        return " ".join(trend_desc) + " 사회복지사의 확인 및 관찰이 권장됩니다."

    if risk_score < 40:
        return "현재 위험 점수가 낮은 상태입니다. 최근 건강 상태와 생활 기록을 확인할 필요가 있습니다."

    if risk_score < 60:
        return "최근 건강 및 생활 기록에서 주의가 필요한 상태가 확인되었습니다. 지속적인 관찰이 권장됩니다."

    if risk_score < 80:
        return "현재 큰 이상 징후는 확인되지 않았으나 일부 위험 요인이 있어 지속적인 관찰이 필요합니다."

    return "최근 건강 상태와 입력 패턴에서 특별한 이상 징후가 확인되지 않았습니다."


def _risk_level_from_score(risk_score):
    if risk_score >= 80:
        return 'SAFE', 'safe'
    if risk_score >= 60:
        return 'WATCH', 'watch'
    if risk_score >= 40:
        return 'WARN', 'warn'
    return 'DANGER', 'danger'


def _latest_time(records, attr_name):
    values = [getattr(record, attr_name, None) for record in records or []]
    values = [value for value in values if value]
    return max(values) if values else None


def _latest_analysis_covers_inputs(latest_analysis, health_history, login_history, max_age_minutes=60):
    if not latest_analysis or not latest_analysis.analyzed_at:
        return False

    if datetime.datetime.now() - latest_analysis.analyzed_at > datetime.timedelta(minutes=max_age_minutes):
        return False

    latest_health_at = _latest_time(health_history, "recorded_at")
    latest_login_at = _latest_time(login_history, "auth_time")

    if latest_health_at and latest_analysis.analyzed_at < latest_health_at:
        return False
    if latest_login_at and latest_analysis.analyzed_at < latest_login_at:
        return False

    return True


def _confidence_from_data(health_history, login_history, latest_health):
    health_count = len(health_history or [])
    login_count = len(login_history or [])

    if not latest_health:
        return {"label": "낮음", "score": 35}
    if health_count >= 5 and login_count >= 3:
        return {"label": "높음", "score": 85}
    if health_count >= 2 or login_count >= 2:
        return {"label": "보통", "score": 65}
    return {"label": "낮음", "score": 45}


def _result_from_analysis(record, score_breakdown=None, confidence=None, evidence=None, pattern_insights=None):
    risk_score = float(record.risk_score)
    _, risk_level_code = _risk_level_from_score(risk_score)

    return {
        "score": risk_score,
        "risk_level": risk_level_code,
        "score_breakdown": score_breakdown or [],
        "ai_summary": record.ai_summary,
        "analysis_id": record.analysis_id,
        "confidence": confidence or {"label": "보통", "score": 60},
        "evidence": evidence or [],
        "pattern_insights": pattern_insights or [],
        "reused": True
    }


def calculate_risk(user, health_history, login_history):
    """위험 점수, 분석 근거, 신뢰도를 계산합니다. DB 저장은 하지 않습니다."""
    now = datetime.datetime.now()
    latest_health = health_history[0] if health_history else None
    latest_recorded_at = latest_health.recorded_at if latest_health and latest_health.recorded_at else None
    elapsed_days = None
    seven_days_ago = now - datetime.timedelta(days=7)
    recent_7d = [
        r for r in health_history
        if r.recorded_at and r.recorded_at >= seven_days_ago
    ]

    # ==========================================================
    # 1단계: 규칙 기반 기본 점수 산출 (시간 비례 선형 감점 모델)
    # ==========================================================
    risk_score = 100
    score_breakdown = [{"item": "기본 만점", "score": "100점", "type": "base"}]
    evidence = []
    pattern_insights = []

    if user.age >= 80:
        risk_score -= 10
        score_breakdown.append({"item": f"고령 페널티 ({user.age}세)", "score": "-10점", "type": "minus"})
        evidence.append(f"나이 {user.age}세")

    disease_penalty, disease_name = calculate_underlying_disease_penalty(user)
    if disease_penalty > 0:
        risk_score -= disease_penalty
        score_breakdown.append({
            "item": f"기저질환 ({disease_name})",
            "score": f"-{disease_penalty}점",
            "type": "minus"
        })
        evidence.append(f"기저질환: {disease_name}")

    if latest_health:
        if '결식' in [latest_health.breakfast_status, latest_health.lunch_status, latest_health.dinner_status]:
            risk_score -= 20
            score_breakdown.append({"item": "식사 결식 페널티", "score": "-20점", "type": "minus"})
            evidence.append("최신 식사 기록에 결식 포함")
            pattern_insights.append({
                "title": "식사 패턴",
                "detail": "최신 식사 기록에 결식이 포함되어 있습니다.",
                "level": "warn"
            })

        if latest_recorded_at:
            elapsed = now - latest_recorded_at
            elapsed_days = int(elapsed.total_seconds() // 86400)
            elapsed_hours = int(elapsed.total_seconds() // 3600)
            time_penalty = elapsed_hours * 2
            if elapsed_hours > 0:
                risk_score -= time_penalty
                score_breakdown.append({"item": f"미입력 경과 ({elapsed_hours}시간)", "score": f"-{time_penalty}점", "type": "minus"})
                evidence.append(f"마지막 건강 입력 후 {elapsed_hours}시간 경과")
                pattern_insights.append({
                    "title": "입력 시간 패턴",
                    "detail": f"마지막 건강 상태 입력 후 {elapsed_hours}시간이 경과했습니다.",
                    "level": "danger" if elapsed_hours >= 24 else "watch"
                })
            else:
                pattern_insights.append({
                    "title": "입력 시간 패턴",
                    "detail": "최근 건강 상태 입력이 확인되었습니다.",
                    "level": "safe"
                })
    else:
        risk_score -= 40
        score_breakdown.append({"item": "건강 상태 미등록", "score": "-40점", "type": "minus"})
        evidence.append("건강 상태 입력 기록 없음")
        pattern_insights.append({
            "title": "입력 시간 패턴",
            "detail": "건강 상태 입력 기록이 없어 평소 패턴을 판단하기 어렵습니다.",
            "level": "danger"
        })

    latest_login_at = _latest_time(login_history, "auth_time")
    if latest_login_at:
        login_elapsed = now - latest_login_at
        login_elapsed_hours = int(login_elapsed.total_seconds() // 3600)
        if login_elapsed_hours >= 48:
            login_penalty = min(20, (login_elapsed_hours // 24) * 5)
            risk_score -= login_penalty
            score_breakdown.append({
                "item": f"앱 미접속 경과 ({login_elapsed_hours}시간)",
                "score": f"-{login_penalty}점",
                "type": "minus"
            })
            evidence.append(f"마지막 접속 후 {login_elapsed_hours}시간 경과")
            pattern_insights.append({
                "title": "접속 패턴",
                "detail": f"마지막 앱 접속 후 {login_elapsed_hours}시간이 지나 평소 활동 확인이 필요합니다.",
                "level": "warn"
            })
        else:
            pattern_insights.append({
                "title": "접속 패턴",
                "detail": "최근 앱 접속 기록이 확인되었습니다.",
                "level": "safe"
            })
    else:
        risk_score -= 10
        score_breakdown.append({"item": "로그인 기록 없음", "score": "-10점", "type": "minus"})
        evidence.append("로그인 기록 없음")
        pattern_insights.append({
            "title": "접속 패턴",
            "detail": "앱 접속 기록이 없어 생활 활동 패턴을 충분히 판단하기 어렵습니다.",
            "level": "watch"
        })

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
                    evidence.append(f"평소 입력 시간 대비 {diff:.1f}시간 편차")
                    pattern_insights.append({
                        "title": "입력 시간 이상 패턴",
                        "detail": f"평소 입력 시간대와 {diff:.1f}시간 차이가 감지되었습니다.",
                        "level": "warn"
                    })
                    trend_desc.append(f"평소 입력 시간대(평균 {int(mean_hour)}시)와 {diff:.1f}시간의 큰 시차가 발생했습니다.")

    # 2. 7일 건강 점수 연속 하락 추세 감지
    recent_conds = [r.condition_level for r in recent_7d]
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
            evidence.append("최근 건강 상태가 연속 악화")
            pattern_insights.append({
                "title": "건강 변화 패턴",
                "detail": "최근 건강 상태가 연속으로 나빠지는 흐름이 감지되었습니다.",
                "level": "danger"
            })
            trend_desc.append("최근 건강 상태가 지속 하락하는 악화 흐름이 나타났습니다.")

    # 3. 7일 내 결식 빈도 급증 분석
    skip_count = sum(1 for r in recent_7d if '결식' in [r.breakfast_status, r.lunch_status, r.dinner_status])
    if skip_count >= 3:
        ai_penalty += 10
        is_anomaly = True
        anomaly_types.append("잦은 결식")
        anomalies.append({
            "item": f"AI 영양 불균형 경고 (최근 {skip_count}회 결식)",
            "score": "-10점",
            "type": "minus"
        })
        evidence.append(f"최근 7일 결식 {skip_count}회")
        pattern_insights.append({
            "title": "식사 패턴",
            "detail": f"최근 7일 동안 결식이 {skip_count}회 감지되었습니다.",
            "level": "warn"
        })
        trend_desc.append(f"최근 7일 중 {skip_count}회의 결식 패턴이 감지되었습니다.")

    if (
        latest_health and
        skip_count < 3 and
        not any(item["title"] == "식사 패턴" for item in pattern_insights)
    ):
        pattern_insights.append({
            "title": "식사 패턴",
            "detail": f"최근 7일 결식은 {skip_count}회로 급증 패턴은 감지되지 않았습니다.",
            "level": "safe"
        })

    if len(recent_conds) >= 3 and not any(item["title"] == "건강 변화 패턴" for item in pattern_insights):
        pattern_insights.append({
            "title": "건강 변화 패턴",
            "detail": "최근 건강 상태의 연속 악화 패턴은 감지되지 않았습니다.",
            "level": "safe"
        })

    # AI 감점 합산
    risk_score = max(0, min(100, risk_score - ai_penalty))
    score_breakdown.extend(anomalies)

    # 4단계 위험도 분류
    risk_level_str, risk_level_code = _risk_level_from_score(risk_score)
    if risk_level_str == 'DANGER':
        is_anomaly = True # 40점 미만 시 자동으로 이상 징후 확정

    ai_summary = _build_ai_summary(
        latest_health=latest_health,
        elapsed_days=elapsed_days,
        recent_7d_count=len(recent_7d),
        risk_score=risk_score,
        trend_desc=trend_desc
    )

    return {
        "score": risk_score,
        "risk_level": risk_level_code,
        "risk_level_db": risk_level_str,
        "score_breakdown": score_breakdown,
        "ai_summary": ai_summary,
        "is_anomaly": is_anomaly,
        "anomaly_types": anomaly_types,
        "time_deviation": time_dev_minutes,
        "predicted_risk_prob": round(100.0 - risk_score, 2),
        "confidence": _confidence_from_data(health_history, login_history, latest_health),
        "evidence": evidence,
        "pattern_insights": pattern_insights
    }


def record_risk_analysis(user, risk_result, db_session, RiskAnalysisModel):
    """계산된 위험 분석 결과를 DB에 저장합니다."""
    new_risk_analysis = RiskAnalysisModel(
        user_id=user.user_id,
        risk_score=risk_result["score"],
        risk_level=risk_result["risk_level_db"],
        is_anomaly=risk_result["is_anomaly"],
        anomaly_type=", ".join(risk_result["anomaly_types"]) if risk_result["anomaly_types"] else "정상",
        time_deviation=risk_result["time_deviation"],
        predicted_risk_prob=risk_result["predicted_risk_prob"],
        ai_summary=risk_result["ai_summary"],
        analyzed_at=datetime.datetime.now()
    )
    db_session.add(new_risk_analysis)
    db_session.commit()
    return new_risk_analysis


def evaluate_and_record_risk(user, health_history, login_history, db_session, RiskAnalysisModel, force=True):
    """
    위험 분석을 계산하고 저장합니다.
    force=False이면 최신 건강/로그인 기록을 이미 반영한 최근 분석 결과를 재사용합니다.
    """
    risk_result = calculate_risk(user, health_history, login_history)
    latest_analysis = RiskAnalysisModel.query.filter_by(user_id=user.user_id)\
        .order_by(RiskAnalysisModel.analyzed_at.desc()).first()

    if not force and _latest_analysis_covers_inputs(latest_analysis, health_history, login_history):
        return _result_from_analysis(
            latest_analysis,
            score_breakdown=risk_result["score_breakdown"],
            confidence=risk_result["confidence"],
            evidence=risk_result["evidence"],
            pattern_insights=risk_result["pattern_insights"]
        )

    # ==========================================================
    # 3단계: RISK_ANALYSIS 테이블에 분석 결과 적재 (Insert)
    # ==========================================================
    new_risk_analysis = record_risk_analysis(user, risk_result, db_session, RiskAnalysisModel)

    return {
        "score": risk_result["score"],
        "risk_level": risk_result["risk_level"],
        "score_breakdown": risk_result["score_breakdown"],
        "ai_summary": risk_result["ai_summary"],
        "analysis_id": new_risk_analysis.analysis_id,
        "confidence": risk_result["confidence"],
        "evidence": risk_result["evidence"],
        "pattern_insights": risk_result["pattern_insights"],
        "reused": False
    }


def _status_counts(records):
    total = len(records or [])
    skipped = sum(
        1 for record in records or []
        if '결식' in [record.breakfast_status, record.lunch_status, record.dinner_status]
    )
    return total, skipped


def _recommended_action_from_risk(risk_result):
    risk_level = risk_result.get("risk_level")
    score = risk_result.get("score", 100)
    evidence_text = " ".join(risk_result.get("evidence", []))

    if risk_level == "danger" or score < 40:
        if any(keyword in evidence_text for keyword in ["결식", "연속 악화", "건강 상태 입력 기록 없음"]):
            return "즉시 방문 확인 검토"
        return "긴급 안부 전화"

    if risk_level == "warn" or score < 60:
        if any(keyword in evidence_text for keyword in ["결식", "연속 악화"]):
            return "방문 검토"
        return "안부 전화"

    if risk_level == "watch" or score < 80:
        return "상태 확인"

    return "정기 관찰"


def analyze_life_pattern_with_gemini(user, health_history, login_history):
    """
    Gemini로 생활 패턴을 자연어 분석합니다.
    이름, 전화번호, 주소 등 직접 식별 정보는 전송하지 않습니다.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise LifePatternAIError("Gemini API 키가 설정되지 않았습니다. 설정을 확인해주세요.", 503)

    risk_result = calculate_risk(user, health_history, login_history)
    latest_health = health_history[0] if health_history else None
    latest_login_at = _latest_time(login_history, "auth_time")
    now = datetime.datetime.now()
    recent_7d = [
        record for record in health_history or []
        if record.recorded_at and record.recorded_at >= now - datetime.timedelta(days=7)
    ]
    recent_count, skip_count = _status_counts(recent_7d)

    latest_health_elapsed = None
    if latest_health and latest_health.recorded_at:
        latest_health_elapsed = int((now - latest_health.recorded_at).total_seconds() // 3600)

    latest_login_elapsed = None
    if latest_login_at:
        latest_login_elapsed = int((now - latest_login_at).total_seconds() // 3600)

    health_levels = [
        record.condition_level for record in recent_7d
        if record.condition_level is not None
    ][:7]
    pattern_lines = [
        f"- 나이대: {int(user.age // 10) * 10}대",
        f"- 기저질환 여부: {'있음' if getattr(user, 'has_underlying_disease', False) else '없음'}",
        f"- 현재 위험점수: {risk_result['score']}점",
        f"- 현재 위험등급: {risk_result['risk_level_db']}",
        f"- 시스템 권장 조치: {_recommended_action_from_risk(risk_result)}",
        f"- 최근 7일 건강 기록 수: {recent_count}건",
        f"- 최근 7일 결식 감지 횟수: {skip_count}회",
        f"- 최근 건강 상태 점수 흐름: {health_levels if health_levels else '데이터 부족'}",
        f"- 마지막 건강 입력 후 경과: {latest_health_elapsed if latest_health_elapsed is not None else '기록 없음'}시간",
        f"- 마지막 앱 접속 후 경과: {latest_login_elapsed if latest_login_elapsed is not None else '기록 없음'}시간",
        "- 시스템 감지 패턴:",
    ]
    pattern_lines.extend(
        f"  · {item['title']}: {item['detail']}"
        for item in risk_result.get("pattern_insights", [])
    )

    prompt = (
        "당신은 사회복지사의 독거 어르신 생활 패턴 모니터링을 보조하는 AI입니다.\n"
        "아래 데이터에는 이름, 전화번호, 주소가 제거되어 있습니다.\n"
        "의학적 진단을 하지 말고, 생활 패턴 이상 징후와 복지사가 확인할 행동만 제안하세요.\n"
        "긴급 방문 여부는 AI가 확정하지 말고, 복지사 최종 확인이 필요하다고 표현하세요.\n"
        "마크다운 표나 볼드 기호는 사용하지 말고, 아래 형식으로 짧게 작성하세요.\n\n"
        "1. 생활 패턴 요약: 2문장 이내\n"
        "2. AI가 주목한 이상 신호: 2~3개\n"
        "3. 복지사 확인 권장 조치: 2개 이내\n"
        "4. 판단 한계: 데이터 부족 또는 확인 필요한 점\n\n"
        "분석 데이터:\n" + "\n".join(pattern_lines)
    )

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = generate_content(
                model=resolve_model(), feature='legacy_life_pattern',
                contents=[prompt]
            )
            if not response.text or not response.text.strip():
                raise LifePatternAIError("AI 분석 결과가 비어 있습니다. 다시 시도해주세요.")
            return response.text
        except LifePatternAIError:
            raise
        except AIError as e:
            raise LifePatternAIError(str(e), e.status) from e
        except Exception as e:
            if "503" in str(e) and attempt < max_retries - 1:
                time.sleep(2)
                continue
            if getattr(e, 'code', None) == 429:
                raise LifePatternAIError("AI 요청 한도를 초과했습니다. 잠시 후 다시 시도해주세요.", 429) from e
            raise LifePatternAIError("AI 생활 패턴 분석에 실패했습니다. 잠시 후 다시 시도해주세요.") from e


def analyze_checkup_document_with_gemini(image_path):
    """Compatibility summary API; extraction failures propagate to the caller."""
    from services.checkup_service import extract_document, summarize
    return summarize(extract_document(image_path))
