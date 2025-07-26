
import sqlite3
from pykrx import stock

# DB 연결
conn = sqlite3.connect("market_ohlcv.db")
cursor = conn.cursor()

# 삭제 실행
cursor.execute("DELETE FROM market_ohlcv WHERE date = ?", ("20250725",))
conn.commit()

# 최적화(선택)
cursor.execute("VACUUM;")
conn.commit()

conn.close()

