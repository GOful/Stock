import pandas as pd
from sqlalchemy import create_engine
from datetime import datetime, timedelta

# DataFrame 출력 옵션
pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', None)
pd.set_option('display.max_colwidth', None)
pd.set_option('display.float_format', '{:,.0f}'.format)

class StockDB:
    def __init__(self, db_url='sqlite:///ohlcv.db'):
        self.engine = create_engine(db_url)

    def load(self, start_date, end_date):
        query = f"""
        SELECT date, ticker, name, open, high, low, close, amount, marketcap
        FROM ohlcv
        WHERE date BETWEEN '{start_date}' AND '{end_date}'
        """
        df = pd.read_sql(query, self.engine)
        df['date'] = pd.to_datetime(df['date'])
        return df

class StockQuery:
    def __init__(self, db: StockDB, base_date: str, window_days: int = 200):
        self.db = db
        self.base_date = base_date
        self.window_days = window_days
        self.df = self._load_window()

    def _load_window(self):
        base_dt = datetime.strptime(self.base_date, '%Y-%m-%d')
        start_dt = base_dt - timedelta(days=int(self.window_days * 1.5))
        start = start_dt.strftime('%Y-%m-%d')
        end = self.base_date
        df = self.db.load(start, end)
        df = df.sort_values(['ticker', 'date'])
        biz_days = sorted(df['date'].unique())[-self.window_days:]
        return df[df['date'].isin(biz_days)].reset_index(drop=True)

    def exclude_over_3x(self, result_df, base_df=None):
        if result_df.empty:
            return result_df
        if base_df is None:
            base_df = self.df
        min_close = base_df.groupby('ticker')['close'].min().rename('min_close')
        merged = result_df.merge(min_close, on='ticker', how='left')
        filtered = merged[merged['close'] / merged['min_close'] < 3].copy()
        return filtered.drop(columns=['min_close'])

    def show(self, df):
        if df.empty:
            print('조건에 맞는 종목이 없습니다.')
        else:
            out = df[['date', 'ticker', 'name', 'close', 'amount', 'marketcap']] \
                     .sort_values(['date', 'amount'], ascending=[True, False])
            print(out)
            print(f"\n[최종 추천 종목: 총 {len(out):,}개, 종목 수: {out['ticker'].nunique():,}개]")

if __name__ == '__main__':
    base_date = '2025-07-11'
    db = StockDB()
    query = StockQuery(db, base_date, window_days=200)

    # 1) 200일 전체 이력 데이터
    hist_df = query.df
    print(f"1) 전체 이력 데이터: {len(hist_df):,}개, 종목 수: {hist_df['ticker'].nunique():,}개")

    # 2) 우선주 제외 (티커 끝자리 '0'만 포함)
    step2 = hist_df[hist_df['ticker'].str.endswith('0')]
    print(f"2) 우선주 제외 후: {len(step2):,}개, 종목 수: {step2['ticker'].nunique():,}개 (제외: {len(hist_df) - len(step2):,}개)")

    # 3) 스팩주 제외 (name에 '스팩' 포함 시 제외)
    step3 = step2[~step2['name'].str.contains('스팩', na=False)]
    print(f"3) 스팩주 제외 후: {len(step3):,}개, 종목 수: {step3['ticker'].nunique():,}개 (제외: {len(step2) - len(step3):,}개)")

    # 4) 시가총액 >=1000억 (step3 기준)
    step4 = step3[pd.to_numeric(step3['marketcap'], errors='coerce') >= 100_000_000_000]
    print(f"4) 시가총액 >=1000억 후: {len(step4):,}개, 종목 수: {step4['ticker'].nunique():,}개 (제외: {len(step3) - len(step4):,}개)")

    # 5) 거래대금 >=150억 (step4 기준)
    step5 = step4[pd.to_numeric(step4['amount'], errors='coerce') >= 15_000_000_000]
    print(f"5) 거래대금 >=150억 후: {len(step5):,}개, 종목 수: {step5['ticker'].nunique():,}개 (제외: {len(step4) - len(step5):,}개)")

    # 6) 조회날짜({base_date}) 종가 >=1000원 (step5 기준)
    base_dt = datetime.strptime(base_date, '%Y-%m-%d')
    step6 = step5[(step5['date'] == base_dt) & (step5['close'] >= 1_000)].copy()
    print(f"6) 조회날짜({base_date}) 종가 >=1000원 후: {len(step6):,}개, 종목 수: {step6['ticker'].nunique():,}개 (제외: {len(step5) - len(step6):,}개)")

    # 7) 전일 음봉 & 당일 양봉 (step5 기준, step6 종목에 한해 판단)
    dates = sorted(step5['date'].unique())
    prev_date = dates[-2] if len(dates) >= 2 else None
    if prev_date:
        df_prev = step5[step5['date'] == prev_date]
        df_today = step5[step5['date'] == base_dt]
        tickers_prev_bearish = set(df_prev[df_prev['ticker'].isin(step6['ticker']) & (df_prev['close'] < df_prev['open'])]['ticker'])
        tickers_today_bullish = set(df_today[df_today['ticker'].isin(step6['ticker']) & (df_today['close'] > df_today['open'])]['ticker'])
        final_tickers = tickers_prev_bearish & tickers_today_bullish
        step7 = df_today[df_today['ticker'].isin(final_tickers)].copy()
    else:
        step7 = pd.DataFrame(columns=step6.columns)
    print(f"7) 전일 음봉 & 당일 양봉 후: {len(step7):,}개, 종목 수: {step7['ticker'].nunique():,}개 (제외: {len(step6) - len(step7):,}개)")

    # 최종 결과 출력
    query.show(step7)
