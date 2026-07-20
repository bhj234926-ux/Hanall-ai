# HANALL AI Catalog Extractor

기존 HANALL AI 공간 시뮬레이터와 다중 PDF 카탈로그 추출기를 하나로 통합한 웹앱입니다. 관리자가 벽지·바닥재·타일 PDF를 분석하고 `앱에 적용`을 누르면 고객용 사진 시뮬레이터의 실제 품번 목록이 즉시 갱신됩니다.

## 화면 구성

- `/` - 고객용 공간 사진·실제 자재 적용 시뮬레이터
- `/admin/catalogs` - 관리자용 PDF 업로드·추출·검수 화면
- `/api/catalog/products` - 현재 고객 앱에 적용된 자재 라이브러리 API

## 현재 구현된 기능

- PDF 최대 20개 동시 업로드
- 파일별 브랜드·컬렉션 직접 입력 또는 자동 추정
- PDF 텍스트 좌표에서 자재번호 자동 인식
- 내장 이미지, 벡터 셀, 품번 그리드 배치를 조합한 샘플 영역 탐지
- 페이지별 실제 샘플 이미지 크롭
- `브랜드 + 정규화 품번` 기준 자동 중복 병합
- 중복 제품의 원본 PDF·페이지 이력 보존
- 추출 신뢰도 표시와 검수 필요 항목 분류
- 품번·브랜드·카탈로그·컬렉션 수정
- 원본 페이지 위에서 샘플 크롭 영역 이동·크기 조절
- 잘못 추출된 제품 제외
- 통합 `textures/`, `products.json`, `products.csv`, `manifest.json` ZIP 내보내기
- 작업·제품 메타데이터 SQLite 저장
- 모바일·PC 대응 웹 UI
- 검수 완료 작업을 고객용 시뮬레이터에 바로 활성화
- 기존 고정 샘플 배열 없이 실제 추출 텍스처 API 사용

## Windows에서 실행

Python 3.10 이상이 설치된 PC에서 `run_windows.cmd`를 더블클릭합니다. 처음 한 번만 필요한 패키지를 설치한 뒤 브라우저가 열립니다.

Python 3.14에서도 별도 C++·Rust 컴파일러 없이 실행되도록 이미지 분석은 Pillow만 사용하고, Python 3.14 바이너리를 제공하는 FastAPI·Pydantic 버전을 고정했습니다.

직접 실행하려면:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다.

## macOS / Linux에서 실행

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Docker 실행

```bash
docker build -t hanall-catalog-extractor .
docker run --rm -p 8000:8000 -v hanall-data:/app/data hanall-catalog-extractor
```

## 자동 검증

개발용 의존성을 설치한 뒤 합성 카탈로그 2권의 추출·중복 병합·ZIP 생성을 한 번에 검증할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m tests.smoke_test
.\.venv\Scripts\python.exe -m tests.api_test
```

UI 자동 검증은 Playwright가 설치된 개발 환경에서 `node tests/ui_check.cjs`로 실행합니다.

## 사용 흐름

1. 카탈로그 PDF 여러 권을 선택합니다.
2. 필요한 경우 파일별 브랜드·컬렉션을 입력합니다.
3. `추출 시작`을 누르고 페이지 분석이 끝날 때까지 기다립니다.
4. `검수 필요` 필터에서 신뢰도가 낮은 항목을 먼저 확인합니다.
5. 제품 카드의 수정 버튼에서 품번·메타데이터와 샘플 영역을 고칩니다.
6. 불필요한 항목은 `최종 DB에서 제외`로 표시합니다.
7. `앱에 적용`을 눌러 고객용 시뮬레이터의 현재 자재함으로 지정합니다.
8. `ZIP 내보내기`를 눌러 별도 통합 자재 DB도 받을 수 있습니다.

## ZIP 구조

```text
hanall-catalog-xxxxxxxx.zip
├── manifest.json
├── products.json
├── products.csv
└── textures/
    ├── 개나리벽지__57231-1__xxxxxxx.jpg
    └── ...
```

`products.json`에는 제품별 품번, 브랜드, 컬렉션, 원본 PDF, 페이지, 대표 색상, 추출 신뢰도, 텍스처 경로, 모든 중복 출처 이력이 들어 있습니다. `products.csv`는 Excel에서 한글이 깨지지 않도록 UTF-8 BOM으로 생성합니다.

## 배포

이 프로젝트는 정적 Vercel 사이트가 아니라 PDF를 처리하는 Python 서버가 필요합니다. GitHub 저장소를 Render에 연결하고 저장소 루트의 `render.yaml`을 사용하면 웹 UI와 API가 함께 배포됩니다. 실제 카탈로그와 결과를 재배포 후에도 유지하려면 영구 디스크를 사용해야 합니다.

운영 환경 권장값:

- 메모리 2GB 이상
- 영구 저장공간 10GB 이상
- 단일 작업자(`--workers 1`)로 시작
- 대용량/동시 작업이 늘면 추출 작업을 별도 큐 워커로 분리

## 자동 추출의 범위

카탈로그 디자인이 제조사마다 달라 모든 항목을 무검수로 완벽하게 자르는 것은 현실적으로 어렵습니다. 이 프로젝트는 자동 추출률을 높이면서, 실패 항목을 빠르게 바로잡는 검수 화면을 함께 제공하는 방식입니다. PDF가 스캔 이미지로만 구성돼 텍스트 레이어가 없는 경우에는 별도의 OCR 엔진 연결이 필요합니다.
