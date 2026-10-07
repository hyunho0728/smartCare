"""공통 모델 허용 목록과 개인정보 없는 외부 호출 집계."""
import datetime as dt
import json
import os
import time
from zoneinfo import ZoneInfo
from google import genai
from google.genai import types
from sqlalchemy import select, func, inspect, text
from sqlalchemy.orm import Session
from models.models import db, AIUsage

DEFAULT_MODEL = 'gemini-3.6-flash'
MODELS = {
    'gemini-3.8-flash': 'Gemini 3.8 Flash',
    'gemini-3.6-flash': 'Gemini 3.6 Flash',
    'gemini-2.5-flash': 'Gemini 2.5 Flash',
    'gemini-3.5-flash-lite': 'Gemini 3.5 Flash Lite',
}


class AIError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


def resolve_model(value=None, default=None):
    selected = (default or os.getenv('GEMINI_DEFAULT_MODEL', DEFAULT_MODEL)) if value is None else value
    if not isinstance(selected, str) or selected not in MODELS:
        raise AIError('지원하지 않는 AI 모델입니다. 네 가지 모델 중 선택해주세요.', 400)
    return selected


def scope():
    value = os.getenv('GEMINI_USAGE_SCOPE', 'smartcare-project')
    if not value.strip() or len(value) > 100:
        raise AIError('사용량 집계 범위 설정을 확인해주세요.')
    return value


def model_catalog():
    try:
        overrides = json.loads(os.getenv('GEMINI_MODEL_LIMITS', '{}'))
        if not isinstance(overrides, dict) or set(overrides) - set(MODELS):
            raise ValueError()
        result = []
        for model, label in MODELS.items():
            limits = {'rpm': 15 if model.endswith('lite') else 5, 'tpm': 250000, 'rpd': 500 if model.endswith('lite') else 20}
            settings = overrides.get(model, {})
            if not isinstance(settings, dict) or set(settings) - set(limits):
                raise ValueError()
            limits.update(settings)
            if any(type(n) is not int or n <= 0 for n in limits.values()):
                raise ValueError()
            result.append({'id': model, 'label': label, 'limits': limits})
        return result
    except (ValueError, TypeError):
        raise AIError('AI 사용량 한도 설정을 확인해주세요.')


def record_attempt(model, feature):
    try:
        with Session(db.engine) as ledger:
            row = AIUsage(scope=scope(), model=model, feature=feature, started_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))
            ledger.add(row); ledger.commit()
            return row.usage_id
    except Exception as error:
        raise AIError('사용량 기록을 저장하지 못해 AI 호출을 시작하지 않았습니다. 다시 시도해주세요.') from error


def finish_attempt(usage_id, response=None, error=None):
    try:
        with Session(db.engine) as ledger:
            row = ledger.get(AIUsage, usage_id)
            if not row:
                raise ValueError('missing usage')
            row.status = 'error' if error is not None else 'response'
            code = getattr(error, 'code', None)
            row.error_code = code if type(code) is int else None
            metadata = getattr(response, 'usage_metadata', None)
            tokens = getattr(metadata, 'prompt_token_count', None)
            row.input_tokens = tokens if type(tokens) is int and tokens >= 0 else None
            ledger.commit()
    except Exception as failure:
        raise AIError('AI 응답의 사용량 기록을 완료하지 못했습니다. 이전 분석 결과는 유지됩니다.') from failure


def generate_content(*, model, feature, contents, config=None, timeout=60000, attempts=1):
    model = resolve_model(model)
    key = os.getenv('GEMINI_API_KEY')
    if not key:
        raise AIError('Gemini API 키가 설정되지 않았습니다. 설정을 확인해주세요.')
    client = genai.Client(api_key=key, http_options=types.HttpOptions(
        timeout=timeout, retry_options=types.HttpRetryOptions(attempts=1)))
    try:
        for attempt in range(attempts):
            usage_id = record_attempt(model, feature)
            try:
                response = client.models.generate_content(model=model, contents=contents, config=config)
            except Exception as error:
                finish_attempt(usage_id, error=error)
                if getattr(error, 'code', None) in (500, 502, 503, 504) and attempt < attempts - 1:
                    time.sleep(min(2 * (attempt + 1), 4))
                    continue
                raise
            finish_attempt(usage_id, response=response)
            return response
    finally:
        client.close()


def usage_summary(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    now = now.astimezone(dt.timezone.utc)
    pacific = ZoneInfo('America/Los_Angeles')
    local = now.astimezone(pacific)
    day_start = dt.datetime.combine(local.date(), dt.time(), pacific).astimezone(dt.timezone.utc)
    next_reset = dt.datetime.combine(local.date() + dt.timedelta(days=1), dt.time(), pacific).astimezone(dt.timezone.utc)
    minute = now - dt.timedelta(seconds=60)
    utc = lambda value: value.replace(tzinfo=None)
    usage_scope = scope()
    with Session(db.engine) as ledger:
        first = ledger.scalar(select(func.min(AIUsage.started_at)).where(AIUsage.scope == usage_scope))
        rows = ledger.scalars(select(AIUsage).where(AIUsage.scope == usage_scope,
            AIUsage.started_at >= utc(min(day_start, minute)), AIUsage.started_at <= utc(now))).all()
        models = []
        for config in model_catalog():
            model_rows = [r for r in rows if r.model == config['id']]
            recent = [r for r in model_rows if r.started_at > utc(minute)]
            daily = [r for r in model_rows if r.started_at >= utc(day_start)]
            unknown = sum(r.input_tokens is None for r in recent)
            used = {'rpm': len(recent), 'tpm': sum(r.input_tokens or 0 for r in recent), 'rpd': len(daily)}
            limits = config['limits']
            models.append({**config, 'used': used, 'unknown_token_calls': unknown,
                           'remaining': {k: None if k == 'tpm' and unknown else max(0, limits[k] - used[k]) for k in limits},
                           'exceeded': {k: used[k] >= limits[k] for k in limits}})
    return {'scope': usage_scope, 'models': models, 'as_of': now.isoformat(),
            'tracking_started_at': first.replace(tzinfo=dt.timezone.utc).isoformat() if first else None,
            'next_daily_reset_at': next_reset.isoformat(), 'basis': 'app_estimate'}


def ensure_checkup_model_columns(engine):
    """기존 MySQL/SQLite 검진 테이블에 누락된 nullable 컬럼만 추가한다."""
    columns = {c['name'] for c in inspect(engine).get_columns('CHECKUP_RESULT')}
    with engine.begin() as connection:
        for name in ('extraction_model', 'confirmed_model'):
            if name not in columns:
                connection.execute(text(f'ALTER TABLE CHECKUP_RESULT ADD COLUMN {name} VARCHAR(100) NULL'))
