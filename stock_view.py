import sqlite3
import bisect
from datetime import datetime, date, timedelta
import streamlit as st
import pandas as pd
from pathlib import Path
import logging
import warnings

# 로그 및 경고 설정
logging.getLogger("streamlit.elements.lib.policies").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=FutureWarning)

class Config:
    def __init__(self):
        # 앱 기준 디렉터리
        base_dir = Path(__file__).parent
        # SQLite DB 파일 경로
        self.DB_FILE = str(base_dir / "market_ohlcv.db")
        # 데이터 업데이트 스크립트 경로 (GitHub Actions에서 실행됨)
        self.DATA_SCRIPT = str(base_dir / "stock_data.py")

class DatabaseUpdater:
    def __init__(self, config: Config):
        self.db_file = config.DB_FILE

    def update(self) -> date:
        # 최초 실행 시 한 번만 최신 DB 날짜를 읽어와 표시
        if "db_updated" not in st.session_state:
            with st.spinner("앱 시작: DB 최신화 정보를 불러오는 중입니다..."):
                # GitHub Actions 워크플로우가 별도로 데이터 업데이트를 담당하므로 subprocess 호출 제거
                pass
            # 최신 날짜 조회
            conn = sqlite3.connect(self.db_file)
            latest_str = conn.cursor().execute(
                "SELECT MAX(date) FROM market_ohlcv"
            ).fetchone()[0]
            conn.close()
            latest = datetime.strptime(latest_str, "%Y%m%d").date()
            st.success(f"DB 최신 날짜: {latest}")
            st.session_state["db_updated"] = True
            return latest
        return None

class DataManager:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def load_data(self) -> pd.DataFrame:
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query(
            """
            SELECT date, ticker, name AS 종목명, open, high, low, close,
                   volume, value, change_rate, market_cap
            FROM market_ohlcv
            """,
            conn, parse_dates=["date"]
        )
        conn.close()
        df['date_only'] = df['date'].dt.date
        return df

    def get_trading_days(self, df: pd.DataFrame) -> list:
        return sorted(df['date_only'].unique())

class CalendarManager:
    @staticmethod
    def prev_trading_day(trading_days: list, target: date) -> date:
        idx = bisect.bisect_left(trading_days, target)
        if idx < len(trading_days) and trading_days[idx] == target:
            return target
        return trading_days[idx-1] if idx > 0 else trading_days[0]

class FilterCondition:
    def __init__(self, name: str, label: str, logic: str):
        self.name = name
        self.label = label
        self.logic = logic

class SidebarManager:
    def __init__(self, config: Config, trading_days: list):
        self.config = config
        self.trading_days = trading_days

    def render(self):
        with st.sidebar.form(key="filter_form"):
            st.title("필터 설정")
            st.text_input("SQLite DB 경로", value=self.config.DB_FILE)

            default_end   = max(self.trading_days)
            default_start = default_end - timedelta(days=200)
            c1, c2 = st.columns(2)
            start_date = c1.date_input("조회기간 - 부터", value=default_start)
            end_date   = c2.date_input("까지",      value=default_end)
            st.markdown("---")

            conditions, key_map = [], {}
            for i in [0,1,2]:
                use = st.checkbox(f"D-{i} 일봉", key=f"day{i}_use")
                dir_col, lg_col = st.columns([3,1])
                direction = dir_col.selectbox(
                    "", ["", "양봉", "음봉"],
                    key=f"day{i}_dir", label_visibility="collapsed"
                )
                logic = lg_col.radio(
                    "", ["AND","OR"],
                    key=f"day{i}_logic", horizontal=True,
                    label_visibility="collapsed"
                )
                if use and direction:
                    typ   = 'pos' if direction=="양봉" else 'neg'
                    name  = f"{typ}{i}"
                    label = f"D-{i} {direction}"
                    conditions.append(FilterCondition(name, label, logic))
                    key_map[name] = label
                st.markdown("---")

            use_junk = st.checkbox(
                "우량주필터",
                key="junk_chk",
                help="거래대금≥500억, 종가<3배, 스팩·우선주 제외&종가≥1,000원을 모두 적용"
            )
            if use_junk:
                conditions.append(FilterCondition("junk", "우량주필터", "AND"))
                key_map["junk"] = "우량주필터"
            st.markdown("---")

            run = st.form_submit_button("종목추천")

        return start_date, end_date, conditions, key_map, run

class MetricsManager:
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
        if cond.name.startswith(("pos","neg")):
            df_day = self.latest[cond.name[-1]]
            mask = df_day['change_rate']>0 if cond.name.startswith("pos") else df_day['change_rate']<0
            return df_day[mask]['ticker'].nunique()
        if cond.name == "junk":
            tickers = RecommendationEngine(
                [cond], self.df_period, self.latest
            ).run()
            return len(tickers)
        return 0

class RecommendationEngine:
    def __init__(self, conditions, df_period, latest):
        self.conditions = conditions
        self.df_period = df_period
        self.latest = latest

    def run(self) -> set:
        result = None
        for cond in self.conditions:
            s = self._tickers_for(cond)
            result = s if result is None else (
                result & s if cond.logic=="AND" else result | s
            )
        if result is None:
            result = set(self.latest['0']['ticker'])
        return result

    def _tickers_for(self, cond: FilterCondition) -> set:
        if cond.name.startswith(("pos","neg")):
            df_day = self.latest[cond.name[-1]]
            mask = df_day['change_rate']>0 if cond.name.startswith("pos") else df_day['change_rate']<0
            return set(df_day[mask]['ticker'])
        if cond.name == "junk":
            s1 = set(
                self.df_period.groupby('ticker')['value']
                              .max().loc[lambda x: x>=5e10]
                              .index
            )
            min_close = self.df_period.groupby('ticker')['close'].min()
            latest_c  = self.latest['0'].set_index('ticker')['close']
            s2 = set((latest_c / min_close).loc[lambda x: x<3].index)
            df0 = self.latest['0'].set_index('ticker')
            s3 = {
                t for t in latest_c.index
                if not (
                    ('스팩' in df0.loc[t,'종목명'])
                    or (not t.endswith('0'))
                    or (df0.loc[t,'close']<1000)
                )
            }
            return s1 & s2 & s3
        return set()

class UIManager:
    @staticmethod
    def show_title():
        st.title("필터 조건 기반 종목 추천")

    @staticmethod
    def show_conditions(conditions, key_map):
        st.write("### 현재 필터 조건")
        expr = [f"[{c.label}]{c.logic}" for c in conditions]
        st.info(" ".join(expr) if expr else "조건 없음")

    @staticmethod
    def _format_korean_unit(x: int) -> str:
        if x >= 10**12:
            return f"{x/10**12:.2f}조"
        if x >= 10**8:
            return f"{x/10**8:.1f}억"
        if x >= 10**4:
            return f"{x/10**4:.0f}만"
        return f"{x:,}"

    @staticmethod
    def show_results(tickers: set, latest: dict, end_date: date):
        df0 = latest['0']
        df = df0[df0['ticker'].isin(tickers)].copy()
        df = df.sort_values('market_cap', ascending=False).reset_index(drop=True)
        df.index += 1

        st.subheader(f"추천 종목 ({len(df)}개) — {end_date}")
        if df.empty:
            st.info("조건에 맞는 종목이 없습니다.")
            return

        df = df.rename(columns={
            'ticker':'종목코드','name':'종목명','open':'시가','high':'고가',
            'low':'저가','close':'종가','volume':'거래량',
            'value':'거래대금','change_rate':'등락률','market_cap':'시가총액'
        })

        df['시가총액'] = df['시가총액'].apply(UIManager._format_korean_unit)
        df['거래대금'] = df['거래대금'].apply(UIManager._format_korean_unit)
        df['거래량'] = df['거래량'].apply(UIManager._format_korean_unit)

        df['차트'] = df['종목코드'].apply(
            lambda c: f"https://finance.naver.com/item/fchart.naver?code={c}"
        )

        display_cols = ['종목코드','차트','종목명','시가총액','거래량','거래대금','등락률']
        FIXED_WIDTH = 100
        cfg = {col: st.column_config.Column(width=FIXED_WIDTH)
               for col in display_cols}
        cfg['차트'] = st.column_config.LinkColumn(
            label='차트', width=FIXED_WIDTH, display_text='📈'
        )

        st.data_editor(
            df[display_cols],
            hide_index=True,
            column_config=cfg,
            width=None,
            height=400
        )

class StockRecommenderApp:
    def __init__(self):
        self.config       = Config()
        self.updater      = DatabaseUpdater(self.config)
        self.data_manager = DataManager(self.config.DB_FILE)
        self.calendar     = CalendarManager()

    def run(self):
        st.set_page_config(layout="wide")
        UIManager.show_title()

        latest_date = self.updater.update()
        df_all = self.data_manager.load_data()
        trading_days = self.data_manager.get_trading_days(df_all)

        sidebar = SidebarManager(self.config, trading_days)
        start_date, end_date, conditions, key_map, run = sidebar.render()

        if run:
            df_period = df_all[
                (df_all['date_only']>=start_date) &
                (df_all['date_only']<=end_date)
            ]
            if df_period.empty:
                st.warning("선택 기간에 데이터가 없습니다.")
                return

            latest = {
                str(i): df_all[
                    df_all['date_only']==
                    self.calendar.prev_trading_day(trading_days, end_date - timedelta(days=i))
                ]
                for i in [0,1,2]
            }

            MetricsManager(conditions, latest, df_period).show()
            UIManager.show_conditions(conditions, key_map)

            tickers = RecommendationEngine(conditions, df_period, latest).run()
            UIManager.show_results(tickers, latest, end_date)
        else:
            st.sidebar.write("필터를 설정한 뒤 ‘종목추천’ 버튼을 눌러주세요.")

if __name__ == "__main__":
    StockRecommenderApp().run()
