import pandas as pd
from sqlalchemy import create_engine
from datetime import datetime, timedelta

# DataFrame 출력 시 생략 없이 모두 보이게 설정
pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', None)
pd.set_option('display.max_colwidth', None)
pd.set_option('display.float_format', '{:,.0f}'.format)

class StockDB:
    def __init__(self, db_url='sqlite:///ohlcv.db'):
        self.engine = create_engine(db_url)

    def load(self, start_date, end_date):
        df = pd.read_sql(f"SELECT * FROM ohlcv WHERE date >= '{start_date}' AND date <= '{end_date}'", self.engine)
        df['date'] = pd.to_datetime(df['date'])
        return df

class StockQuery:
    def filter_by_strict_conditions(self, df=None):
        if df is None:
            df = self.df.copy()
        for col in ['open','close', 'amount', 'marketcap']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df[df['amount'] >= 15_000_000_000]
        df = df[df['marketcap'] >= 100_000_000_000]
        df = df[df['close'] >= 1_000]
        df = df[~df['name'].str.contains('스팩', na=False)]
        df = df[df['ticker'].astype(str).str[-1] == '0']
        return df

    def filter_prev_day_bearish_tickers(self, df=None):
        if df is None:
            df = self.df.copy()
        last_days = sorted(df['date'].unique())
        if len(last_days) < 2:
            return set()
        prev_day = last_days[-2]
        prev_df = df[df['date'] == prev_day]
        bearish_tickers = set(prev_df[prev_df['close'] < prev_df['open']]['ticker'])
        return bearish_tickers

    def filter_today_bullish_df(self, df=None):
        if df is None:
            df = self.df.copy()
        last_days = sorted(df['date'].unique())
        if not last_days:
            return df.iloc[0:0]
        today = last_days[-1]
        today_df = df[df['date'] == today]
        bullish_df = today_df[today_df['close'] > today_df['open']]
        return bullish_df.copy()

    def exclude_over_3x(self, result_df, base_df=None):
        if result_df.empty:
            return result_df
        if base_df is None:
            base_df = self.df
        min_close_map = base_df.groupby('ticker')['close'].min().to_dict()
        def is_under_3x(row):
            min_close = min_close_map.get(row['ticker'], None)
            try:
                return float(row['close']) / float(min_close) < 3
            except Exception:
                return False
        mask = result_df.apply(is_under_3x, axis=1)
        return result_df[mask]
    def __init__(self, db: StockDB, base_date: str, window_days: int = 200):
        self.base_date = base_date
        self.window_days = window_days
        self.db = db
        self.df = self._load_window()

    def filter_today_bullish(self):
        # 기준일(마지막 영업일) 양봉(종가 > 시가) 종목
        df = self.df.copy()
        last_days = sorted(df['date'].unique())
        if not last_days:
            return df.iloc[0:0]
        today = last_days[-1]
        today_df = df[df['date'] == today]
        bullish_df = today_df[today_df['close'] > today_df['open']]
        return bullish_df.copy()

    def filter_prev_day_bearish(self):
        # 기준일의 전일(마지막 영업일 -1) 음봉(종가 < 시가) 종목
        df = self.df.copy()
        # 기준일 직전 날짜 구하기
        last_days = sorted(df['date'].unique())
        if len(last_days) < 2:
            return df.iloc[0:0]  # 빈 DataFrame 반환
        prev_day = last_days[-2]
        prev_df = df[df['date'] == prev_day]
        bearish_df = prev_df[prev_df['close'] < prev_df['open']]
        return bearish_df.copy()

    def _load_window(self):
        base_dt = datetime.strptime(self.base_date, '%Y-%m-%d')
        start_dt = base_dt - timedelta(days=self.window_days*1.5)
        start_date = start_dt.strftime('%Y-%m-%d')
        df = self.db.load(start_date, self.base_date)
        df = df.sort_values(['ticker', 'date'])
        # 실제 200영업일만 추출
        biz_days = sorted(df['date'].unique())[-self.window_days:]
        return df[df['date'].isin(biz_days)]

    def filter_by_amount(self, min_amount):
        return self.df[self.df['amount'] >= min_amount].copy()

    # 추가 조건 메서드 예시
    def filter_by_close(self, min_close=None, max_close=None):
        df = self.df
        if min_close is not None:
            df = df[df['close'] >= min_close]
        if max_close is not None:
            df = df[df['close'] <= max_close]
        return df.copy()

    def show(self, df):
        if df.empty:
            print('조건에 맞는 종목이 없습니다.')
        else:
            out = df[['date', 'ticker', 'name', 'open','close', 'amount', 'marketcap']].sort_values(['date', 'amount'], ascending=[True, False])
            print(out)
            print(f'\n총 {len(out)}개')

if __name__ == '__main__':
    base_date = '2025-07-11'  # 기준일
    db = StockDB()
    query = StockQuery(db, base_date, window_days=200)

    # 1~4. 모든 기본 조건 필터 적용
    filtered_df = query.filter_by_strict_conditions()
    # 5. 전일 음봉 티커
    bearish_tickers = query.filter_prev_day_bearish_tickers(filtered_df)
    # 6. 당일 양봉 종목
    bullish_df = query.filter_today_bullish_df(filtered_df)
    # 5+6. 전일 음봉 & 당일 양봉 모두 만족하는 종목
    final_tickers = set(bullish_df['ticker']) & bearish_tickers
    result = bullish_df[bullish_df['ticker'].isin(final_tickers)]
    # 7. 3배 이상 상승 제외
    result = query.exclude_over_3x(result, filtered_df)

    if result.empty:
        print('조건에 맞는 종목이 없습니다.')
    else:
        out = result[['date', 'ticker', 'name', 'close', 'amount', 'marketcap']].sort_values(['date', 'amount'], ascending=[True, False])
        print(out)
        print('\n[조건: 스팩/우선주 제외, 200일 내 거래대금 150억, 시가총액 1000억, 종가 1000원, 전일 음봉, 당일 양봉, 200일 내 최저점 대비 3배 미만]')
        print(f'\n총 {len(out)}개')