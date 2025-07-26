import pandas as pd
from pykrx import stock
from sqlalchemy import create_engine
from datetime import datetime

class TradingCalendar:
    """
    비즈니스 데이트(영업일) 리스트 생성
    """
    def __init__(self, start: str, end: str, fmt: str = '%Y%m%d'):
        self.start = pd.to_datetime(start, format=fmt)
        self.end = pd.to_datetime(end, format=fmt)

    def get_business_days(self) -> pd.DatetimeIndex:
        return pd.date_range(start=self.start, end=self.end, freq='B')

class MarketAPI:
    """
    pykrx를 사용한 OHLCV 데이터 조회
    """
    @staticmethod
    def fetch_ohlcv(date: pd.Timestamp) -> pd.DataFrame:
        ymd = date.strftime('%Y%m%d')
        raw = stock.get_market_ohlcv_by_ticker(date=ymd, market='ALL')
        raw.index.name = 'ticker'
        return raw.reset_index()

class Database:
    """
    SQLite DB 연결 및 OHLCV 테이블 관리
    """
    def __init__(self, url: str = 'sqlite:///ohlcv.db'):
        self.engine = create_engine(url)
        self._ensure_table()

    def _ensure_table(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
            conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")
            conn.exec_driver_sql("PRAGMA cache_size=10000;")
            # marketcap 컬럼 추가
            conn.exec_driver_sql(
                '''
                CREATE TABLE IF NOT EXISTS ohlcv (
                    date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    name TEXT,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume INTEGER,
                    amount REAL,
                    marketcap REAL,
                    PRIMARY KEY(date, ticker)
                )
                '''
            )
            # 기존 테이블에 marketcap 컬럼 없으면 추가
            try:
                conn.exec_driver_sql('ALTER TABLE ohlcv ADD COLUMN marketcap REAL')
            except Exception:
                pass

    def get_processed_dates(self) -> set[str]:
        df = pd.read_sql('SELECT DISTINCT date FROM ohlcv', self.engine, parse_dates=['date'])
        return set(df['date'].dt.strftime('%Y-%m-%d'))

    def save_ohlcv(self, df: pd.DataFrame):
        df.to_sql('ohlcv', self.engine, if_exists='append', index=False)

class OHLCVTransformer:
    """
    원본 DataFrame을 저장용 포맷으로 변환
    """
    @staticmethod
    def map_columns(df: pd.DataFrame, date=None) -> pd.DataFrame:
        df = df.rename(columns={
            '시가': 'open', '고가': 'high', '저가': 'low',
            '종가': 'close', '거래량': 'volume', '거래대금': 'amount',
            '상장주식수': 'shares', '시가총액': 'marketcap'
        })
        if '종목명' in df.columns:
            df = df.rename(columns={'종목명': 'name'})
        else:
            from pykrx import stock as _stock
            df['name'] = df['ticker'].map(lambda t: _stock.get_market_ticker_name(t))

        if 'marketcap' not in df.columns:
            df['marketcap'] = None

        return df[['ticker', 'name', 'open', 'high', 'low', 'close', 'volume', 'amount', 'marketcap']]

    @staticmethod
    def filter_zero_open(df: pd.DataFrame) -> pd.DataFrame:
        return df[df['open'] != 0]





class OHLCVPipeline:
    """
    전체 파이프라인 실행: 데이터 조회, 변환, 계산, 저장
    """
    def __init__(self, start: str, end: str):
        self.calendar = TradingCalendar(start, end)
        self.api = MarketAPI()
        self.db = Database()
        self.transformer = OHLCVTransformer()
        self.existing = self.db.get_processed_dates()

    def run(self):
        all_processed = []
        for date in self.calendar.get_business_days():
            date_str = date.strftime('%Y-%m-%d')
            if date_str in self.existing:
                print(f"{date_str} already processed.")
                continue

            print(f"Processing {date_str}...")
            raw = self.api.fetch_ohlcv(date)

            df = self.transformer.map_columns(raw, date=date_str)
            df = self.transformer.filter_zero_open(df)
            df.insert(0, 'date', date_str)

            if all_processed:
                history = pd.concat(all_processed[-4:], ignore_index=True)
                concat = pd.concat([history, df], ignore_index=True)
            else:
                concat = df.copy()


            concat['date'] = pd.to_datetime(concat['date']).dt.strftime('%Y-%m-%d')
            today_df = concat[concat['date'] == date_str].copy()
            self.db.save_ohlcv(today_df)
            all_processed.append(today_df)

        print("Pipeline complete.")

if __name__ == '__main__':
    pipeline = OHLCVPipeline('20240701', '20250711')
    pipeline.run()
