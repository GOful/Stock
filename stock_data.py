
import sqlite3
import requests
from pykrx import stock
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import ntplib

# ————————————————————————————————
# 0) NTP 서버로부터 현재 시각(Asia/Seoul) 가져오기
# ————————————————————————————————
def get_now_seoul():
    client = ntplib.NTPClient()
    # pool.ntp.org에서 UDP로 시간 요청
    resp = client.request("pool.ntp.org", version=3)
    # UTC 기준 datetime 생성
    dt_utc = datetime.fromtimestamp(resp.tx_time, tz=timezone.utc)
    # 서울(UTC+9) 타임존으로 변환
    seoul_tz = timezone(timedelta(hours=9))
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
    market_cap REAL,
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
        # 1) 중복 검사
        cursor.execute(
            "SELECT 1 FROM market_ohlcv WHERE date=? LIMIT 1",
            (date_str,)
        )
        if cursor.fetchone():
            print(f"{date_str} ▶ 이미 데이터 존재, 스킵")
            continue

        # 2) OHLCV 불러오기
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

        # --- 4) 시가총액 불러오기 & 정리 ---
        cap_df = stock.get_market_cap_by_ticker(date_str, market="ALL")
        #print(cap_df.head(), cap_df.columns)
        cap_df = cap_df.rename(columns={"시가총액": "market_cap"})
        cap_df = cap_df[["market_cap"]]
        cap_df.index = cap_df.index.map(lambda x: str(x).zfill(6))

        #print("ohlcv sample ticker:", ohlcv["ticker"].head().tolist())
        #print("ohlcv ticker dtype:", ohlcv["ticker"].dtype)
        # --- 5) join 으로 병합 ---
        ohlcv = ohlcv.set_index("ticker")
        ohlcv = ohlcv.join(cap_df["market_cap"], how="left")
        ohlcv = ohlcv.reset_index()

        # 5) 휴장일 필터링(close == 0)
        ohlcv = ohlcv[ohlcv["close"] != 0]
        if ohlcv.empty:
            print(f"{date_str} ▶ close가 0인 데이터만 있어 스킵")
            continue

        # 6) DB에 저장
        ohlcv[[
            "date","ticker","name","open","high","low",
            "close","volume","value","change_rate",
            "market_cap"
        ]].to_sql("market_ohlcv", conn,
                  if_exists="append", index=False)

        print(f"{date_str} ▶ {len(ohlcv)}건 저장 완료")

    except Exception as e:
        print(f"Error on {date_str}: {e}")
        # 에러 난 날도 넘기고 다음 날짜로 진행
        continue

conn.close()