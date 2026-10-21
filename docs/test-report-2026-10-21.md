# SmartCare 테스트 보고서

- 수행일: 2026-10-21 (Asia/Seoul)
- 대상 커밋: `366fdc1ff04ab96bb20d313b0ab0e076eda780e0`
- 목적: 현재 프로젝트의 기존 자동화 테스트를 단위·통합·시스템 수준으로 분류하여 실행하고, 결과와 검증 한계를 기록한다.
- 작업 시작 시 Git 작업 트리는 깨끗했다. 애플리케이션 코드와 기존 테스트는 수정하지 않았다.

## 실행 환경과 데이터 격리

| 항목 | 환경 |
| --- | --- |
| 운영체제 | Windows 11, build 26300 |
| Python | 프로젝트 `.venv`, 3.14.7, 64bit |
| 웹·DB 라이브러리 | Flask 3.1.3 / SQLAlchemy 2.0.52 |
| AI SDK | google-genai 2.22.0 |
| 문서 처리 | Pillow 11.3.0 / pypdf 6.1.1 |
| 브라우저 | Playwright 1.63.0 / Chrome 155.0.8059.85, headless |
| 테스트 DB | SQLite 메모리 DB 및 동시성 검사용 별도 파일 DB |
| 테스트 데이터 | 가상 사용자·복지사, 생성된 검진표 PDF·이미지 |

운영 진입점 `app/app.py`는 import 시 DB 테이블 생성과 스키마 변경을 수행하므로 직접 실행하지 않았다. 기존 테스트가 구성하는 Flask 앱에 실제 Blueprint·모델·서비스·템플릿을 연결했다. 운영 MySQL, 실제 사용자 데이터 및 운영 업로드 폴더를 대상으로 테스트하지 않았다. Gemini SDK 응답 또는 분석 함수를 모의하여 실제 AI 추출 정확도·제공자 연결 상태와 구분했다.

## 결과 요약

| 수준 | 실행 | 통과 | 실패 | 오류 | 건너뜀 | 실행 시간 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 단위 | 47 | 47 | 0 | 0 | 0 | 2.082초 |
| 통합 | 75 | 75 | 0 | 0 | 0 | 5.982초 |
| 시스템 | 22 | 21 | 1 | 0 | 0 | 119.903초 |
| 합계 | **144** | **143** | **1** | **0** | **0** | **127.967초** |

전체 통과율은 99.31%이며 **전체 성공 판정은 아니다**. 시간 합계는 각 최종 전체 실행의 테스트 러너 측정값으로, 수집·별도 원인 분석 시간은 제외한다. 실패 1건을 단독 재실행한 결과도 실패했다(1건, 2.630초). 재실행은 위 144건에 중복 합산하지 않았다.

### 실패 분석: 숨겨진 알림 카드의 열 수 검사

- 테스트: `test_status_alerts_browser.StatusAlertBrowserTests.test_restore_detail_explicit_read_mobile_and_new_poll_notice`
- 위치: `tests/test_status_alerts_browser.py:40`
- 기대값 / 실제값: `4` / `3`, `AssertionError: 3 != 4`
- 판정: **테스트의 표시 상태·CSS 문자열 해석 문제로 판단**. 이 관측에서는 실제 표시된 4열 UI의 결함이 확인되지 않았다.

테스트는 관리자 로그인 직후, 아직 숨겨져 있는 최근 알림 탭에서 `getComputedStyle(n).gridTemplateColumns.split(" ").length`를 검사한다. 동일 fixture를 이용한 추가 브라우저 관측 결과는 다음과 같다.

| 상태 | 카드 표시 | 계산된 `gridTemplateColumns` | 관측 |
| --- | --- | --- | --- |
| 알림 탭 이동 전 | 숨김, bounding box 없음 | `repeat(4, minmax(0px, 1fr))` | 공백 분리 결과가 3개지만 3열이라는 뜻이 아님 |
| 최근 알림 탭 이동 후 | 표시됨, 폭 1066px | `244.25px 244.25px 244.25px 244.25px` | 실제 4열 |

관련 CSS는 `app/static/css/status_alerts.css:19`의 `repeat(4,minmax(0,1fr))`이다. 권장 수정 방향은 알림 탭을 명시적으로 열고 카드가 보일 때까지 기다린 후 실제 열 배치를 검사하는 것이다. 다른 숨김 카드 검사도 같은 조건을 검토할 필요가 있다. 이번 작업은 테스트 수행·보고 요청이므로 기존 테스트나 CSS는 변경하지 않았다.

이 테스트는 첫 실패에서 중단되었으므로, 같은 메서드 뒤쪽의 읽음 처리·모바일 상세·신규 알림 폴링 검사는 **해당 실행에서 완료되지 않았다**. 다른 통과 테스트의 유사 범위와 구분해야 한다.

## 테스트 수준별 범위

### 단위 테스트

서비스 함수와 데이터 변환·검증을 중심으로, 외부 AI 응답 및 사용량 저장 경계를 모의했다. 파일 처리 테스트는 실제 PDF·이미지 라이브러리를 사용한다.

| 클래스 | 건수 | 주요 확인 내용 |
| --- | ---: | --- |
| `test_checkup.DocumentTests` | 11 | 지원 문서 형식, 회전, 손상·암호화·크기 제한, 추출 JSON 검증, AI 요청·오류 계약 |
| `test_health_analysis.InputTests` | 5 | 건강·접속·검진 자료 조합, 기간 경계, 누락·상충 자료, 이름·수치 정규화 |
| `test_health_analysis.ProviderTests` | 10 | JSON 스키마, 근거 참조, 개인정보 제외, 응답 개수·토큰 제한, 키·할당량·시간 초과 오류 |
| `test_health_comparisons.ComparisonTests` | 11 | 점수 등급 경계, 입력 변경 감지, 경과 시간, 일별 마지막 기록, 단위·좌우 구분, 이전 분석 비교 |
| `test_health_comparisons.ProviderComparisonTests` | 2 | 비교용 AI 입력의 정보 제한, 잘못된 우선 조치·근거 거부 |
| `test_health_trends.TrendDataTests` | 8 | 실제 0과 결측치 구분, 미래·중복·단위 충돌 자료 제외, 과거 스냅샷 보존 |

### 통합 테스트

Flask 테스트 클라이언트와 실제 SQLAlchemy 모델·SQLite를 연결했다. API 권한, 저장·재조회, 실패 시 데이터 보존, 동시성 및 서비스 간 연결을 검증했다.

| 클래스 | 건수 | 주요 확인 내용 |
| --- | ---: | --- |
| `test_ai_usage.MigrationTests` | 1 | 기존 SQLite 테이블 데이터 보존 및 반복 스키마 적용 |
| `test_ai_usage.UsageTests` | 15 | 모델 선택, 사용량 공유·저장, 할당량 사전 차단, 재시도 횟수, 동시 예약, 날짜·DST 경계 |
| `test_auth.AuthTests` | 11 | 역할별 로그인·페이지 접근, 전환·실패·만료·로그아웃, 가입·대리 등록, AI 실패 응답 |
| `test_checkup.ApiTests` | 21 | 검진표 분석·확정·재조회, 질환 반영, 날짜 우선순위, 소유권·입력 검증, 롤백, 업로드 거부 |
| `test_health_analysis.ApiTests` | 5 | 분석 이력·스냅샷, 장애 시 보존, 담당 권한 변경, 잘못된 선택 |
| `test_health_comparisons.ApiComparisonTests` | 4 | 비교 저장, 재확정 이후 오래된 자료 표시, 점수 계산 실패, 이전 결과 호환 |
| `test_health_trends.TrendApiTests` | 5 | 읽기 전용·기간·담당 권한, 스냅샷 고정, 이전 형식·분석 실패·계산 실패 |
| `test_status_alerts.StatusAlertTests` | 13 | 악화·회복·등급 변경, 저장·재시도, 담당 재배정, 읽음 멱등성, 백그라운드 검사, 동시 중복 방지 |

### 시스템 테스트

로컬 HTTP 서버와 실제 Chrome을 사용해 UI·JavaScript·API·DB가 연결되는 사용자 흐름을 검증했다. 기존 fixture는 대상자 목록 API와 AI 분석 등을 대체하고 일부 응답 지연·실패를 주입한다. 따라서 이 분류는 **격리 환경의 브라우저 시스템 시나리오**이며, 운영 구성을 그대로 사용한 전체 E2E 검증은 아니다.

- 로그인·역할 전환, 등록·대리 등록, 세션 만료 및 중복 제출 방지
- 관리자 탐색, 점수 산정 근거, AI 모델 선택·로딩·이력·복원
- 검진표 수정·확정·재분석, 기존 확정 결과 보존, HTML 삽입 방어
- 건강 비교·추세 그래프, 실제 0·결측치 표시, 키보드 조작, 오래된 응답·갱신 경합
- 알림 복원·상세·읽음, 조회·저장 오류, 자동 갱신 및 스크롤 위치 보존
- 여러 시나리오에서 360·390·768·1280·1440px 화면 폭과 가로 넘침 검사

| 클래스 | 실행 | 통과 | 실패 |
| --- | ---: | ---: | ---: |
| `test_auth_browser.AuthBrowserTests` | 12 | 12 | 0 |
| `test_checkup_browser.BrowserTests` | 2 | 2 | 0 |
| `test_health_trends_browser.HealthTrendBrowserTests` | 4 | 4 | 0 |
| `test_status_alerts_browser.StatusAlertBrowserTests` | 4 | 3 | 1 |

## 실행 방법과 증빙

프로젝트 루트 PowerShell에서 실행한다. 기존 가상환경·Chrome 및 `tests/fixtures/checkups`의 생성된 문서 샘플이 필요하다. 브라우저 의존성이 없으면 기존 테스트는 건너뛰므로 JSON의 개별 `outcome`도 확인해야 한다.

```powershell
.\.venv\Scripts\python.exe tests/run_project_tests.py --level unit --output docs/test-results/2026-10-21/unit.json --log docs/unit-test-2026-10-21.log
.\.venv\Scripts\python.exe tests/run_project_tests.py --level integration --output docs/test-results/2026-10-21/integration.json --log docs/integration-test-2026-10-21.log
.\.venv\Scripts\python.exe tests/run_project_tests.py --level system --output docs/test-results/2026-10-21/system.json --log docs/system-test-2026-10-21.log
# 실패 항목만 재현
.\.venv\Scripts\python.exe tests/run_project_tests.py --level system --case test_status_alerts_browser.StatusAlertBrowserTests.test_restore_detail_explicit_read_mobile_and_new_poll_notice --output docs/test-results/2026-10-21/system-recheck.json --log docs/system-recheck-2026-10-21.log
```

실행기는 `tests/test_*.py`를 발견하고 클래스 단위로 분류한다. 각 테스트는 한 수준에만 포함하며, 하위 `subTest` 반복은 별도 테스트 건수로 합산하지 않는다. 수집 오류가 발생하면 실행을 실패 처리한다. 현재 단위 클래스 목록 외의 비브라우저 테스트는 통합으로 분류하므로 새 테스트 추가 시 분류 검토가 필요하다.

- [단위 테스트 개별 결과](test-results/2026-10-21/unit.json)
- [통합 테스트 개별 결과](test-results/2026-10-21/integration.json)
- [시스템 테스트 개별 결과](test-results/2026-10-21/system.json)
- [실패 항목 단독 재실행](test-results/2026-10-21/system-recheck.json)
- [알림 탭 표시 전후 CSS 관측](test-results/2026-10-21/system-layout-diagnostic.json)
- 로그: `docs/{unit,integration,system}-test-2026-10-21.log` (로컬 생성, 기존 `.gitignore`에 따라 Git 제외)
- 브라우저 캡처: `tests/ui_artifacts/`와 OS 임시 폴더 (기존 테스트가 생성, 이미지 Git 제외)

JSON에는 실행 시각(KST), 환경·패키지 버전, 테스트 식별자, 판정, 소요 시간 및 실패 상세를 보존한다. 로그에 나타나는 의도적 `ERROR`·예외와 HTTP 4xx·5xx는 실패 주입 시나리오가 포함될 수 있으므로 최종 테스트 판정과 함께 해석해야 한다.

## 검증 한계와 추가 확인 사항

1. **운영 MySQL 미검증:** SQLite 통과만으로 MySQL의 ENUM·BIGINT 자동 증가, 행 잠금·트랜잭션, 스키마 변경 권한의 호환성을 보장할 수 없다. 인증 fixture는 SQLite용 로그인 이력 ID를 별도로 부여한다.
2. **운영 기동·배포 미검증:** `app/app.py` 기동, 실제 WSGI·터널, 운영 상태 검사 프로세스 및 장시간 실행은 검증하지 않았다. 단위·통합의 검사 루프 테스트가 운영 프로세스 검증을 대체하지 않는다.
3. **실제 AI 품질·연결 미검증:** Gemini API 키·네트워크·실제 모델 가용성·실제 청구량·OCR 정확도를 확인하지 않았다. `tests/evaluate_checkup.py --live`는 실행하지 않았으며 과거 `evaluation.json`을 이번 결과에 포함하지 않았다.
4. **미포함 기능 흐름:** 기존 테스트는 모든 경로를 포괄하지 않는다. 실제 긴급 요청 생성부터 읽음까지, 조치 기록 저장·조회, 정상 업로드 전체 흐름 등은 추가 시나리오 검토가 필요하다. 관리자 브라우저 테스트의 대체 대상자 목록 응답은 실제 목록 API 전체의 정확성을 입증하지 않는다.
5. **비기능 항목 미검증:** 부하·장시간 안정성·침투 보안·전체 접근성 감사 및 Firefox·Safari·실물 모바일 기기는 범위 밖이다. 이번 시간 측정은 테스트 실행 시간이며 서비스 응답 성능 지표가 아니다.
6. **코드 커버리지 미측정:** 현재 가상환경에 coverage 도구가 없고 이번 실행에서 별도 계측하지 않아 코드 커버리지 비율을 제시하지 않는다. 통과율을 코드 커버리지로 해석하면 안 된다.

## 변경 파일과 검토

- `tests/run_project_tests.py`: 기존 테스트의 수준별 실행, UTF-8 로그 및 개별 JSON 결과 저장 기능 추가
- `docs/test-report-2026-10-21.md`: 수행 범위·환경·결과·재현 방법·검증 한계 보고서 추가
- `docs/test-results/2026-10-21/unit.json`: 단위 테스트 47건 개별 결과
- `docs/test-results/2026-10-21/integration.json`: 통합 테스트 75건 개별 결과
- `docs/test-results/2026-10-21/system.json`: 시스템 테스트 22건 개별 결과
- `docs/test-results/2026-10-21/system-recheck.json`: 실패 항목 단독 재실행 증빙
- `docs/test-results/2026-10-21/system-layout-diagnostic.json`: 숨김·표시 상태의 CSS 및 카드 크기 관측 증빙