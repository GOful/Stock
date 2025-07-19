
import sqlite3
import requests
from pykrx import stock
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

# ————————————————————————————————
# 0) 네이버 HEAD 요청으로 현재 시각(Asia/Seoul) 가져오기
# ————————————————————————————————
def get_now_seoul():
    resp = requests.head("https://www.naver.com", timeout=5)
    resp.raise_for_status()
    date_str = resp.headers["Date"]             # e.g. 'Thu, 17 Jul 2025 08:45:12 GMT'
    dt_utc   = parsedate_to_datetime(date_str)  # tzinfo=UTC
    seoul_tz = timezone(timedelta(hours=9))     # UTC+9
    return dt_utc.astimezone(seoul_tz)

# ————————————————————————————————
# 1) 오늘/어제 기준으로 수집 종결일(end_date) 결정
# ————————————————————————————————
now = get_now_seoul()
today = now.date()

if now.hour < 17:
    # 17시 이전이면 오늘 데이터는 아직 미완료이므로 어제까지만
    end_date = today - timedelta(days=1)
    print(f"{now.strftime('%Y-%m-%d %H:%M:%S')} 현재 17:00 이전, 오늘({today}) 데이터는 제외합니다.")
else:
    # 17시 이후면 오늘까지 수집
    end_date = today
    print(f"{now.strftime('%Y-%m-%d %H:%M:%S')} 현재 17:00 이후, 오늘({today})까지 수집합니다.")
# ————————————————————————————————
# 2) 시작일(start_date)은 end_date 기준 7일 전
# ————————————————————————————————
start_date = end_date - timedelta(days=7)
print(f"데이터 수집 구간: {start_date} ▶ {end_date}")
# ————————————————————————————————
# 3) DB 연결 및 테이블 생성
# ————————————————————————————————
import os
from pathlib import Path

# 스크립트(.py) 파일이 있는 디렉터리
BASE_DIR = Path(__file__).resolve().parent
DB_PATH  = BASE_DIR / "market_ohlcv.db"

print("cwd:", os.getcwd())
print("DB absolute path:", DB_PATH)

# -- 여기서만 connect! --
conn   = sqlite3.connect(str(DB_PATH))
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS market_ohlcv (
    date TEXT,
    ticker TEXT,
    name TEXT,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume INTEGER,
    value REAL,
    change_rate REAL,
    PRIMARY KEY (date, ticker)
)
""")
conn.commit()

# ————————————————————————————————
# 4) 202일 이전 데이터 삭제
# ————————————————————————————————
# 문자열 YYYYMMDD 로 저장되어 있으므로 그대로 비교해도古い日付が削除できます.
cutoff_date = (end_date - timedelta(days=202)).strftime("%Y%m%d")
cursor.execute("DELETE FROM market_ohlcv WHERE date < ?", (cutoff_date,))
conn.commit()
print(f"{cutoff_date} 이전 데이터 삭제 완료")

# ————————————————————————————————
# 4) 날짜 생성기
# ————————————————————————————————
def daterange(start_date, end_date):
    for n in range((end_date - start_date).days + 1):
        yield start_date + timedelta(days=n)

# ————————————————————————————————
# 5) 데이터 수집 및 저장
# ————————————————————————————————
for single_date in daterange(start_date, end_date):
    date_str = single_date.strftime("%Y%m%d")
    try:
        # 이미 해당 날짜 데이터 존재 여부 확인
        cursor.execute("SELECT 1 FROM market_ohlcv WHERE date=? LIMIT 1", (date_str,))
        if cursor.fetchone():
            print(f"{date_str} ▶ 이미 데이터 존재, 스킵")
            continue

        df = stock.get_market_ohlcv(date_str, market="ALL")
        if df is None or df.empty:
            print(f"{date_str} ▶ 시장 휴장 혹은 데이터 없음, 스킵")
            continue

        ohlcv = df.iloc[:, :7].copy()
        ohlcv.columns = ["open","high","low","close","volume","value","change_rate"]
        ohlcv["date"]   = date_str
        ohlcv["ticker"] = ohlcv.index
        ohlcv["name"]   = ohlcv["ticker"].apply(stock.get_market_ticker_name)

        # close가 0인 행 제거
        ohlcv = ohlcv[ohlcv["close"] != 0]
        if ohlcv.empty:
            print(f"{date_str} ▶ close가 0인 데이터만 있어 스킵(휴장일)")
            continue

        ohlcv[[
            "date","ticker","name","open","high","low",
            "close","volume","value","change_rate"
        ]].to_sql("market_ohlcv", conn, if_exists="append", index=False)

        print(f"{date_str} ▶ {len(ohlcv)}건 저장 완료")

    except Exception as e:
        print(f"Error on {date_str}: {e}")

conn.close()