# AI 모델 선택·앱 사용량 기록

## 사용 방법

사회복지사 화면 상단의 ‘AI 모델·사용량’ 또는 종합 분석/검진표 확인창의 ‘모델 선택·사용량’을 누릅니다. 선택은 해당 브라우저에 저장되고 두 AI 기능에 공통 적용됩니다. 기본은 Gemini 3.6 Flash입니다. 진행 중 요청은 시작 당시 모델을 유지하고 다음 요청부터 변경 모델을 사용합니다. 실패 시 모델을 자동 전환하지 않습니다.

패널은 Gemini 3.8 Flash, 3.6 Flash, 2.5 Flash, 3.5 Flash Lite의 앱 요청 횟수·확인된 입력 토큰·예상 잔여량을 표시합니다. 열 때, 분석 완료/실패 후, 열린 동안 30초마다 갱신하고 수동 갱신도 지원합니다. 조회 오류가 나면 이전 조회값과 시각을 보존하고 갱신 실패를 표시합니다.

## 집계 한계

**앱 호출 기준 예상치이며 AI Studio·다른 프로그램의 사용량은 포함하지 않습니다.** 사진의 기존 사용 횟수는 가져오지 않고 적용 이후 호출부터 기록합니다. 한도는 사용자 제공 사진을 참고한 설정값이며 Google에서 조회한 공식 한도가 아닙니다. 실제 프로젝트 사용량은 패널의 [AI Studio 링크](https://aistudio.google.com/rate-limit)로 확인합니다.

- RPM과 입력 TPM은 최근 60초, RPD는 미국 태평양 시간 자정부터 집계합니다. UTC로 저장하고 서머타임을 반영합니다.
- 실패·대기·재시도도 호출 시도 횟수에 포함하므로 보수적인 예상치입니다. 실제 Google 할당량 과금/집계 방식과 일치한다고 보장하지 않습니다.
- 토큰은 응답 `usage_metadata.prompt_token_count`를 사용합니다. 정보가 없는 호출이 최근 60초에 있으면 잔여 TPM은 null/‘확인 불가’입니다. 이때 토큰 사용량 막대도 표시하지 않습니다.
- 로컬 예상 한도에 도달해도 호출을 차단하지 않습니다. Gemini의 실제 권한·할당량 응답을 따릅니다. 모델 접근 불가나 429는 다른 모델로 자동 전환하지 않습니다.
- 기록에는 집계 범위·모델·기능·호출 시각·pending/response/error 상태·입력 토큰·숫자 오류 코드만 저장합니다. 건강 자료·프롬프트·사용자 식별자·키·오류 원문은 저장하지 않습니다.
- 분석 JSON 검증이나 결과 저장이 실패해도 이미 받은 응답의 사용량은 보존됩니다. 호출 전 기록 실패는 외부 요청을 막습니다. 응답 기록이 실패하면 이전 결과를 유지하며 해당 호출은 pending/토큰 미확인으로 남습니다.

## 설정과 DB 적용

`requirements.txt`에 Windows 시간대 데이터 `tzdata==2026.2`를 추가했습니다. 현재 프로젝트 가상환경에 설치했습니다.

- `GEMINI_DEFAULT_MODEL`: 기본 선택 모델. 기본 `gemini-3.6-flash`.
- `GEMINI_CHECKUP_MODEL`: 모델 필드 없는 기존 검진 API/서비스 호출의 기본값을 유지합니다.
- `GEMINI_USAGE_SCOPE`: 동일 Google 프로젝트의 앱 호출을 합산할 구분자. 기본 `smartcare-project`. 같은 프로젝트를 쓰는 서버는 동일 값·공통 DB를 사용해야 합니다. Google 프로젝트를 바꾸면 이 값을 변경합니다.
- `GEMINI_MODEL_LIMITS`: 네 모델의 양의 정수 한도를 부분 재정의하는 JSON. 기본 `{}`. 예: `{"gemini-3.6-flash":{"rpm":10,"tpm":300000,"rpd":100}}`.

서버 재시작 시 `AI_USAGE` 테이블을 생성하고 기존 `CHECKUP_RESULT`에 `extraction_model`, `confirmed_model` nullable 컬럼이 없으면 추가합니다. 기존 확정값은 유지하며 모델 미기록 값은 null입니다. 재분석은 판독 모델만 바꾸고, 다시 확정할 때 확정 모델을 복사합니다. 운영 MySQL에 CREATE/ALTER 권한과 새 컬럼을 확인해야 합니다. 기존 USER/인증 구조는 변경하지 않습니다.

호출 기록은 분석 결과와 별도 DB 세션/트랜잭션으로 저장합니다. 공통 외부 호출 서비스는 Flask 앱·DB 컨텍스트가 필요합니다. SDK 내부 재시도는 1회 시도로 설정하고, 검진의 500/502/503/504 오류만 최대 3회까지 각각 기록해 재시도합니다. 429는 재시도하지 않습니다.

## API

- `GET /api/admin/ai/models`: 유효한 사회복지사 세션에 네 모델의 id/label/limits, default_model을 제공합니다.
- `GET /api/admin/ai/usage`: scope, basis=app_estimate, as_of, tracking_started_at, next_daily_reset_at, 모델별 used/limits/remaining/exceeded/unknown_token_calls를 제공합니다. 두 조회는 Gemini를 호출하지 않습니다.
- 기존 종합 분석과 검진 분석 POST에 선택적 JSON `model`을 추가합니다. 필드 생략은 기존 기본값을 사용하고, null이나 허용 목록 밖의 값은 외부 호출 전 400으로 거절합니다.
- 종합 분석 이력은 실제 모델을 기존 model 컬럼에 저장합니다. 검진 결과 조회·분석·확정 응답에 extraction_model/confirmed_model을 추가합니다.

## 변경 파일

| 파일 | 주요 변경 |
| --- | --- |
| `app/services/ai_service.py` | 모델 허용 목록·설정 한도·공통 호출·독립 사용량 기록·시간대 집계·컬럼 호환 |
| `app/routes/ai.py` | 인증된 모델 목록·사용량 조회 API |
| `app/models/models.py` | AI_USAGE와 검진 판독/확정 모델 컬럼 |
| `app/app.py` | Blueprint 등록·기존 검진 테이블 컬럼 추가 |
| `app/routes/social_worker.py`, `app/routes/checkup.py` | 선택 모델 검증·서비스 전달·실제 모델 저장 |
| `app/services/health_analysis_service.py`, `app/services/checkup_service.py`, `app/services/social_worker_ai_service.py` | 공통 호출 서비스 사용·모델 오류 안내 |
| `app/static/js/ai_models.js`, `app/static/css/ai_models.css` | 선택 패널·브라우저 기억·사용량 갱신·모바일 표시 |
| `app/static/js/life_pattern_feedback.js`, `app/static/js/checkup_review.js` | 공통 선택 연결·요청 모델 고정·실제 모델 표시 |
| `app/templates/admin_web.html` | 공통 패널 자산 연결 |
| `.env.example`, `requirements.txt` | 모델·집계 설정 예제·시간대 의존성 |
| `tests/test_ai_usage.py` | 네 모델·집계·오류·권한·시간 경계·기존 DB 호환 테스트 |
| `tests/test_auth.py`, `tests/test_auth_browser.py`, `tests/test_checkup.py`, `tests/test_checkup_browser.py`, `tests/test_health_analysis.py` | 기존 회귀 테스트 연결·선택 및 패널 브라우저 검사 |
| `tests/ui_artifacts/`, `tests/fixtures/checkups/review_mobile_preview.png`, 이 문서 | 테스트가 갱신한 가상 화면 캡처와 구현 기록 |

## 검증과 추가 확인

전체 자동 테스트 62개가 통과했습니다. 새 사용량/모델 API·서비스·DB 호환 검사 13개, 인증 API 10개, 실제 Chrome 인증·분석·모델 선택 브라우저 5개, 검진 서비스/API 20개와 검진 브라우저 1개, 종합 분석 서비스/API 13개를 포함합니다. Python 구문 검사, JavaScript 문법 검사 12개, `git diff --check`도 통과했습니다.

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

테스트는 격리 SQLite와 모의 Gemini 응답을 사용합니다. 실제 Windows Chrome에서 360/390/768/1280/1440px 화면과 선택·복원·분석 중 모델 변경·토큰 미확인·조회 오류·검진 확인창 연결을 검사합니다. 운영 DB와 실제 Gemini 호출은 수행하지 않았습니다.

실제 계정의 네 모델 접근 가능 여부, 각 모델의 구조화 응답·검진 품질과 Google 프로젝트의 실제 한도를 추가 확인해야 합니다. 실제 Google 한도가 바뀌면 서버 설정도 갱신해야 합니다. 실제 휴대폰 화면과 운영 MySQL 컬럼 추가는 별도 확인 대상입니다.

추천 커밋 메시지: `기능추가: AI 모델 선택과 앱 기준 사용량 확인 기능 추가`
