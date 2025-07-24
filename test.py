#"""
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
#"""

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