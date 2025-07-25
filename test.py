"""
import sqlite3
from pykrx import stock

# DB 연결
conn = sqlite3.connect("market_ohlcv.db")
cursor = conn.cursor()

# 삭제 실행
cursor.execute("DELETE FROM market_ohlcv WHERE date = ?", ("20250721",))
conn.commit()

# 최적화(선택)
cursor.execute("VACUUM;")
conn.commit()

conn.close()
"""

"""
import ntplib
from datetime import datetime
import pytz

client = ntplib.NTPClient()
resp   = client.request("pool.ntp.org", version=3)
utc_dt = datetime.utcfromtimestamp(resp.tx_time)
seoul  = pytz.timezone("Asia/Seoul")
now    = seoul.localize(utc_dt)

print(now)
"""

import sqlite3
import requests
from pykrx import stock
from datetime import datetime, timezone, timedelta
import ntplib
import logging
import warnings
import os
from pathlib import Path
import FinanceDataReader as fdr
import pandas as pd

logging.getLogger("streamlit.elements.lib.policies").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=FutureWarning)

# 0) NTP 서버로부터 현재 시각(Asia/Seoul) 가져오기
def get_now_seoul():
    client = ntplib.NTPClient()
    resp = client.request("pool.ntp.org", version=3)
    dt_utc = datetime.fromtimestamp(resp.tx_time, tz=timezone.utc)
    seoul_tz = timezone(timedelta(hours=9))
    return dt_utc.astimezone(seoul_tz)

now = get_now_seoul()
today = now.date()

if now.hour < 17:
    end_date = today - timedelta(days=1)
    print(f"{now.strftime('%Y-%m-%d %H:%M:%S')} 현재 17:00 이전, 오늘({today}) 데이터는 제외합니다.")
else:
    end_date = today
    print(f"{now.strftime('%Y-%m-%d %H:%M:%S')} 현재 17:00 이후, 오늘({today})까지 수집합니다.")

start_date = end_date - timedelta(days=7)
print(f"데이터 수집 구간: {start_date} ▶ {end_date}")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH  = BASE_DIR / "market_ohlcv.db"

print("cwd:", os.getcwd())
print("DB absolute path:", DB_PATH)

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
    market_cap REAL,
    operating_income REAL,
    PRIMARY KEY (date, ticker)
)
""")
conn.commit()

# (이미 테이블이 있었다면 아래 ALTER 로컬럼 추가)
cursor.execute("PRAGMA table_info(market_ohlcv)")
cols = [row[1] for row in cursor.fetchall()]
if "market_cap" not in cols:
    cursor.execute("ALTER TABLE market_ohlcv ADD COLUMN market_cap REAL")
    conn.commit()
if "operating_income" not in cols:
    cursor.execute("ALTER TABLE market_ohlcv ADD COLUMN operating_income REAL")
    conn.commit()

cutoff_date = (end_date - timedelta(days=202)).strftime("%Y%m%d")
cursor.execute("DELETE FROM market_ohlcv WHERE date < ?", (cutoff_date,))
conn.commit()
print(f"{cutoff_date} 이전 데이터 삭제 완료")

def daterange(start_date, end_date):
    for n in range((end_date - start_date).days + 1):
        yield start_date + timedelta(days=n)

# ──────────────────────────────
# 전체 KRX 종목 목록 + 티커별 최근분기 영업이익 캐싱
# ──────────────────────────────
print("전체 KRX 종목 코드 및 영업이익 데이터 캐싱 중...")
krx_list = fdr.StockListing('KRX')
op_income_dict = {}

for row in krx_list.itertuples():
    ticker = str(row.Code).zfill(6)
    try:
        fs = fdr.FinancialStatement(ticker)
        # 영업이익이 있을 때만 저장
        if fs is not None and not fs.empty and '영업이익' in fs.columns:
            latest_quarter = fs.index[-1]
            operating_income = fs.loc[latest_quarter, '영업이익']
            # float 또는 None (에러/결측치 대비)
            try:
                operating_income = float(operating_income)
            except:
                operating_income = None
            op_income_dict[ticker] = operating_income
        else:
            op_income_dict[ticker] = None
    except Exception as e:
        op_income_dict[ticker] = None

print(f"총 {len(op_income_dict)}종목 영업이익 정보 캐싱 완료.")

# ──────────────────────────────
# 수집 및 저장 루프
# ──────────────────────────────

for single_date in daterange(start_date, end_date):
    date_str = single_date.strftime("%Y%m%d")

    try:
        cursor.execute(
            "SELECT 1 FROM market_ohlcv WHERE date=? LIMIT 1",
            (date_str,)
        )
        if cursor.fetchone():
            print(f"{date_str} ▶ 이미 데이터 존재, 스킵")
            continue

        df_ohlcv = stock.get_market_ohlcv(date_str, market="ALL")
        if df_ohlcv is None or df_ohlcv.empty:
            print(f"{date_str} ▶ 시장 휴장 혹은 데이터 없음, 스킵")
            continue

        ohlcv = df_ohlcv.iloc[:, :7].copy()
        ohlcv.columns = [
            "open","high","low","close",
            "volume","value","change_rate"
        ]
        ohlcv["date"]   = date_str
        ohlcv["ticker"] = ohlcv.index
        ohlcv["name"]   = ohlcv["ticker"].apply(
            stock.get_market_ticker_name
        )

        # ticker 문자열 6자리 통일
        ohlcv["ticker"] = ohlcv.index.map(lambda x: str(x).zfill(6))

        # 시가총액 추가
        cap_df = stock.get_market_cap_by_ticker(date_str, market="ALL")
        cap_df = cap_df.rename(columns={"시가총액": "market_cap"})
        cap_df = cap_df[["market_cap"]]
        cap_df.index = cap_df.index.map(lambda x: str(x).zfill(6))

        ohlcv = ohlcv.set_index("ticker")
        ohlcv = ohlcv.join(cap_df["market_cap"], how="left")
        ohlcv = ohlcv.reset_index()

        # **여기서 영업이익 컬럼 추가**
        ohlcv["operating_income"] = ohlcv["ticker"].map(op_income_dict)

        ohlcv = ohlcv[ohlcv["close"] != 0]
        if ohlcv.empty:
            print(f"{date_str} ▶ 데이터없음(휴장일)")
            continue

        ohlcv[[
            "date","ticker","name","open","high","low",
            "close","volume","value","change_rate",
            "market_cap","operating_income"
        ]].to_sql("market_ohlcv", conn,
                  if_exists="append", index=False)

        print(f"{date_str} ▶ {len(ohlcv)}건 저장 완료 (영업이익 포함)")

    except Exception as e:
        print(f"Error on {date_str}: {e}")
        continue

conn.close()
