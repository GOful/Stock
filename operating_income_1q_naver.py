import pandas as pd
import sqlite3
import requests
from lxml import html
from pykrx import stock

pd.set_option('display.float_format', '{:,}'.format)

XPATH = '//*[@id="content"]/div[5]/div[1]/table/tbody/tr[2]/td[9]'
DB_PATH = "operating_income_1q_naver.db"

# 전체 상장 종목코드 리스트(코스피+코스닥)
tickers = stock.get_market_ticker_list(market="ALL")
tickers = list(set(tickers))

def get_op_profit_by_xpath(ticker, xpath_expr):
    url = f"https://finance.naver.com/item/main.naver?code={ticker}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        doc = html.fromstring(r.text)
        elems = doc.xpath(xpath_expr)
        if elems:
            value_str = elems[0].text_content().replace(",", "").strip()
            if value_str == "" or value_str == "-":
                return None
            # 억원(소수/음수 모두) → 원화 정수 변환
            return int(float(value_str) * 100_000_000)
        else:
            return None
    except Exception as e:
        print(f"Error({ticker}): {e}")
        return None

# DB 연결 및 테이블 생성(컬럼 타입: INTEGER)
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.execute("""
    CREATE TABLE IF NOT EXISTS operating_income_1q (
        stock_code TEXT PRIMARY KEY,
        thstrm_amount INTEGER
    )
""")
conn.commit()

results = []
for idx, stock_code in enumerate(tickers, 1):
    try:
        thstrm_amount = get_op_profit_by_xpath(stock_code, XPATH)
        cur.execute(
            "INSERT OR REPLACE INTO operating_income_1q (stock_code, thstrm_amount) VALUES (?, ?)",
            (stock_code, thstrm_amount)
        )
        results.append({"stock_code": stock_code, "thstrm_amount": thstrm_amount})

        print(f"[{idx:4d}/{len(tickers)}] {stock_code} -> {thstrm_amount if thstrm_amount is not None else 'None'}")

        if idx % 50 == 0:
            conn.commit()
            print(f"{idx}개 저장 및 커밋 완료")
        # time.sleep(0.1)  # 완전 제거 (속도 우선, 트래픽 제한 유의)
    except Exception as e:
        print(f"Error: {stock_code} - {e}")

conn.commit()
conn.close()

df = pd.DataFrame(results)
print("\n저장된 결과 상위 10개 미리보기:")
print(df.head(10))
