import sqlite3
import bisect
import logging
import warnings
from datetime import datetime, date, timedelta
from pathlib import Path
from dataclasses import dataclass

import pandas as pd
import streamlit as st
from browser_detection import browser_detection_engine

# ──────────────────────────────
# 앱 초기 설정: 경고 & 로깅 & 브라우저 정보
# ──────────────────────────────
class AppInitializer:
    @staticmethod
    def setup():
        logging.getLogger("streamlit.elements.lib.policies").setLevel(logging.ERROR)
        warnings.filterwarnings("ignore", category=FutureWarning)
        logging.basicConfig(
            format="%(asctime)s  접속 기록  %(message)s",
            level=logging.INFO,
            datefmt="%Y-%m-%d %H:%M:%S"
        )

    @staticmethod
    def get_user_agent():
        ua_info = browser_detection_engine()
        return ua_info.get("userAgent", "Unknown") if ua_info else "Unknown"


# ──────────────────────────────
# 설정 클래스: 경로 정보
# ──────────────────────────────
class Config:
    def __init__(self):
        base_dir = Path(__file__).parent
        self.DB_FILE = str(base_dir / "market_ohlcv.db")


# ──────────────────────────────
# 필터 조건 표현 클래스
# ──────────────────────────────
@dataclass
class FilterCondition:
    name: str
    label: str
    logic: str


# ──────────────────────────────
# 날짜 유틸
# ──────────────────────────────
class DateUtils:
    @staticmethod
    def default_range(trading_days, delta=200):
        end = max(trading_days)
        start = end - timedelta(days=delta)
        return start, end


# ──────────────────────────────
# 거래일 관련 처리
# ──────────────────────────────
class CalendarManager:
    @staticmethod
    def prev_trading_day(days, target):
        idx = bisect.bisect_left(days, target)
        return target if idx < len(days) and days[idx] == target else days[max(idx - 1, 0)]


# ──────────────────────────────
# 데이터 로딩 클래스
# ──────────────────────────────
class DataManager:
    def __init__(self, db_path):
        self.db_path = db_path

    def load_data(self):
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query("""
                SELECT date, ticker, name AS 종목명, open, high, low, close,
                       volume, value, change_rate, market_cap
                FROM market_ohlcv
            """, conn, parse_dates=["date"])
        df['date_only'] = df['date'].dt.date
        return df

    def get_trading_days(self, df):
        return sorted(df['date_only'].unique())


# ──────────────────────────────
# 사이드바 UI 구성
# ──────────────────────────────
class SidebarManager:
    def __init__(self, config, trading_days):
        self.config = config
        self.trading_days = trading_days

    def render(self):
        with st.sidebar.form("filter_form"):
            st.title("필터 설정")
            st.text_input("SQLite DB 경로", value=self.config.DB_FILE)

            start_date, end_date = DateUtils.default_range(self.trading_days)
            c1, c2 = st.columns(2)
            start = c1.date_input("조회기간 - 부터", start_date)
            end = c2.date_input("까지", end_date)
            st.markdown("---")

            conditions, key_map = [], {}
            for i in [0, 1, 2]:
                cbox = st.checkbox(f"D-{i} 일봉", key=f"day{i}_use")
                dir_col, lg_col = st.columns([3, 1])
                direction = dir_col.selectbox("", ["", "양봉", "음봉"],
                                              key=f"day{i}_dir", label_visibility="collapsed")
                logic = lg_col.radio("", ["AND", "OR"], key=f"day{i}_logic",
                                      horizontal=True, label_visibility="collapsed")
                if cbox and direction:
                    typ = 'pos' if direction == "양봉" else 'neg'
                    name = f"{typ}{i}"
                    label = f"D-{i} {direction}"
                    conditions.append(FilterCondition(name, label, logic))
                    key_map[name] = label
                st.markdown("---")

            if st.checkbox("우량주필터", key="junk_chk", help="거래대금≥500억, 종가<3배, 스팩·우선주 제외&종가≥1,000원을 모두 적용"):
                conditions.append(FilterCondition("junk", "우량주필터", "AND"))
                key_map["junk"] = "우량주필터"

            st.markdown("---")
            run = st.form_submit_button("종목추천")

        return start, end, conditions, key_map, run


# ──────────────────────────────
# 필터 조건 추천 로직
# ──────────────────────────────
class RecommendationEngine:
    def __init__(self, conditions, df_period, latest):
        self.conditions = conditions
        self.df_period = df_period
        self.latest = latest

    def run(self):
        result = None
        for cond in self.conditions:
            tickers = self._tickers_for(cond)
            result = tickers if result is None else (
                result & tickers if cond.logic == "AND" else result | tickers
            )
        return result or set(self.latest['0']['ticker'])

    def _tickers_for(self, cond):
        if cond.name.startswith(("pos", "neg")):
            df = self.latest[cond.name[-1]]
            mask = df['change_rate'] > 0 if cond.name.startswith("pos") else df['change_rate'] < 0
            return set(df[mask]['ticker'])

        if cond.name == "junk":
            s1 = set(self.df_period.groupby('ticker')['value'].max().loc[lambda x: x >= 5e10].index)
            min_close = self.df_period.groupby('ticker')['close'].min()
            latest_c = self.latest['0'].set_index('ticker')['close']
            s2 = set((latest_c / min_close).loc[lambda x: x < 3].index)
            df0 = self.latest['0'].set_index('ticker')
            s3 = {
                t for t in latest_c.index
                if '스팩' not in df0.loc[t, '종목명'] and t.endswith('0') and df0.loc[t, 'close'] >= 1000
            }
            return s1 & s2 & s3

        return set()


# ──────────────────────────────
# 메인 앱 클래스
# ──────────────────────────────
class StockRecommenderApp:
    def __init__(self, user_agent):
        self.user_agent = user_agent
        self.config = Config()
        self.data_manager = DataManager(self.config.DB_FILE)
        self.calendar = CalendarManager()

    def run(self):
        logging.info(f"{self.user_agent} 접속")
        st.set_page_config(layout="wide")
        st.title("필터 조건 기반 종목 추천")

        df_all = self.data_manager.load_data()
        trading_days = self.data_manager.get_trading_days(df_all)

        sidebar = SidebarManager(self.config, trading_days)
        start_date, end_date, conditions, key_map, run = sidebar.render()

        if run:
            df_period = df_all[(df_all['date_only'] >= start_date) & (df_all['date_only'] <= end_date)]
            if df_period.empty:
                st.warning("선택 기간에 데이터가 없습니다.")
                return

            latest = {
                str(i): df_all[df_all['date_only'] == self.calendar.prev_trading_day(trading_days, end_date - timedelta(days=i))]
                for i in [0, 1, 2]
            }

            tickers = RecommendationEngine(conditions, df_period, latest).run()
            df_result = df_all[df_all['ticker'].isin(tickers) & (df_all['date_only'] == latest['0']['date_only'].iloc[0])]
            df_result = df_result.sort_values("market_cap", ascending=False).reset_index(drop=True)
            df_result.index += 1

            if df_result.empty:
                st.info("조건에 맞는 종목이 없습니다.")
                return

            df_result['시가총액'] = df_result['market_cap'].apply(self._format_unit)
            df_result['거래대금'] = df_result['value'].apply(self._format_unit)
            df_result['거래량'] = df_result['volume'].apply(self._format_unit)
            df_result['차트'] = df_result['ticker'].apply(lambda x: f"https://finance.naver.com/item/fchart.naver?code={x}")

            st.subheader(f"추천 종목 ({len(df_result)}개) — {end_date}")
            st.data_editor(
                df_result[[
                    'ticker', '차트', '종목명', '시가총액',
                    '거래량', '거래대금', 'change_rate']]
                .rename(columns={
                    'ticker': '종목코드', 'change_rate': '등락률'
                }),
                column_config={
                    '차트': st.column_config.LinkColumn(label='차트', display_text='📈')
                },
                hide_index=True, height=400
            )

    def _format_unit(self, x):
        return (
            f"{x / 10 ** 12:.2f}조" if x >= 1e12 else
            f"{x / 10 ** 8:.1f}억" if x >= 1e8 else
            f"{x / 10 ** 4:.0f}만" if x >= 1e4 else
            f"{x:,}"
        )


# ──────────────────────────────
# 실행 시작점
# ──────────────────────────────
if __name__ == "__main__":
    AppInitializer.setup()
    user_agent = AppInitializer.get_user_agent()
    StockRecommenderApp(user_agent).run()
