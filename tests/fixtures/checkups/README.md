# 건강검진표 인식 샘플 및 검증

모든 이름·생년월일·기관·수치는 **가상 테스트 자료**입니다. 실제 검진표의 복제본이 아니며 참고범위는 테스트용 예시입니다. 현재 샘플은 일반검진표와 유사한 단일 표 형식이므로 병원별 양식 전체의 정확도를 대표하지 않습니다.

## 샘플

이미지·PDF는 Git 추적에서 제외하고 로컬에 보관합니다. 새로 복제한 저장소에서 테스트하기 전 아래 ‘샘플 재생성’ 명령으로 파일을 생성해주세요. 생성 코드와 정답 JSON은 계속 Git으로 관리합니다.

- `normal`: 정상 범위 중심, 좌우 시력·청력 및 혈액/소변검사 포함.
- `abnormal`: 혈압·혈당·지질·간·신장 수치 변화와 부등호/양성 표기 포함.
- `missing`: HDL·LDL·감마GTP 행이 없는 문서. 누락 항목을 만들어내면 안 됨.
- 각 사례의 PDF·PNG·JPG·90도 회전 JPG·흐림 JPG, 총 15개 문서.
- `expected.json`: 문서별 정답. `*_pdf_preview.png`: PDF를 렌더한 검토 이미지.
- `evaluation.json`: 실제 Gemini 평가 기록. 오류는 정확도 0%로 단정하지 않고 요청 실패로 별도 기록.

## 실행

프로젝트 루트에서 실행합니다.

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe tests/evaluate_checkup.py --live --interval 15
```

브라우저 테스트는 선택적 개발 의존성 `playwright`와 기존 Windows Chrome을 사용합니다. 가상 SQLite와 모의 AI 응답만 사용하며 운영 MySQL에 접속하지 않습니다. Chrome을 실행할 수 없는 환경에서는 해당 테스트를 제외하고 `-p test_checkup.py`로 실행할 수 있습니다.

실제 평가는 `.env`의 Gemini API 키와 `GEMINI_CHECKUP_MODEL`을 사용하며 API 비용/할당량을 소비할 수 있습니다. 오류 원인 종류와 HTTP 상태는 기록하지만 키는 기록하지 않습니다. 할당량 초과(429)는 추가 요청을 중단합니다. 일시적 서비스 실패 후에는 다음 명령으로 성공한 문서를 재요청하지 않고 실패 항목만 평가합니다.

```powershell
.venv\Scripts\python.exe tests/evaluate_checkup.py --live --retry-failures --interval 15
```

주요 필드 정확도는 이름·검진일·기관과 검사별 항목명·결과·단위를 정답과 비교합니다. 누락 항목은 불일치로 계산하고 추가 항목은 별도로 보고합니다. 좌우 시력은 서로 다른 항목입니다. 공백·괄호와 좌/왼쪽 표기만 정규화하며 수치나 부등호는 바꾸지 않습니다. 참고범위·원문은 사람이 별도 대조해야 합니다. 깨끗한 문서의 목표는 95% 이상이며 실패 문서가 남아 있으면 전체 목표 달성으로 판단하지 않습니다.

샘플 재생성은 `reportlab`과 맑은 고딕 폰트가 필요합니다.

```powershell
.venv\Scripts\python.exe tests/fixtures/checkups/generate_samples.py
```

## 기능 및 연결

- 사용자 파일 선택/카메라 촬영 → 실제 파일 검증 → 15MB/10페이지 제한 → 이미지 EXIF 보정.
- 관리자 문서의 `검진표 AI` 또는 `결과 확인` → 원본과 항목별 표 → 수정/판독 불가 → 확정 저장.
- 이름 누락/불일치는 이름 수정 또는 담당 대상 확인이 필요합니다. 기타 경고는 원본 대조 확인 후 확정 가능합니다.
- 재분석은 최신 추출 결과만 갱신합니다. 이전 확정 결과는 화면에 유지되며 새 결과를 명시적으로 확정해야 교체됩니다.
- 분석 중 표시·중복 클릭 방지 및 모바일 원본/표 표시를 지원합니다. 실제 휴대폰 촬영과 모바일 PDF 뷰어는 기기별 확인이 필요합니다.

API:

| 요청 | 동작 |
| --- | --- |
| `POST /api/admin/checkup/analyze/<doc_id>` | 추출 결과 저장; 기존 `analysis` 요약과 `extraction`, `validation_issues`, revision 반환 |
| `GET /api/admin/checkup/<doc_id>/result` | 최신 추출/확정 결과, 확정자·시각 조회 |
| `GET /api/admin/checkup/<doc_id>/original` | 담당자의 원본 대조 뷰어 |
| `PUT /api/admin/checkup/<doc_id>/confirm` | `{revision, result, identity_verified, issues_reviewed}`로 수정·확정 |

`result`는 추출 JSON과 같은 구조입니다. 원문/페이지 및 행 수는 수정하지 않으며 판독 불가 행은 `value=null`로 저장합니다. 재분석으로 revision이 바뀐 상태의 확정 요청은 409를 반환합니다. 미담당/미배정 문서의 분석·결과·확정·원본 API 접근은 403입니다.

## 변경 파일과 운영 확인

| 파일 | 주요 변경 |
| --- | --- |
| `app/services/checkup_service.py` | 입력 검증, Gemini 호환 스키마, 추출/검증/오류 처리 |
| `app/routes/checkup.py` | 담당자 권한, 원본/추출/확정 API |
| `app/models/models.py` | `CHECKUP_RESULT` 신규 테이블; AI 원본과 확정 JSON 분리 |
| `app/app.py` | 검진표 Blueprint 등록 |
| `app/routes/user.py` | 업로드 내용·용량 검증 및 보정 |
| `app/routes/social_worker.py` | 기존 판독 API를 새 Blueprint로 이관 |
| `app/services/social_worker_ai_service.py` | 기존 요약 함수가 새 인식 서비스 사용 |
| `app/templates/admin_web.html` | PDF 카드, 결과 확인 화면 연결 |
| `app/templates/user_web.html` | 파일 형식 안내와 촬영 입력 |
| `app/static/js/checkup_review.js`, `app/static/css/checkup_review.css` | 원본 대조, 수정/확정, 로딩, 반응형 팝업 |
| `requirements.txt`, `.env.example` | Pillow/pypdf 및 검진표 모델 설정 |
| `tests/test_checkup.py`, `tests/test_checkup_browser.py` | 서비스/API/브라우저 회귀 테스트 |
| `tests/evaluate_checkup.py`, 이 폴더 | 정답 샘플과 실제 인식률 평가 |

앱의 기존 `db.create_all()` 흐름으로 새 테이블이 생성됩니다. 기존 문서/테이블 컬럼은 변경하지 않습니다. 운영 MySQL은 이번 테스트에서 사용하지 않았으므로 앱 재시작 시 테이블 생성 권한을 확인해야 합니다. 새 결과 API 권한 검사는 기존 생활패턴 API와 달리 미배정 문서 접근도 거절합니다.

기존 `/static/uploads/checkups/` 원본 공개 경로와 기존 문서 삭제 동작은 이번 변경 범위 밖입니다. 원본의 공개 URL 접근을 차단하거나 삭제를 휴지통 방식으로 바꾸는 작업은 별도로 필요합니다. 테스트/샘플 제작 과정에서 기존 프로젝트 자료를 삭제하지 않았습니다.

추천 커밋 메시지: `기능추가: 건강검진표 항목 인식과 결과 확인·저장 기능 추가`
