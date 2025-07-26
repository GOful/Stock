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
stock_code = '035420'

fs = dart.finstate_all(stock_code, year, reprt_code)
print(fs)
