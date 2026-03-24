# 22B Strategy Engine (Dry-Run) - Linux Installation Guide

> **NOTE**: 원본 repo(sinmb79/straregy-consol)에는 dry-run 코드가 포함되어 있지 않습니다.
> 반드시 이 디렉토리의 코드를 통째로 서버에 복사하거나, fork한 repo를 사용하세요.

---

## 1. System Requirements

| Item | Minimum | Recommended |
|------|---------|-------------|
| OS | Ubuntu 20.04+ / Debian 11+ / CentOS 8+ | Ubuntu 22.04 LTS |
| Python | 3.11+ | 3.12 |
| RAM | 512 MB | 2 GB |
| Disk | 500 MB | 2 GB |
| Network | Outbound HTTPS (443) | Low-latency to Binance |

---

## 2. Initial Server Setup

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl wget
```

---

## 3. 코드 배포 (3가지 방법 중 택 1)

### 방법 A: scp로 직접 복사 (가장 간단)

로컬 Windows에서:
```powershell
# Windows PowerShell / CMD
scp -r C:\temp\claude\22b user@your-server-ip:~/22b-engine
```

서버에서:
```bash
cd ~/22b-engine
```

### 방법 B: GitHub fork 후 clone

1. GitHub에서 https://github.com/sinmb79/straregy-consol 를 Fork
2. Fork한 repo에 dry-run 변경사항을 push
3. 서버에서:

```bash
cd ~
git clone https://github.com/YOUR_USERNAME/straregy-consol.git 22b-engine
cd 22b-engine
```

### 방법 C: tar 압축 전송

로컬에서:
```powershell
# Windows - Git Bash 또는 WSL
cd /c/temp/claude/22b
tar czf 22b-engine.tar.gz --exclude='.git' --exclude='venv' --exclude='__pycache__' --exclude='data' .
scp 22b-engine.tar.gz user@your-server-ip:~/
```

서버에서:
```bash
mkdir -p ~/22b-engine && cd ~/22b-engine
tar xzf ~/22b-engine.tar.gz
rm ~/22b-engine.tar.gz
```

---

## 4. Python 환경 설치 (2가지 방법 중 택 1)

### 방법 1: Conda (Recommended)

#### Miniconda 설치

```bash
# Miniconda 다운로드 및 설치
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O miniconda.sh
bash miniconda.sh -b -p $HOME/miniconda3
rm miniconda.sh

# PATH 등록
eval "$($HOME/miniconda3/bin/conda shell.bash hook)"
conda init bash
source ~/.bashrc
```

#### conda 환경 생성 (environment.yml 사용)

```bash
cd ~/22b-engine
conda env create -f environment.yml
conda activate 22b-engine
```

#### 환경 확인

```bash
conda activate 22b-engine
python --version          # 3.12.x
python -c "import fastapi; print('fastapi OK')"
python -c "import pandas; print('pandas OK')"
python -c "import httpx; print('httpx OK')"
```

#### conda 주요 명령어

```bash
# 환경 활성화
conda activate 22b-engine

# 환경 비활성화
conda deactivate

# 환경 목록 보기
conda env list

# 환경 삭제 후 재생성
conda env remove -n 22b-engine
conda env create -f environment.yml

# 패키지 추가 설치
conda activate 22b-engine
pip install some-package

# 현재 환경 내보내기 (다른 서버에 복제할 때)
conda env export > environment-lock.yml
```

### 방법 2: venv (시스템 Python 사용)

```bash
# Python 3.11+ 확인
python3 --version

# 3.11 미만이면:
sudo apt install -y software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev

# venv 생성
cd ~/22b-engine
python3 -m venv venv
source venv/bin/activate

# 의존성 설치
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 5. 환경 변수 설정

```bash
cd ~/22b-engine
touch .env
nano .env
```

아래 내용을 붙여넣기:

```env
# ============================================================
# 22B Strategy Engine - Dry-Run Configuration
# ============================================================

# --- Binance API (READ-ONLY keys are sufficient for dry-run) ---
BINANCE_API_KEY=your_binance_api_key_here
BINANCE_API_SECRET=your_binance_api_secret_here
BINANCE_TESTNET=false

# --- Dry-Run Mode ---
DRY_RUN_ENABLED=true
DRY_RUN_INITIAL_BALANCE=10000
DRY_RUN_POSITION_SIZE_PCT=0.10
DRY_RUN_FEE_RATE=0.0004
DRY_RUN_SLIPPAGE_PCT=0.0005
DRY_RUN_REPORT_INTERVAL_MIN=60

# --- System ---
SYSTEM_MODE=OBSERVE
LOG_LEVEL=INFO
DB_PATH=./data/trading.db

# --- Symbols to Track ---
TRACKED_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT
CANDLE_INTERVALS=1h,4h

# --- Dashboard ---
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=8000

# --- Telegram Alerts (Optional) ---
# TELEGRAM_BOT_TOKEN=your_bot_token
# TELEGRAM_CHAT_ID=your_chat_id

# --- AI Analysis (Optional) ---
AI_ENABLED=false
```

### Binance API Key 발급

1. https://www.binance.com/en/my/settings/api-management 접속
2. 새 API Key 생성
3. **dry-run 모드에서는 "Read" 권한만 필요** (거래 권한 불필요)
4. IP 제한을 서버 IP로 설정 (보안)
5. API Key와 Secret을 `.env`에 복사

### Telegram Bot 설정 (선택)

1. Telegram에서 @BotFather에게 메시지
2. `/newbot` 명령으로 봇 생성
3. 받은 토큰을 `TELEGRAM_BOT_TOKEN`에 입력
4. 봇과 대화 시작 후 `https://api.telegram.org/bot<TOKEN>/getUpdates` 에서 `chat_id` 확인
5. `TELEGRAM_CHAT_ID`에 입력

---

## 6. 데이터 디렉토리 생성

```bash
mkdir -p ~/22b-engine/data
```

---

## 7. 설치 테스트

```bash
cd ~/22b-engine

# conda 사용시
conda activate 22b-engine

# venv 사용시
# source venv/bin/activate

python test_dryrun.py
```

기대 출력:
```
============================================================
  ALL 8 TESTS PASSED
============================================================
```

---

## 8. Dry-Run 실행

### 방법 A: 기본 실행 (포그라운드)

```bash
conda activate 22b-engine
cd ~/22b-engine
python run_dryrun.py --balance 10000
```

### 방법 B: 커스텀 파라미터

```bash
python run_dryrun.py \
  --balance 20000 \
  --fee 0.04 \
  --slippage 0.05 \
  --size-pct 10 \
  --report-interval 30
```

### 방법 C: .env 환경변수 기반

```bash
python -m bot.main
```

---

## 9. systemd 서비스 등록 (항상 실행)

### Conda 환경일 때

```bash
# conda 환경의 python 경로 확인
conda activate 22b-engine
which python
# 예: /home/trading/miniconda3/envs/22b-engine/bin/python
```

```bash
sudo nano /etc/systemd/system/22b-engine.service
```

```ini
[Unit]
Description=22B Strategy Engine (Dry-Run)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=trading
Group=trading
WorkingDirectory=/home/trading/22b-engine

# === Conda 환경 사용시 (경로를 위에서 확인한 값으로 변경) ===
ExecStart=/home/trading/miniconda3/envs/22b-engine/bin/python run_dryrun.py --balance 10000

# === venv 사용시 (위 줄 대신 아래 줄 사용) ===
# ExecStart=/home/trading/22b-engine/venv/bin/python run_dryrun.py --balance 10000

Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/home/trading/22b-engine/data
ProtectHome=read-only

[Install]
WantedBy=multi-user.target
```

### 서비스 시작

```bash
sudo systemctl daemon-reload
sudo systemctl enable 22b-engine
sudo systemctl start 22b-engine
```

### 서비스 관리

```bash
# 상태 확인
sudo systemctl status 22b-engine

# 실시간 로그
sudo journalctl -u 22b-engine -f

# 최근 100줄
sudo journalctl -u 22b-engine -n 100

# 재시작 / 중지
sudo systemctl restart 22b-engine
sudo systemctl stop 22b-engine
```

---

## 10. 대시보드 접속

브라우저에서:
```
http://<서버IP>:8000
```

### 방화벽 설정

```bash
# UFW
sudo ufw allow 8000/tcp

# iptables
sudo iptables -A INPUT -p tcp --dport 8000 -j ACCEPT
```

### Nginx 리버스 프록시 + HTTPS (선택)

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo nano /etc/nginx/sites-available/22b-engine
```

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/22b-engine /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# SSL 인증서 (도메인이 있을 때)
sudo certbot --nginx -d your-domain.com
```

---

## 11. 유지보수

### 로그 로테이션

```bash
sudo nano /etc/logrotate.d/22b-engine
```

```
/home/trading/22b-engine/data/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
}
```

### 자동 복구 (cron)

```bash
crontab -e
```

```cron
# 5분마다 서비스 상태 확인, 죽었으면 자동 재시작
*/5 * * * * systemctl is-active --quiet 22b-engine || systemctl restart 22b-engine
```

### DB 백업 (매일)

```bash
mkdir -p ~/22b-engine/data/backups
crontab -e
```

```cron
0 0 * * * cp /home/trading/22b-engine/data/trading.db /home/trading/22b-engine/data/backups/trading_$(date +\%Y\%m\%d).db
```

### 업그레이드

```bash
cd ~/22b-engine
sudo systemctl stop 22b-engine

# 새 코드 복사 (scp 또는 git pull)
# scp -r 로컬경로/* user@server:~/22b-engine/

conda activate 22b-engine
python test_dryrun.py

sudo systemctl start 22b-engine
```

---

## 12. Troubleshooting

| 증상 | 해결 |
|------|------|
| `ModuleNotFoundError` | `conda activate 22b-engine` 또는 `source venv/bin/activate` |
| `BINANCE_API_KEY is not set` | `.env` 파일 확인 |
| Port 8000 사용 중 | `.env`에서 `DASHBOARD_PORT` 변경 또는 `kill $(lsof -ti:8000)` |
| WebSocket 연결 실패 | 방화벽 8000 포트 확인, Nginx WebSocket 프록시 설정 확인 |
| DB locked | 인스턴스 중복 실행 확인: `ps aux | grep run_dryrun` |
| 시그널 안 나옴 | Binance 데이터 수집까지 2~5분 대기 필요 |
| conda 명령 안됨 | `source ~/.bashrc` 또는 `eval "$($HOME/miniconda3/bin/conda shell.bash hook)"` |

---

## Quick Start (복사-붙여넣기용)

### Conda 버전

```bash
# 1. Miniconda 설치
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O miniconda.sh
bash miniconda.sh -b -p $HOME/miniconda3 && rm miniconda.sh
eval "$($HOME/miniconda3/bin/conda shell.bash hook)" && conda init bash && source ~/.bashrc

# 2. 코드 복사 (로컬에서 scp로 전송했다고 가정)
cd ~/22b-engine

# 3. Conda 환경 생성
conda env create -f environment.yml
conda activate 22b-engine

# 4. 환경 설정
touch .env && nano .env
# BINANCE_API_KEY, BINANCE_API_SECRET, DRY_RUN_ENABLED=true 설정

# 5. 데이터 디렉토리
mkdir -p data

# 6. 테스트
python test_dryrun.py

# 7. 실행
python run_dryrun.py --balance 10000
```

### venv 버전

```bash
# 1. 코드 복사 (로컬에서 scp로 전송했다고 가정)
cd ~/22b-engine

# 2. venv 생성 + 의존성 설치
python3 -m venv venv && source venv/bin/activate
pip install --upgrade pip && pip install -r requirements.txt

# 3. 환경 설정
touch .env && nano .env
# BINANCE_API_KEY, BINANCE_API_SECRET, DRY_RUN_ENABLED=true 설정

# 4. 데이터 디렉토리
mkdir -p data

# 5. 테스트
python test_dryrun.py

# 6. 실행
python run_dryrun.py --balance 10000
```
