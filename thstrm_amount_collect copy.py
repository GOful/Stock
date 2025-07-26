import pandas as pd
import sqlite3
from pykrx import stock
import OpenDartReader
import time

# 숫자 지수표기 없이 실수로 출력
pd.set_option('display.float_format', '{:.0f}'.format)

# DART API 키
api_key = '856f8562ecff6ebd5233943155b5157ae93c7c67'
dart = OpenDartReader(api_key)

year = 2025
reprt_code = '11013'  # 1분기

# 1. 전체 코스피+코스닥 ticker
tickers = stock.get_market_ticker_list(market="ALL")
tickers = list(set(tickers))

results = []

# 2. SQLite 연결 및 테이블 생성
db_path = "thstrm_amount_1q.db"
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("""
    CREATE TABLE IF NOT EXISTS operating_income_1q (
        stock_code TEXT PRIMARY KEY,
        thstrm_amount REAL
    )
""")
conn.commit()

for idx, stock_code in enumerate(tickers, 1):
    try:
        fs = dart.finstate_all(stock_code, year, reprt_code)
        thstrm_amount = None

        if fs is not None and not fs.empty:
            fs['account_nm'] = fs['account_nm'].astype(str)
            row = fs[fs['account_nm'].str.contains('영업이익', na=False)]
            if not row.empty:
                val = row['thstrm_amount'].values[0]
                if pd.notnull(val):
                    try:
                        thstrm_amount = float(str(val).replace(',', '').replace(' ', ''))
                    except Exception:
                        thstrm_amount = None

        # DB에 저장 (중복시 REPLACE)
        cur.execute(
            "INSERT OR REPLACE INTO operating_income_1q (stock_code, thstrm_amount) VALUES (?, ?)",
            (stock_code, thstrm_amount)
        )

        results.append({"stock_code": stock_code, "thstrm_amount": thstrm_amount})

        # 50개마다 커밋 및 진행상황 출력
        if idx % 50 == 0:
            conn.commit()
            print(f"{idx}개 저장 및 커밋 완료")

        # 과도한 요청 방지
        time.sleep(0.1)
    except Exception as e:
        print(f"Error: {stock_code} - {e}")

# 마지막 잔여분 커밋
conn.commit()
conn.close()

# DataFrame으로 변환 후 상위 10개 출력
df = pd.DataFrame(results)
print("\n저장된 결과 상위 10개 미리보기:")
print(df.head(10))
