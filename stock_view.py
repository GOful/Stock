import sqlite3
import logging
import warnings
from datetime import timedelta, date, datetime
from pathlib import Path
from dataclasses import dataclass
from typing import List, Set, Dict, Tuple

import pandas as pd
import streamlit as st

# ──────────────
# 1. 설정 및 로거
# ──────────────
class AppConfig:
    DB_FILENAME = "market_ohlcv.db"

    @staticmethod
    def get_db_path() -> str:
        """앱 파일과 같은 폴더 내 db파일 경로"""
        try:
            base_dir = Path(__file__).parent
        except NameError:
            base_dir = Path.cwd()
        return str(base_dir / AppConfig.DB_FILENAME)


class LoggerSetup:
    @staticmethod
    def setup():
        # Streamlit 로깅 억제
        logging.getLogger("streamlit.elements.lib.policies").setLevel(logging.ERROR)
        warnings.filterwarnings("ignore", category=FutureWarning)
        logging.basicConfig(
            format="%(asctime)s  [접속] %(message)s",
            level=logging.INFO,
            datefmt="%Y-%m-%d %H:%M:%S"
        )

# ──────────────
# 2. DB 관련
# ──────────────
class DBManager:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def load_ohlcv(self) -> pd.DataFrame:
        """DB에서 OHLCV 데이터 전체 로딩 (파싱/에러처리)"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                df = pd.read_sql_query(
                    """
                    SELECT date, ticker, name AS stock_name, open, high, low, close,
                        volume, value, change_rate, market_cap, "thstrm_amount"
                    FROM market_ohlcv
                    """, conn, parse_dates=["date"]
                )
            df['date_only'] = df['date'].dt.date
            return df
        except Exception as e:
            st.error(f"DB 연결/로딩 실패: {e}")
            return pd.DataFrame()

    @staticmethod
    def get_trading_days(df: pd.DataFrame) -> List[date]:
        """모든 거래일만 반환 (오름차순)"""
        return sorted(df['date_only'].unique())

# ──────────────
# 3. 거래일 계산
# ──────────────
class TradingCalendar:
    @staticmethod
    def prev_trading_day(days: List[date], target: date) -> date:
        from bisect import bisect_left
        idx = bisect_left(days, target)
        return target if (idx < len(days) and days[idx] == target) else days[max(idx - 1, 0)]

    @staticmethod
    def get_recent_n(days: List[date], end_date: date, n: int = 3) -> List[date]:
        from bisect import bisect_right
        idx = bisect_right(days, end_date)
        return list(reversed(days[max(0, idx - n):idx]))  # 최신 → 과거

# ──────────────
# 4. 단위 변환 등 유틸
# ──────────────
class Utility:
    @staticmethod
    def format_unit(x) -> str:
        """숫자를 한국 단위로 변환 (음수도 지원)"""
        try:
            if pd.isna(x):  # NaN은 빈문자
                return ""
            sign = '-' if x < 0 else ''
            x_abs = abs(x)
            if x_abs >= 1e12:
                return f"{sign}{x_abs / 1e12:.2f}조"
            elif x_abs >= 1e8:
                return f"{sign}{x_abs / 1e8:.1f}억"
            elif x_abs >= 1e4:
                return f"{sign}{x_abs / 1e4:.0f}만"
            else:
                return f"{sign}{x_abs:,}"
        except Exception:
            return str(x)


    @staticmethod
    def default_date_range(trading_days: List[date], delta: int = 200) -> Tuple[date, date]:
        end = max(trading_days)
        start = end - timedelta(days=delta)
        return start, end

# ──────────────
# 5. 필터 조건 데이터
# ──────────────
@dataclass
class FilterCondition:
    name: str     # ex. pos0, neg2, junk
    label: str    # ex. D-0 양봉
    logic: str    # AND/OR

# ──────────────
# 6. 사이드바 UI
# ──────────────
class SidebarUI:
    def __init__(self, db_path: str, trading_days: List[date]):
        self.db_path = db_path
        self.trading_days = trading_days

    def render(self) -> Tuple[date, date, List[FilterCondition], Dict[str, str], bool]:
        """필터조건 등 입력 및 반환"""
        with st.sidebar.form("filter_form"):
            st.title("🔍 필터 설정")
            st.text_input("SQLite DB 경로", value=self.db_path, disabled=True)

            start_date, end_date = Utility.default_date_range(self.trading_days)
            c1, c2 = st.columns(2)
            start = c1.date_input("조회 시작일", start_date)
            end = c2.date_input("조회 종료일", end_date)
            st.markdown("---")

            conditions: List[FilterCondition] = []
            key_map: Dict[str, str] = {}

            for i in range(3):
                cbox = st.checkbox(f"D-{i} 일봉", key=f"day{i}_use")
                dir_col, lg_col = st.columns([3, 1])
                direction = dir_col.selectbox("", ["", "양봉", "음봉"], key=f"day{i}_dir", label_visibility="collapsed")
                logic = lg_col.radio("", ["AND", "OR"], key=f"day{i}_logic", horizontal=True, label_visibility="collapsed")
                if cbox and direction:
                    typ = 'pos' if direction == "양봉" else 'neg'
                    name = f"{typ}{i}"
                    label = f"D-{i} {direction}"
                    conditions.append(FilterCondition(name, label, logic))
                    key_map[name] = label
                st.markdown("---")

            if st.checkbox("우량주 필터", key="bluechip_chk", help="거래대금≥500억, 종가<3배, 스팩·우선주 제외, 종가≥1,000원 모두 적용"):
                conditions.append(FilterCondition("junk", "우량주 필터", "AND"))
                key_map["junk"] = "우량주 필터"
            st.markdown("---")

            run = st.form_submit_button("종목 추천")

        return start, end, conditions, key_map, run

# ──────────────
# 7. 종목 추천 로직
# ──────────────
class StockFilterEngine:
    def __init__(self, conditions: List[FilterCondition], df_period: pd.DataFrame, latest: Dict[str, pd.DataFrame]):
        self.conditions = conditions
        self.df_period = df_period
        self.latest = latest

    def recommend(self) -> Set[str]:
        """조건 조합대로 종목코드 set 반환 (없으면 최신 전체)"""
        result = None
        for cond in self.conditions:
            tickers = self._tickers_for(cond)
            if result is None:
                result = tickers
            else:
                if cond.logic == "AND":
                    result &= tickers
                else:
                    result |= tickers
        if result is None or len(result) == 0:
            return set(self.latest['0']['ticker'])  # 전체 반환
        return result

    def _tickers_for(self, cond: FilterCondition) -> Set[str]:
        """각 조건별 종목 반환"""
        if cond.name.startswith(("pos", "neg")):
            idx = cond.name[-1]
            df = self.latest.get(idx)
            if df is not None:
                if cond.name.startswith("pos"):
                    mask = df['change_rate'] > 0
                else:
                    mask = df['change_rate'] < 0
                return set(df[mask]['ticker'])
            return set()

        if cond.name == "junk":
            # 거래대금≥500억, 종가<3배, 스팩·우선주 제외, 종가≥1,000
            s1 = set(self.df_period.groupby('ticker')['value'].max().loc[lambda x: x >= 5e10].index)
            min_close = self.df_period.groupby('ticker')['close'].min()
            latest_c = self.latest['0'].set_index('ticker')['close']
            s2 = set((latest_c / min_close).loc[lambda x: x < 3].index)
            df0 = self.latest['0'].set_index('ticker')
            s3 = {
                t for t in latest_c.index
                if '스팩' not in df0.loc[t, 'stock_name'] and t.endswith('0') and df0.loc[t, 'close'] >= 1000
            }
            return s1 & s2 & s3
        return set()

# ──────────────
# 8. 메인 앱
# ──────────────
class StockRecommenderApp:
    def __init__(self):
        self.db_path = AppConfig.get_db_path()
        self.db = DBManager(self.db_path)

    def run(self):
        LoggerSetup.setup()
        logging.info("앱 실행/접속")

        st.set_page_config(layout="wide", page_title="Stock Recommender")
        st.title("📊 필터 조건 기반 종목 추천기")

        df_all = self.db.load_ohlcv()
        if df_all.empty:
            st.error("DB 데이터를 불러올 수 없습니다. 경로/DB 확인")
            return
        trading_days = self.db.get_trading_days(df_all)

        # UI
        sidebar = SidebarUI(self.db_path, trading_days)
        start_date, end_date, conditions, key_map, run = sidebar.render()

        if run:
            # 입력 validation
            if start_date > end_date:
                st.warning("조회 시작일이 종료일보다 늦을 수 없습니다.")
                return
            df_period = df_all[(df_all['date_only'] >= start_date) & (df_all['date_only'] <= end_date)]
            if df_period.empty:
                st.warning("선택 기간에 데이터가 없습니다.")
                return

            recent_days = TradingCalendar.get_recent_n(trading_days, end_date, 3)
            latest: Dict[str, pd.DataFrame] = {
                str(i): df_all[df_all['date_only'] == recent_days[i]] for i in range(len(recent_days))
            }

            # 추천
            engine = StockFilterEngine(conditions, df_period, latest)
            tickers = engine.recommend()
            df_result = df_all[
                (df_all['ticker'].isin(tickers)) & (df_all['date_only'] == latest['0']['date_only'].iloc[0])
            ]
            df_result = df_result.sort_values("market_cap", ascending=False).reset_index(drop=True)
            df_result.index += 1

            if df_result.empty:
                st.info("조건에 맞는 종목이 없습니다.")
                return

            # 보기 좋은 컬럼 변환
            df_result['시가총액'] = df_result['market_cap'].apply(Utility.format_unit)
            df_result['영업이익(1q)'] = df_result['thstrm_amount'].apply(Utility.format_unit)
            df_result['거래대금'] = df_result['value'].apply(Utility.format_unit)
            df_result['거래량'] = df_result['volume'].apply(Utility.format_unit)
            df_result['차트'] = df_result['ticker'].apply(lambda x: f"https://finance.naver.com/item/fchart.naver?code={x}")

            st.subheader(f"추천 종목 {len(df_result)}개 ({end_date})")
            st.data_editor(
                df_result[[
                    'ticker', '차트', 'stock_name', '시가총액', '영업이익(1q)',
                    '거래량', '거래대금', 'change_rate'
                ]].rename(columns={
                    'ticker': '종목코드', 'stock_name': '종목명', 'change_rate': '등락률'
                }),
                column_config={
                    '차트': st.column_config.LinkColumn(label='차트', display_text='📈')
                },
                hide_index=True, height=800
            )

# ──────────────
# 9. 실행 진입점
# ──────────────
if __name__ == "__main__":
    StockRecommenderApp().run()
