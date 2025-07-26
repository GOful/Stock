import sqlite3
import requests
from pykrx import stock
from datetime import datetime, timezone, timedelta
import ntplib
import logging
import warnings
import os
from pathlib import Path

# -----------------------------------------------------------
# 경고/불필요한 로그는 모두 비활성화
logging.getLogger("streamlit.elements.lib.policies").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=FutureWarning)

# -----------------------------------------------------------
# NTP 서버에서 한국(서울) 표준시를 받아오는 함수
def get_now_seoul():
    client = ntplib.NTPClient()
    resp = client.request("pool.ntp.org", version=3)  # NTP 서버 요청
    dt_utc = datetime.fromtimestamp(resp.tx_time, tz=timezone.utc)
    seoul_tz = timezone(timedelta(hours=9))
    return dt_utc.astimezone(seoul_tz)

# -----------------------------------------------------------
# 오늘 날짜 및 데이터 수집 구간 결정
now = get_now_seoul()
today = now.date()

if now.hour < 17:
    # 오후 5시(장마감) 전이면 어제까지 데이터만 유효
    end_date = today - timedelta(days=1)
    print(f"{now.strftime('%Y-%m-%d %H:%M:%S')} 현재 17:00 이전, 오늘({today}) 데이터는 제외합니다.")
else:
    # 오후 5시 이후면 오늘 데이터까지 수집
    end_date = today
    print(f"{now.strftime('%Y-%m-%d %H:%M:%S')} 현재 17:00 이후, 오늘({today})까지 수집합니다.")

# 수집 시작일: 종료일 기준 7일 전
start_date = end_date - timedelta(days=7)
print(f"데이터 수집 구간: {start_date} ▶ {end_date}")

# -----------------------------------------------------------
# DB 경로 설정 (스크립트 파일 기준 같은 폴더)
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "market_ohlcv.db"          # 메인 OHLCV DB
DB_PATH_2 = BASE_DIR / "operating_income_1q_naver.db"    # 추가 재무정보 DB

print("cwd:", os.getcwd())
print("DB absolute path:", DB_PATH)

# -----------------------------------------------------------
# 메인 DB 연결 및 테이블 생성
conn = sqlite3.connect(str(DB_PATH))
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS market_ohlcv (
    date TEXT,        -- 날짜(YYYYMMDD)
    ticker TEXT,      -- 종목코드
    name TEXT,        -- 종목명
    open REAL,        -- 시가
    high REAL,        -- 고가
    low REAL,         -- 저가
    close REAL,       -- 종가
    volume INTEGER,   -- 거래량
    value REAL,       -- 거래대금
    change_rate REAL, -- 등락률(%)
    market_cap REAL,  -- 시가총액
    thstrm_amount REAL, -- 1분기 영업이익 등 재무정보(추가)
    PRIMARY KEY (date, ticker)
)
""")
conn.commit()

# -----------------------------------------------------------
# 기존 테이블에 컬럼이 없으면 추가(스키마 자동 보완)
cursor.execute("PRAGMA table_info(market_ohlcv)")
cols = [row[1] for row in cursor.fetchall()]
if "market_cap" not in cols:
    cursor.execute("ALTER TABLE market_ohlcv ADD COLUMN market_cap REAL")
    conn.commit()
if "thstrm_amount" not in cols:
    cursor.execute("ALTER TABLE market_ohlcv ADD COLUMN thstrm_amount REAL")
    conn.commit()

# -----------------------------------------------------------
# 202일 이전의 오래된 데이터는 삭제(성능 및 용량 관리)
cutoff_date = (end_date - timedelta(days=202)).strftime("%Y%m%d")
cursor.execute("DELETE FROM market_ohlcv WHERE date < ?", (cutoff_date,))
conn.commit()
print(f"{cutoff_date} 이전 데이터 삭제 완료")

# -----------------------------------------------------------
# 날짜 생성기(시작~종료 날짜까지 for loop용 generator)
def daterange(start_date, end_date):
    for n in range((end_date - start_date).days + 1):
        yield start_date + timedelta(days=n)

# -----------------------------------------------------------
# [추가] thstrm_amount_1q.db에서 (stock_code, thstrm_amount) 모두 읽어서 dict화
conn2 = sqlite3.connect(str(DB_PATH_2))
cursor2 = conn2.cursor()
cursor2.execute("SELECT stock_code, thstrm_amount FROM operating_income_1q")
# 종목코드 6자리 문자열로 통일 (zfill)
thstrm_dict = {str(row[0]).zfill(6): row[1] for row in cursor2.fetchall()}
conn2.close()

# -----------------------------------------------------------
# 메인 데이터 수집 및 저장 루프
for single_date in daterange(start_date, end_date):
    date_str = single_date.strftime("%Y%m%d")  # YYYYMMDD 형식

    try:
        # (1) 이미 데이터가 있으면 스킵(중복 저장 방지)
        cursor.execute(
            "SELECT 1 FROM market_ohlcv WHERE date=? LIMIT 1",
            (date_str,)
        )
        if cursor.fetchone():
            print(f"{date_str} ▶ 이미 데이터 존재, 스킵")
            continue

        # (2) PyKRX로부터 시장별 OHLCV 데이터 로드
        df_ohlcv = stock.get_market_ohlcv(date_str, market="ALL")
        if df_ohlcv is None or df_ohlcv.empty:
            print(f"{date_str} ▶ 시장 휴장 혹은 데이터 없음, 스킵")
            continue

        # (3) 컬럼명 통일 및 부가정보 추가
        ohlcv = df_ohlcv.iloc[:, :7].copy()
        ohlcv.columns = [
            "open", "high", "low", "close",
            "volume", "value", "change_rate"
        ]
        ohlcv["date"] = date_str
        ohlcv["ticker"] = ohlcv.index              # 종목코드
        ohlcv["name"] = ohlcv["ticker"].apply(stock.get_market_ticker_name)
        ohlcv["ticker"] = ohlcv.index.map(lambda x: str(x).zfill(6))

        # (4) 시가총액 데이터와 병합(종목코드 기준)
        cap_df = stock.get_market_cap_by_ticker(date_str, market="ALL")
        cap_df = cap_df.rename(columns={"시가총액": "market_cap"})
        cap_df = cap_df[["market_cap"]]
        cap_df.index = cap_df.index.map(lambda x: str(x).zfill(6))

        ohlcv = ohlcv.set_index("ticker")
        ohlcv = ohlcv.join(cap_df["market_cap"], how="left")
        ohlcv = ohlcv.reset_index()

        # (5) 휴장/상장폐지 등으로 종가가 0인 데이터는 제거
        ohlcv = ohlcv[ohlcv["close"] != 0]
        if ohlcv.empty:
            print(f"{date_str} ▶ 데이터없음(휴장일)")
            continue

        # (6) [추가] 종목코드별 thstrm_amount 컬럼 추가 (dict 매핑)
        ohlcv["thstrm_amount"] = ohlcv["ticker"].map(thstrm_dict)

        # (7) 필요한 컬럼만 DB에 저장
        ohlcv[[
            "date", "ticker", "name", "open", "high", "low",
            "close", "volume", "value", "change_rate",
            "market_cap", "thstrm_amount"
        ]].to_sql("market_ohlcv", conn,
                  if_exists="append", index=False)

        print(f"{date_str} ▶ {len(ohlcv)}건 저장 완료")

    except Exception as e:
        # 날짜별 에러가 나도 전체 루프는 중단하지 않음
        print(f"Error on {date_str}: {e}")
        continue

# -----------------------------------------------------------
# 모든 DB 연결 종료
conn.close()
