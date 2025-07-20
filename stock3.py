import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime, timedelta
from typing import Optional, List

pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)

# ————————————————
# 1) DB 연결 및 데이터 로딩
# ————————————————
class StockDB:
    def __init__(self, db_url: str = 'sqlite:///ohlcv.db'):
        self.engine = create_engine(db_url)

    def load_ohlcv(self, start_date: str, end_date: str) -> pd.DataFrame:
        """
        start_date~end_date 구간의 OHLCV 데이터를 DataFrame으로 반환.
        """
        sql = text("""
            SELECT date, ticker, name, open, high, low, close, amount, marketcap
            FROM ohlcv
            WHERE date BETWEEN :start AND :end
        """)
        df = pd.read_sql(sql, self.engine, params={'start': start_date, 'end': end_date})
        df['date'] = pd.to_datetime(df['date'])
        # 숫자형 컬럼 한 번에 변환
        num_cols = ['open', 'high', 'low', 'close', 'amount', 'marketcap']
        df[num_cols] = df[num_cols].apply(pd.to_numeric, errors='coerce')
        return df

# ————————————————
# 2) 필터 기준 상수
# ————————————————
class Thresholds:
    MIN_AMOUNT   = 15_000_000_000   # 거래대금
    MIN_MARKETCAP= 100_000_000_000  # 시가총액
    MIN_CLOSE    = 1_000            # 종가

# ————————————————
# 3) 쿼리 빌더
# ————————————————
class StockQuery:
    def __init__(self, db: StockDB, base_date: str, window_days: int = 200):
        self.db = db
        self.base_date = datetime.strptime(base_date, '%Y-%m-%d')
        self.window_days = window_days
        self.df = self._load_window()

    def _load_window(self) -> pd.DataFrame:
        """기준일로부터 1.5 * window_days 만큼 백데이터 로드 후 마지막 window_days 영업일만 추출"""
        start_dt = self.base_date - timedelta(days=int(self.window_days * 1.5))
        df = self.db.load_ohlcv(start_dt.strftime('%Y-%m-%d'), self.base_date.strftime('%Y-%m-%d'))
        unique_days = sorted(df['date'].unique())[-self.window_days:]
        return df[df['date'].isin(unique_days)].sort_values(['ticker', 'date'])

    # —————— 필터 메서드들 ——————
    def exclude_special(self) -> 'StockQuery':
        """스팩·우선주 제외"""
        df = self.df[~self.df['name'].str.contains('스팩', na=False)]
        df = df[df['ticker'].str.endswith('0')]
        self.df = df
        return self

    def filter_amount(self, amount: int = Thresholds.MIN_AMOUNT) -> 'StockQuery':
        self.df = self.df[self.df['amount'] >= amount]
        return self

    def filter_marketcaps(self, marketcap: int = Thresholds.MIN_MARKETCAP) -> 'StockQuery':
        self.df = self.df[self.df['marketcap'] >= marketcap]
        return self

    def filter_close_price(self, min_close: int = Thresholds.MIN_CLOSE) -> 'StockQuery':
        self.df = self.df[self.df['close'] >= min_close]
        return self

    def filter_prev_bearish_today_bullish(self) -> 'StockQuery':
        """전일 음봉 & 당일 양봉 종목만 남김 (벡터화 버전)"""
        # 1) 가장 최근 2영업일만 뽑기
        df_sorted = self.df.sort_values(['ticker', 'date'])
        # 그룹별로 마지막 두 행(전일·당일)만 남김
        last2 = (
            df_sorted
            .groupby('ticker')
            .tail(2)
            .reset_index(drop=True)
        )
        # 2) 전일·당일 구분용 마킹
        grouped = last2.groupby('ticker')
        # 종목별로 2행이 모두 있어야 비교 가능
        valid_tickers = [t for t, g in grouped if len(g) == 2]
        cmp_df = last2[last2['ticker'].isin(valid_tickers)].copy()
        # 3) 전일(close<open), 당일(close>open) 필터링
        def is_valid(group):
            prev, today = group.iloc[0], group.iloc[1]
            return (prev['close'] < prev['open']) and (today['close'] > today['open'])
        good = (
            cmp_df
            .groupby('ticker')
            .filter(is_valid)
        )
        # 4) 최종적으로 당일 행만 남기기
        self.df = good[good['date'] == good.groupby('ticker')['date'].transform('max')]
        return self

    def exclude_large_runups(self, factor: float = 3.0) -> 'StockQuery':
        """
        200일 내 최저 종가 대비 상한(factor)배 이상 오른 종목 제외
        """
        min_close = self.df.groupby('ticker')['close'].min()
        def under_factor(row):
            return row['close'] / min_close[row['ticker']] < factor

        self.df = self.df[self.df.apply(under_factor, axis=1)]
        return self

    # —————— 실행 파이프라인 ——————
    def run_all_filters(self) -> pd.DataFrame:
        return (
            self.exclude_special()
                .filter_amount()
                .filter_marketcaps()
                .filter_close_price()
                .filter_prev_bearish_today_bullish()
                .exclude_large_runups()
                .df
        )

    def show(self) -> None:
        if self.df.empty:
            print("조건에 맞는 종목이 없습니다.")
        else:
            out = self.df[['date','ticker','name','close','amount','marketcap']] \
                     .sort_values(['date','amount'], ascending=[True,False])
            print(out)
            print(f"\n총 {len(out)}개")

# ————————————————
# 4) 사용 예
# ————————————————
if __name__ == '__main__':
    db    = StockDB()
    query = StockQuery(db, base_date='2025-07-11', window_days=200)
    result= query.run_all_filters()
    query.show()
