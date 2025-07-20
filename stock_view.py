import os
import sys
import subprocess
import sqlite3
import bisect
from datetime import datetime, date, timedelta

import streamlit as st
import pandas as pd


class Config:
    """
    Application configuration paths.
    """
    def __init__(self):
        base_dir = os.path.abspath(os.path.dirname(__file__))
        self.DB_FILE = os.path.join(base_dir, "market_ohlcv.db")
        self.DATA_SCRIPT = os.path.join(base_dir, "stock_data.py")


class DatabaseUpdater:
    """
    Ensures the SQLite DB is up-to-date by running the data script once.
    """
    def __init__(self, config: Config):
        self.db_file = config.DB_FILE
        self.data_script = config.DATA_SCRIPT

    def update(self) -> date:
        if "db_updated" not in st.session_state:
            with st.spinner("앱 시작: DB 업데이트 중입니다..."):
                subprocess.run([sys.executable, self.data_script], check=True)

            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(date) FROM market_ohlcv")
            latest_str = cursor.fetchone()[0]
            conn.close()

            latest = datetime.strptime(latest_str, "%Y%m%d").date()
            st.success(f"DB 업데이트 완료: 최신 DB 날짜: {latest}")
            st.session_state["db_updated"] = True
            return latest
        return None


class DataManager:
    """
    Loads OHLCV data from the SQLite database with caching.
    """
    def __init__(self, db_path: str):
        self.db_path = db_path

    @st.cache_data(ttl=3600)
    def load_data(_self) -> pd.DataFrame:
        # 'self' renamed to '_self' so Streamlit caching ignores this parameter
        conn = sqlite3.connect(_self.db_path)
        df = pd.read_sql_query(
            """
            SELECT date, ticker, name AS 종목명, open, high, low, close, volume, value, change_rate
            FROM market_ohlcv
            """,
            conn,
            parse_dates=["date"]
        )
        conn.close()
        df['date_only'] = df['date'].dt.date
        return df

    def get_trading_days(self, df: pd.DataFrame) -> list:
        # No caching needed for this quick computation
        return sorted(df['date_only'].unique())


class CalendarManager:
    """
    Handles trading day lookup logic.
    """
    @staticmethod
    def prev_trading_day(trading_days: list, target: date) -> date:
        idx = bisect.bisect_left(trading_days, target)
        if idx < len(trading_days) and trading_days[idx] == target:
            return target
        return trading_days[idx-1] if idx > 0 else trading_days[0]


class FilterCondition:
    """
    Represents a single filter condition (name, label, logic).
    """
    def __init__(self, name: str, label: str, logic: str):
        self.name = name
        self.label = label
        self.logic = logic


class SidebarManager:
    """
    Builds and reads sidebar inputs.
    """
    def __init__(self, config: Config, trading_days: list):
        self.config = config
        self.trading_days = trading_days

    def render(self):
        st.sidebar.title("필터 설정")
        # DB 경로 (미사용 시에도 보여줌)
        _ = st.sidebar.text_input("SQLite DB 경로", value=self.config.DB_FILE)

        default_end = max(self.trading_days)
        default_start = default_end - timedelta(days=200)
        cols = st.sidebar.columns(2)
        start_date = cols[0].date_input("조회기간 - 부터", value=default_start)
        end_date = cols[1].date_input("까지", value=default_end)

        conditions, key_map = [], {}
        # D-필터 0~2
        for i in [0, 1, 2]:
            use = st.sidebar.checkbox(f"D-{i} 일봉", key=f"day{i}_use")
            if use:
                dir_col, logic_col = st.sidebar.columns([3,1])
                direction = dir_col.radio("", ["양봉", "음봉"], key=f"day{i}_dir", horizontal=True)
                logic = logic_col.radio("", ["AND", "OR"], key=f"day{i}_logic", horizontal=True)
                typ = 'pos' if direction == '양봉' else 'neg'
                name = f"{typ}{i}"
                label = f"D-{i} {'양봉' if typ=='pos' else '음봉'}"
                conditions.append(FilterCondition(name, label, logic))
                key_map[name] = label
            st.sidebar.markdown("---")

        # 거래대금
        if st.sidebar.checkbox("기간 중 거래대금 ≥500억", key="value_chk"):
            logic = st.sidebar.radio("", ["AND","OR"], key="value_logic", label_visibility="collapsed")
            conditions.append(FilterCondition("value_cond", "거래대금 ≥500억", logic))
            key_map["value_cond"] = "거래대금 ≥500억"
        st.sidebar.markdown("---")

        # 종가 상승
        if st.sidebar.checkbox("종가 상승 3배 미만", key="price_chk"):
            logic = st.sidebar.radio("", ["AND","OR"], key="price_logic", label_visibility="collapsed")
            conditions.append(FilterCondition("price_cond", "종가 상승 <3배", logic))
            key_map["price_cond"] = "종가 상승 <3배"
        st.sidebar.markdown("---")

        exclude_spc = st.sidebar.checkbox("스팩/우선주 제외/종가1000원 이상", key="ex_spc")
        key_map["spc"] = "스팩/우선주 제외/종가1000원 이상"
        st.sidebar.markdown("---")

        run = st.sidebar.button("종목추천")
        return start_date, end_date, conditions, key_map, exclude_spc, run


class MetricsManager:
    """
    Computes and displays per-condition counts.
    """
    def __init__(self, conditions: list, latest: dict, df_period: pd.DataFrame):
        self.conditions = conditions
        self.latest = latest
        self.df_period = df_period

    def show(self):
        if not self.conditions:
            return
        st.write("### 조건별 결과 개수")
        cols = st.columns(len(self.conditions))
        for idx, cond in enumerate(self.conditions):
            cnt = self._count(cond)
            cols[idx].metric(label=f"[{cond.label}]", value=f"{cnt}개")

    def _count(self, cond: FilterCondition) -> int:
        if cond.name.startswith(('pos','neg')):
            df_day = self.latest[cond.name[-1]]
            if cond.name.startswith('pos'):
                return df_day[df_day['change_rate'] > 0]['ticker'].nunique()
            return df_day[df_day['change_rate'] < 0]['ticker'].nunique()
        if cond.name == 'value_cond':
            return self.df_period.groupby('ticker')['value'].max().ge(5e10).sum()
        if cond.name == 'price_cond':
            min_close = self.df_period.groupby('ticker')['close'].min()
            latest_c = self.latest['0'].set_index('ticker')['close']
            return (latest_c / min_close).lt(3).sum()
        return 0


class RecommendationEngine:
    """
    Applies filter logic to derive the final set of tickers.
    """
    def __init__(self, conditions, df_period, latest, exclude_spc: bool):
        self.conditions = conditions
        self.df_period = df_period
        self.latest = latest
        self.exclude_spc = exclude_spc

    def run(self) -> set:
        result = None
        for cond in self.conditions:
            s = self._tickers_for(cond)
            result = s if result is None else (result & s if cond.logic=='AND' else result | s)
        if result is None:
            result = set(self.latest['0']['ticker'])
        if self.exclude_spc:
            result = self._exclude_spc(result)
        return result

    def _tickers_for(self, cond: FilterCondition) -> set:
        if cond.name.startswith(('pos','neg')):
            df_day = self.latest[cond.name[-1]]
            mask = df_day['change_rate'] > 0 if cond.name.startswith('pos') else df_day['change_rate'] < 0
            return set(df_day[mask]['ticker'])
        if cond.name == 'value_cond':
            return set(self.df_period.groupby('ticker')['value'].max().loc[lambda x: x>=5e10].index)
        if cond.name == 'price_cond':
            min_close = self.df_period.groupby('ticker')['close'].min()
            latest_c = self.latest['0'].set_index('ticker')['close']
            return set((latest_c / min_close).loc[lambda x: x<3].index)
        return set()

    def _exclude_spc(self, tickers: set) -> set:
        df0 = self.latest['0'].set_index('ticker')
        return {t for t in tickers if not (('스팩' in df0.loc[t,'종목명']) or (not t.endswith('0')) or (df0.loc[t,'close']<1000))}


class UIManager:
    """
    Handles all Streamlit UI rendering.
    """
    @staticmethod
    def show_title():
        st.title("필터 조건 기반 종목 추천")

    @staticmethod
    def show_conditions(conditions, key_map, exclude_spc):
        st.write("### 현재 필터 조건")
        expr = [f"[{c.label}]{c.logic}" for c in conditions]
        if exclude_spc:
            expr.append(f"[{key_map['spc']}]")
        st.info(" ".join(expr) if expr else "조건 없음")

    @staticmethod
    def show_results(tickers: set, latest: dict, end_date: date):
        df0 = latest['0']
        df_res = df0[df0['ticker'].isin(tickers)].copy()
        df_res = df_res.sort_values('value', ascending=False)
        df_res.index = range(1, len(df_res)+1)
        st.subheader(f"추천 종목 ({len(df_res)}개)에 대한 {end_date} 데이터")
        if not df_res.empty:
            df_res = df_res.rename(columns={
                'ticker':'종목코드','종목명':'종목명','open':'시가','high':'고가',
                'low':'저가','close':'종가','volume':'거래량','value':'거래대금','change_rate':'등락률'
            })
            st.dataframe(df_res.set_index('종목코드')[['종목명','시가','고가','저가','종가','거래량','거래대금','등락률']], use_container_width=True)
        else:
            st.info("조건에 맞는 종목이 없습니다.")


class StockRecommenderApp:
    """ Orchestrates all components. """
    def __init__(self):
        self.config = Config()
        self.updater = DatabaseUpdater(self.config)
        self.data_manager = DataManager(self.config.DB_FILE)
        self.calendar = CalendarManager()

    def run(self):
        st.set_page_config(layout="wide")
        UIManager.show_title()

        # 1) DB 업데이트
        self.updater.update()

        # 2) 데이터 로딩 및 날짜 리스트
        df_all = self.data_manager.load_data()
        trading_days = self.data_manager.get_trading_days(df_all)

        # 3) 사이드바 입력
        sidebar = SidebarManager(self.config, trading_days)
        start_date, end_date, conditions, key_map, exclude_spc, run = sidebar.render()

        # 4) 실행시 처리
        if run:
            df_period = df_all[(df_all['date_only']>=start_date) & (df_all['date_only']<=end_date)]
            if df_period.empty:
                st.warning("선택 기간에 데이터가 없습니다.")
                return

            latest = {
                str(i): df_all[df_all['date_only']==
                    self.calendar.prev_trading_day(trading_days, end_date - timedelta(days=i))]
                for i in [0,1,2]
            }

            # 5) 메트릭
            MetricsManager(conditions, latest, df_period).show()
            # 6) 조건 요약
            UIManager.show_conditions(conditions, key_map, exclude_spc)

            # 7) 추천
            tickers = RecommendationEngine(conditions, df_period, latest, exclude_spc).run()
            # 8) 결과 출력
            UIManager.show_results(tickers, latest, end_date)
        else:
            st.sidebar.write("필터를 설정한 뒤 ‘종목추천’ 버튼을 눌러주세요.")


if __name__ == "__main__":
    StockRecommenderApp().run()
