# 22B Strategy Engine

암호화폐 자동 트레이딩 봇 시스템입니다.
Binance Futures(선물) 거래소에 연결해 시장 상황을 분석하고, 전략에 따라 자동으로 매매 신호를 생성합니다.
실제 주문 실행 전 반드시 페이퍼 트레이딩(가상 매매)으로 충분히 검증 후 사용하세요.

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [폴더 구조](#2-폴더-구조)
3. [설치 방법](#3-설치-방법)
4. [환경 설정 (.env)](#4-환경-설정-env)
5. [실행 방법](#5-실행-방법)
6. [대시보드 사용법](#6-대시보드-사용법)
7. [텔레그램 명령어](#7-텔레그램-명령어)
8. [시스템 동작 방식](#8-시스템-동작-방식)
9. [안전 장치](#9-안전-장치)
10. [자주 묻는 질문](#10-자주-묻는-질문)

---

## 1. 시스템 개요

### 이 봇이 하는 일

```
바이낸스 → 시세 수집 → 시장 분석 → 전략 실행 → 신호 생성 → (실전 시) 주문
              ↓
        대시보드 표시 + 텔레그램 알림
              ↓
        AI(OpenClaw) 시장 해석 + 리뷰
```

### v1.3 핵심 변경 — Opportunity-Driven 아키텍처

기존 단순 신호 방식에서 **기회 중심 파이프라인**으로 전면 재설계되었습니다.

```
전략 신호(Signal)
    → OpportunityNormalizer  (기회 정규화)
    → ScoringEngine          (11개 규칙, 최대 20점)
    → OpportunityQueue       (TTL 1h, 순위 관리)
    → Top-N 필터             (점수 ≥ 8점만 실행)
    → PaperRecorder / Executor
```

### 운영 모드 4가지

| 모드 | 설명 |
|------|------|
| `OBSERVE` | 관찰만 함. 주문 없음. 처음 시작할 때 이 모드 사용 |
| `LIMITED` | 페이퍼 트레이딩만 허용. 실제 돈 거래 없음 |
| `ACTIVE` | 실제 주문 실행. **신중하게 사용** |
| `BLOCKED` | 모든 기능 차단. 긴급 상황용 |

> **.env 파일의 `SYSTEM_MODE=OBSERVE`** 로 시작하는 것을 권장합니다.

---

## 2. 폴더 구조

```
blockchain-tranding/
│
├── bot/                        ← 봇 핵심 코드
│   ├── main.py                 ← 봇 시작점 (여기서 모든 것이 실행됨)
│   ├── config.py               ← 설정값 관리
│   │
│   ├── data/                   ← 시세 수집
│   │   ├── collector.py        ← 바이낸스에서 캔들/시세 가져오기
│   │   └── store.py            ← 메모리 캐시 + DB 저장
│   │
│   ├── regime/                 ← 시장 국면 판단
│   │   ├── detector.py         ← BTC 기준으로 상승/하락/횡보 등 판단
│   │   └── fast_layer.py       ← 단기 보조 레짐 신호 (5종)
│   │
│   ├── strategies/             ← 매매 전략들 (v1.3)
│   │   ├── overreaction_reversal.py         ← 오버리액션 되돌림 전략 (Reversal)
│   │   ├── volatility_expansion_breakout.py ← 볼린저 스퀴즈 돌파 전략 (Breakout)
│   │   ├── early_trend_capture.py           ← 초기 추세 포착 전략 (Trend)
│   │   ├── params_store.py     ← ★ 런타임 파라미터 저장소 (재시작 없이 수정 가능)
│   │   ├── opportunity.py      ← Opportunity 데이터 모델 + 정규화
│   │   ├── scoring.py          ← 11개 규칙 스코어링 엔진
│   │   ├── opportunity_queue.py ← 순위 큐 (TTL 1h)
│   │   ├── strategy_health.py  ← 전략 건강도 엔진 (15분 주기, auto-pause)
│   │   ├── approval_manager.py ← Level 1/2/3 승인 체계
│   │   ├── manager.py          ← 전략 파이프라인 오케스트레이터
│   │   ├── paper_recorder.py   ← 가상매매 기록
│   │   └── signal_bus.py       ← 매매 신호 전달
│   │
│   ├── market/
│   │   └── symbol_universe.py  ← 심볼 Tier 계층화 (Tier 1/2/3)
│   │
│   ├── execution/              ← 주문 실행
│   │   ├── executor.py         ← 바이낸스 실제 주문 실행
│   │   ├── risk_manager.py     ← 위험 관리 (손실 제한 등)
│   │   ├── kill_switch.py      ← 긴급 정지 (Soft/Hard 분리)
│   │   ├── portfolio_constraints.py ← MAX_POSITIONS=2, SAME_DIR=1
│   │   ├── reconciler.py       ← 5분마다 거래소↔DB 데이터 대조
│   │   └── state_machine.py    ← 주문 상태 추적
│   │
│   ├── ai/                     ← AI 분석 (OpenClaw 연결)
│   │   ├── claude_client.py    ← OpenClaw API 연결
│   │   ├── regime_interpreter.py ← 시장 국면 해석
│   │   ├── daily_reviewer.py   ← 매일 22:00 UTC 일일 리뷰
│   │   └── weekly_reviewer.py  ← 매주 일요일 00:00 UTC 주간 리뷰
│   │
│   └── notifications/
│       └── telegram.py         ← 텔레그램 알림 발송
│
├── dashboard/                  ← 웹 대시보드
│   ├── app.py                  ← FastAPI 웹 서버
│   ├── templates/index.html    ← 화면 레이아웃
│   └── static/                 ← CSS/JS 파일
│
├── db/
│   └── schema.py               ← SQLite 데이터베이스 구조
│
├── data/                       ← 실행 중 생성되는 데이터 (자동 생성)
│   ├── trading.db              ← 거래 기록 데이터베이스
│   └── strategy_params.json    ← ★ 런타임 전략 파라미터 (재시작 없이 수정 가능)
│
├── .env                        ← ⚠️ API 키 등 비밀 설정 (절대 공유 금지)
├── requirements.txt            ← 필요한 파이썬 패키지 목록
├── start.bat                   ← 윈도우에서 봇 실행 스크립트
└── install_startup.bat         ← 윈도우 시작 시 자동실행 등록
```

---

## 3. 설치 방법

### 필요 환경
- Python 3.12 이상
- Windows 10/11 (다른 OS도 가능하지만 `.bat` 파일은 윈도우 전용)

### 패키지 설치

```bash
pip install -r requirements.txt
```

### 설치되는 주요 패키지

| 패키지 | 용도 |
|--------|------|
| `httpx` | 바이낸스 API HTTP 통신 |
| `websockets` | 실시간 시세 수신 (WebSocket) |
| `fastapi` + `uvicorn` | 웹 대시보드 서버 |
| `openai` | OpenClaw AI 연결 (OpenAI 호환 방식 사용) |
| `python-dotenv` | `.env` 파일 읽기 |
| `pandas` + `numpy` | 기술적 지표 계산 |

---

## 4. 환경 설정 (.env)

프로젝트 루트에 `.env` 파일을 만들고 아래 내용을 채워주세요.
`.env` 파일은 **절대 다른 사람에게 공유하거나 GitHub에 올리면 안 됩니다.**

```env
# ============================================================
# 바이낸스 API
# ============================================================

# 테스트넷 (연습용 — 실제 돈 아님)
BINANCE_API_KEY=여기에_테스트넷_API키_입력
BINANCE_API_SECRET=여기에_테스트넷_시크릿_입력
BINANCE_TESTNET=true          # true=테스트넷, false=실전

# 실전 API (실거래 준비가 됐을 때만 입력)
BINANCE_MAINNET_API_KEY=여기에_실전_API키_입력
BINANCE_MAINNET_API_SECRET=여기에_실전_시크릿_입력

# ============================================================
# 텔레그램 알림
# ============================================================
TELEGRAM_BOT_TOKEN=텔레그램_봇_토큰
TELEGRAM_CHAT_ID=내_채팅_ID

# ============================================================
# 웹 대시보드
# ============================================================
DASHBOARD_HOST=0.0.0.0        # 모든 IP에서 접근 허용
DASHBOARD_PORT=8000           # 접속 포트
DASHBOARD_SECRET_KEY=랜덤문자열로_변경하세요

# ============================================================
# 데이터베이스
# ============================================================
DB_PATH=./data/trading.db     # 거래 기록 저장 위치

# ============================================================
# 시스템 설정
# ============================================================
SYSTEM_MODE=OBSERVE           # ACTIVE | LIMITED | OBSERVE | BLOCKED
LOG_LEVEL=INFO                # DEBUG | INFO | WARNING | ERROR

# ============================================================
# 추적할 코인 목록
# ============================================================
TRACKED_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT

# ============================================================
# 데이터 수집 설정
# ============================================================
CANDLE_INTERVALS=1h,4h        # 캔들 시간 단위 (1시간봉, 4시간봉)
TICKER_UPDATE_INTERVAL_SEC=5  # 시세 갱신 주기 (초)
CANDLE_LIMIT=200              # 시작 시 불러올 캔들 개수

# ============================================================
# OpenClaw AI (이 컴퓨터에서 실행 중인 AI 에이전트)
# ============================================================
OPENCLAW_BASE_URL=http://127.0.0.1:18789
OPENCLAW_TOKEN=여기에_토큰_입력
OPENCLAW_AGENT_ID=main
AI_ENABLED=true               # false로 하면 AI 기능 끔

# ============================================================
# 리뷰 스케줄
# ============================================================
WEEKLY_REVIEW_DAY=6           # 0=월요일, 6=일요일
WEEKLY_REVIEW_HOUR=0          # UTC 기준 0시 = 한국 시간 오전 9시
DAILY_REVIEW_HOUR=22          # UTC 기준 22시 = 한국 시간 오전 7시
```

### 바이낸스 테스트넷 API 키 발급 방법
1. https://testnet.binancefuture.com 접속
2. 회원가입 후 로그인
3. 상단 메뉴 → API Management → API 키 생성
4. 생성된 키를 `.env`의 `BINANCE_API_KEY`, `BINANCE_API_SECRET`에 입력

---

## 5. 실행 방법

### 방법 1: 직접 실행 (터미널)

```bash
cd D:\workspace\blockchain-tranding
python -m bot.main
```

### 방법 2: start.bat 더블클릭 (윈도우)

`start.bat` 파일을 더블클릭하면 바로 실행됩니다.

### 방법 3: 윈도우 시작 시 자동실행 등록

```bash
install_startup.bat
```

이 파일을 **관리자 권한으로 실행**하면 컴퓨터를 켤 때마다 봇이 자동으로 시작됩니다.

### 실행 확인

봇이 정상 시작되면:
1. 텔레그램으로 `22B Strategy Engine Started` 알림이 옵니다
2. 웹 브라우저에서 `http://localhost:8000` 접속 시 대시보드가 보입니다

---

## 6. 대시보드 사용법

웹 브라우저에서 `http://localhost:8000` 접속

### 화면 구성

```
┌─────────────────────────────────────────────────────┐
│ 헤더: 모드 / 거래소 상태 / 잔고 / 오늘 손익 / 레짐 / 긴급정지 버튼
├─────────────────────────────────────────────────────┤
│ 🛡️ System Status    ← 봇 정상 작동 여부 한눈에 확인   │
│   Bot: RUNNING  Binance WS: Connected  AI: Connected │
│   Telegram: Active  Mode: OBSERVE  Network: TESTNET  │
│                                      [🔄 Restart]   │
├─────────────────────────────────────────────────────┤
│ 🌐 Market Regime    ← 현재 시장 국면 (BTC 기준)       │
├─────────────────────────────────────────────────────┤
│ 📊 Panel 1 — 실시간 지표                             │
│   BTC/ETH/SOL/BNB 별 가격, RSI, EMA, ATR, 자금조달률  │
├─────────────────────────────────────────────────────┤
│ ⚡ Panel 2 — 전략 신호                               │
│   언제, 어떤 코인, 어떤 신호가 발생했는지 기록          │
├─────────────────────────────────────────────────────┤
│ 📂 Panel 3 — 포지션 현황                             │
│   현재 열려있는 LIVE / PAPER 포지션 목록               │
├─────────────────────────────────────────────────────┤
│ 🧠 Panel 4 — 전략 성과                               │
│   각 전략의 승률, 거래횟수, 최대 손실 등               │
├─────────────────────────────────────────────────────┤
│ 📋 Panel 5 — 거래 기록                               │
│   완료된 거래 목록 (필터: 전략별, 기간별, 실전/페이퍼)  │
├─────────────────────────────────────────────────────┤
│ 🤖 Panel 6 — AI 분석                                 │
│   OpenClaw AI의 시장 해석 + 전략 추천                  │
├─────────────────────────────────────────────────────┤
│ ⚙️ Settings                                          │
│   매매 모드 / Kill Switch / 심볼 관리                  │
│   ★ 글로벌 파라미터 편집 (TP/SL 기본값, 최소 점수 등)  │
│   ★ 전략별 파라미터 편집 (재시작 없이 즉시 반영)        │
└─────────────────────────────────────────────────────┘
```

### Settings 패널 — 전략 파라미터 편집 (v1.3 신규)

봇을 재시작하지 않고도 대시보드에서 전략 파라미터를 실시간으로 수정할 수 있습니다.

**글로벌 파라미터**

| 항목 | 기본값 | 설명 |
|------|--------|------|
| 기본 TP (%) | 2.0% | 전략별 TP 미설정 시 사용하는 기본 익절 비율 |
| 기본 SL (%) | 1.0% | 전략별 SL 미설정 시 사용하는 기본 손절 비율 |
| 최소 실행 점수 | 8 | 이 점수 이상인 기회만 실제 포지션 생성 |
| 최대 동시 포지션 | 2 | 동시에 열 수 있는 최대 포지션 수 |

**전략별 파라미터** (탭으로 전략 선택)

| 전략 | 수정 가능한 항목 |
|------|-----------------|
| Overreaction Reversal | TP/SL%, RSI 과매수/과매도 임계값, 급등락 임계(%), lookback 봉, EMA 장기 |
| Volatility Breakout | TP Ratio, SL Offset, 거래량 배수, Squeeze/Expand 비율, BB 기간 |
| Early Trend | TP/SL%, RSI 상한/하한, 거래량 배수, EMA 단기/장기, ATR 기간/배수 |

수정한 파라미터는 `data/strategy_params.json`에 저장되며, 다음 전략 실행 사이클(60초)부터 즉시 반영됩니다.
`기본값 복원` 버튼으로 언제든 초기값으로 되돌릴 수 있습니다.

### 긴급 정지 (Kill Switch)

헤더 우측의 `⚠️ KILL SWITCH` 버튼을 클릭하면:
- 모든 신규 진입이 즉시 차단됩니다
- 기존 포지션은 SL/TP 주문으로 보호됩니다
- 해제하려면 `Reset` 버튼을 클릭합니다

### Restart 버튼

`System Status` 패널의 `🔄 Restart` 버튼:
- 봇 프로세스를 안전하게 재시작합니다
- 재시작 후 약 6초 뒤 대시보드가 자동으로 새로고침됩니다

---

## 7. 텔레그램 명령어

봇 실행 중 텔레그램 채팅창에서 명령어를 보낼 수 있습니다.

### 조회 명령어

| 명령어 | 설명 |
|--------|------|
| `/status` | 현재 봇 상태 (모드, 잔고, Kill Switch 여부, 마지막 대조 시간) |
| `/positions` | 현재 열린 포지션 목록 |
| `/pnl` | 오늘 / 주간 손익 요약 |
| `/signals` | 최근 5개 매매 신호 |
| `/regime` | 현재 시장 국면 |
| `/health` | 전략별 건강도 상태 |
| `/opportunities` | 현재 스코어 상위 기회 목록 |

### 제어 명령어

| 명령어 | 설명 |
|--------|------|
| `/kill` | 긴급 정지 즉시 실행 (외출 중 문제 발생 시) |
| `/resume` | Kill Switch 해제 |
| `/mode [MODE]` | 운영 모드 변경 (예: `/mode OBSERVE`) |
| `/pause [전략명]` | 특정 전략 일시 중지 |
| `/approve [ID]` | 대기 중인 기회 수동 승인 |

### 텔레그램 봇 만들기 (아직 없다면)
1. 텔레그램에서 `@BotFather` 검색
2. `/newbot` 명령 전송
3. 봇 이름 입력 후 발급받은 토큰을 `.env`의 `TELEGRAM_BOT_TOKEN`에 입력
4. `@userinfobot`에서 본인 Chat ID를 확인해 `TELEGRAM_CHAT_ID`에 입력

---

## 8. 시스템 동작 방식

### 60초마다 반복되는 사이클

```
1. 바이낸스에서 최신 시세/캔들 수신
        ↓
2. BTC 데이터로 현재 시장 국면 판단
   → BTC_BULLISH / BTC_BEARISH / BTC_SIDEWAYS /
     ALT_ROTATION / HIGH_VOLATILITY / LOW_VOLATILITY / EVENT_RISK
        ↓
3. 해당 국면에 맞는 전략만 실행
        ↓
4. 전략 신호 → Opportunity 정규화 → 스코어링 (11개 규칙, 최대 20점)
        ↓
5. OpportunityQueue에서 점수 ≥ 8점 Top-N만 선발
        ↓
6. 포트폴리오 제약 검토 (최대 포지션 수, 방향 중복 등)
        ↓
7. PAPER 모드: 가상 진입 기록
   ACTIVE 모드: 실제 주문 전송
        ↓
8. 전략 건강도 엔진 (15분 주기): 성과 불량 전략 자동 일시정지
```

### 시장 국면 (Market Regime)

| 국면 | 의미 | 활성 전략 |
|------|------|----------|
| `BTC_BULLISH` | BTC 상승장 | Volatility Breakout, Early Trend |
| `BTC_BEARISH` | BTC 하락장 | Overreaction Reversal |
| `BTC_SIDEWAYS` | BTC 횡보장 | Overreaction Reversal, Volatility Breakout |
| `ALT_ROTATION` | 알트코인 순환매 | Overreaction Reversal, Early Trend |
| `HIGH_VOLATILITY` | 변동성 높음 | Overreaction Reversal |
| `LOW_VOLATILITY` | 변동성 낮음 | Volatility Breakout |
| `EVENT_RISK` | 이벤트 위험 | 없음 (대기) |
| `UNKNOWN` | 판단 불가 | 없음 (대기) |

### 전략 3가지 (v1.3)

**① Overreaction Reversal (오버리액션 되돌림)**
- RSI < 28 + 직전 3봉 중 -3% 이상 급락 캔들 존재 → 매수 신호
- RSI > 72 + 직전 3봉 중 +3% 이상 급등 캔들 존재 → 매도 신호
- 기본 TP: +2.5%, SL: -1.2%
- 주로 하락장/횡보장/고변동성 구간에서 사용

**② Volatility Expansion Breakout (변동성 확장 돌파)**
- Bollinger Band Squeeze 확인 후 폭발 초기만 포착
- 가격이 20봉 최고가 상향 돌파 + 거래량 2배 이상 → 매수 신호
- 기본 TP: 레인지 크기 × 0.8, SL: 레인지 고점 -0.5%
- 주로 상승장/횡보장/저변동성 구간에서 사용

**③ Early Trend Capture (초기 추세 포착)**
- EMA20 > EMA50 골든크로스 (직전 봉 기준으로 이번 봉에서 크로스) → 매수 신호
- RSI 40~60 구간 + 거래량 1.5배 이상 조건 추가 (과열 제외)
- 기본 TP: +2.0%, SL: -1.0%
- 주로 상승장/알트 순환매 구간에서 사용

### 스코어링 시스템 (11개 규칙)

각 기회(Opportunity)는 아래 기준으로 점수를 받습니다 (총 20점 만점, 8점 이상 실행):

| 항목 | 최대 점수 |
|------|---------|
| 전략 카테고리 x 레짐 적합성 | 3점 |
| 신뢰도 (confidence) | 3점 |
| 펀딩비 방향성 | 2점 |
| 거래량 강도 | 2점 |
| 기술적 지표 정렬 | 2점 |
| RSI 위치 | 2점 |
| 레짐 강도 | 2점 |
| 시간대 가중치 | 1점 |
| 중복 기회 패널티 | -2점 |
| 심볼 Tier 가중치 | ±1점 |

### 심볼 계층화 (Symbol Universe)

| Tier | 설명 | 최소 실행 점수 |
|------|------|--------------|
| Tier 1 | BTC, ETH — 핵심 유동성 | 8점 |
| Tier 2 | SOL, BNB 등 주요 알트 | 9점 |
| Tier 3 | 기타 알트코인 | 10점 |

### 전략 건강도 엔진 (Strategy Health)

15분마다 각 전략의 성과를 점검합니다:
- 최근 승률이 40% 미만이면 → `DEGRADED` 경고
- 연속 손실 3회 이상이면 → 자동 일시정지 (PAUSED)
- 건강도 회복 시 자동 재활성화

---

## 9. 안전 장치

시스템에는 여러 단계의 안전 장치가 내장되어 있습니다.

### 위험 관리 규칙

| 규칙 | 기준값 | 설명 |
|------|--------|------|
| 일일 최대 손실 | 잔고의 2% | 하루에 2% 이상 손실 시 신규 진입 차단 |
| 주간 최대 손실 | 잔고의 5% | 일주일에 5% 이상 손실 시 차단 |
| 최대 낙폭(MDD) | 잔고의 20% | 고점 대비 20% 손실 시 차단 |
| 포지션당 크기 | 잔고의 1% | 한 포지션에 잔고 1% 이상 투입 금지 |
| 최대 동시 포지션 | 2개 | 동시에 2개 이상 포지션 금지 |
| 동일 방향 제한 | 1개 | 같은 방향(롱/숏) 포지션은 1개만 허용 |
| 최대 레버리지 | 3배 | 3배 이상 레버리지 사용 금지 |

### Kill Switch (긴급 정지) — Soft/Hard 분리

**Soft Kill**: 신규 진입만 차단, 기존 포지션은 SL/TP로 보호
**Hard Kill**: 즉시 모든 포지션 청산 + 신규 진입 차단

자동 발동 조건:
- 하루에 5번 이상 API 오류 발생
- 거래소↔DB 데이터 불일치 발견
- 위험 관리 임계값 초과

수동으로도 언제든 작동 가능:
- 대시보드 `KILL SWITCH` 버튼
- 텔레그램 `/kill` 명령

### 감사 기록 (Audit Trail)

모든 주문의 상태 변화가 데이터베이스에 기록됩니다:
- 신호 생성 → 위험 검토 → 주문 제출 → 체결 → 완료
- 이 기록은 **절대 수정/삭제되지 않습니다** (immutable)

---

## 10. 자주 묻는 질문

**Q: 봇이 실제로 내 돈으로 거래하나요?**
A: `.env`의 `SYSTEM_MODE=OBSERVE` 또는 `BINANCE_TESTNET=true` 상태에서는 절대 실제 거래가 발생하지 않습니다. 실제 거래는 `SYSTEM_MODE=ACTIVE`이고 `BINANCE_TESTNET=false`일 때만 작동합니다.

**Q: 대시보드가 안 열려요**
A: 이미 봇이 실행 중이면 `http://localhost:8000`으로 접속하세요. 봇이 꺼져 있으면 대시보드도 없습니다. `start.bat`로 먼저 봇을 실행해주세요.

**Q: "Account balance unavailable (400)" 경고가 계속 나와요**
A: 테스트넷 API 키를 `testnet.binancefuture.com`에서 따로 발급받지 않았거나, 테스트넷 계정에 잔고가 없을 때 발생합니다. 봇 동작에는 문제없습니다.

**Q: 텔레그램 알림이 안 와요**
A: `.env`의 `TELEGRAM_BOT_TOKEN`과 `TELEGRAM_CHAT_ID`가 올바른지 확인하세요. 텔레그램 봇에 먼저 메시지를 보내야 대화가 활성화됩니다.

**Q: 전략 파라미터를 잘못 수정했어요**
A: 대시보드 Settings 패널에서 해당 전략의 `기본값 복원` 버튼을 클릭하면 됩니다. 또는 `data/strategy_params.json` 파일을 직접 삭제하면 전체 기본값으로 초기화됩니다.

**Q: OpenClaw AI가 "unavailable"이라고 나와요**
A: OpenClaw 에이전트가 이 컴퓨터에서 실행 중인지 확인하세요 (기본 포트: 18789). 꺼져 있어도 봇의 핵심 기능(데이터 수집, 전략 실행)은 정상 작동합니다.

**Q: 봇을 완전히 끄려면?**
A: 터미널에서 `Ctrl + C`를 누르거나, 작업 관리자에서 `python.exe` 프로세스를 종료하세요.

**Q: GitHub에 올릴 때 `.env` 파일이 올라가면 어떻게 되나요?**
A: API 키가 공개되어 해킹당할 수 있습니다. `.gitignore`에 이미 `.env`가 등록되어 있어 자동으로 제외됩니다. 혹시라도 올라갔다면 즉시 API 키를 폐기하고 새로 발급받으세요.

---

## API 엔드포인트 목록

대시보드(FastAPI)가 제공하는 HTTP API입니다.

### 조회 (GET)

| 엔드포인트 | 설명 |
|------------|------|
| `/api/snapshot` | 대시보드 전체 상태 스냅샷 |
| `/api/indicators` | 코인별 RSI, EMA, ATR, 자금조달률 |
| `/api/signals` | 최근 50개 매매 신호 |
| `/api/strategies` | 전략 목록 및 상태 |
| `/api/regime` | 현재 시장 국면 |
| `/api/live-positions` | 열린 포지션 목록 |
| `/api/trade-log` | 거래 기록 |
| `/api/settings` | 현재 시스템 설정 |
| `/api/strategy-params` | 전략 파라미터 전체 조회 |

### 제어 (POST)

| 엔드포인트 | 설명 |
|------------|------|
| `/api/kill-switch` | 긴급 정지 `{"reason": "이유"}` |
| `/api/kill-switch/reset` | Kill Switch 해제 |
| `/api/settings` | 시스템 설정 변경 |
| `/api/strategy-params/global` | 글로벌 파라미터 수정 |
| `/api/strategy-params/{전략명}` | 전략별 파라미터 수정 |
| `/api/strategy-params/{전략명}/reset` | 전략 파라미터 기본값 복원 |
| `/api/restart` | 봇 재시작 |

---

## OpenClaw AI 연결 안내

이 봇은 같은 컴퓨터에서 실행 중인 **OpenClaw 에이전트**와 연결됩니다.
OpenClaw에 아래 내용을 전달하면 봇 상태를 직접 조회할 수 있습니다:

```
22B Strategy Engine API가 http://localhost:8000 에서 실행 중입니다.

주요 조회 명령 (HTTP GET):
- /api/system-health   → 봇 전체 상태 (실행여부, 업타임, 각 컴포넌트 상태)
- /api/snapshot        → 실시간 대시보드 전체 데이터 (레짐, 잔고, 손익, 포지션)
- /api/regime          → 현재 시장 국면
- /api/indicators      → 코인별 RSI, EMA, ATR, 자금조달률
- /api/signals         → 최근 50개 매매 신호
- /api/strategies      → 전략별 성과 (승률, 거래횟수)
- /api/live-positions  → 현재 열린 포지션
- /api/trade-log       → 거래 기록
- /api/strategy-params → 전략 파라미터 전체 조회

제어 명령 (HTTP POST):
- /api/kill-switch              → 긴급 정지 {"reason": "이유"}
- /api/restart                  → 봇 재시작
- /api/strategy-params/global   → 글로벌 파라미터 수정
- /api/strategy-params/{name}   → 전략 파라미터 수정
```

---

## 라이선스 / 면책 조항

이 소프트웨어는 **교육 및 연구 목적**으로 제작되었습니다.
실제 거래에 사용 시 발생하는 손실에 대해 개발자는 책임을 지지 않습니다.
암호화폐 투자는 원금 손실 위험이 있으며, 항상 본인이 감당할 수 있는 범위 내에서 투자하세요.
