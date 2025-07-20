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
    애플리케이션 설정 경로를 관리하는 클래스
    - DB 파일 경로 및 데이터 스크립트 경로를 초기화 시에 계산
    """
    def __init__(self):
        # 현재 파일의 절대 경로를 기반으로 기본 디렉토리 설정
        base_dir = os.path.abspath(os.path.dirname(__file__))
        # 시장 OHLCV 데이터가 저장된 SQLite DB 파일 경로
        self.DB_FILE = os.path.join(base_dir, "market_ohlcv.db")
        # 데이터를 최신화하는 스크립트 파일 경로
        self.DATA_SCRIPT = os.path.join(base_dir, "stock_data.py")


class DatabaseUpdater:
    """
    SQLite 데이터베이스를 앱 시작 시 한 번만 업데이트하도록 관리
    - st.session_state["db_updated"] 플래그를 사용하여 중복 실행 방지
    """
    def __init__(self, config: Config):
        self.db_file = config.DB_FILE          # 업데이트할 DB 파일 경로
        self.data_script = config.DATA_SCRIPT  # 실행할 스크립트 경로

    def update(self) -> date:
        # 세션 상태에 업데이트 여부가 기록되어 있지 않으면 스크립트를 실행
        if "db_updated" not in st.session_state:
            with st.spinner("앱 시작: DB 업데이트 중입니다..."):
                # 외부 데이터 수집 스크립트(stock_data.py)를 실행
                subprocess.run([sys.executable, self.data_script], check=True)

            # 업데이트 완료 후 DB에서 최신 날짜 조회
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(date) FROM market_ohlcv")
            latest_str = cursor.fetchone()[0]  # 'YYYYMMDD' 형식 문자열
            conn.close()

            # 문자열을 date 객체로 변환
            latest = datetime.strptime(latest_str, "%Y%m%d").date()
            # 사용자에게 성공 메시지 출력
            st.success(f"DB 업데이트 완료: 최신 DB 날짜: {latest}")
            # 세션 상태에 업데이트 완료 플래그 설정
            st.session_state["db_updated"] = True
            return latest
        # 이미 업데이트된 상태라면 None 반환
        return None


class DataManager:
    """
    SQLite에서 OHLCV 데이터를 로드하고 캐싱하는 클래스
    - Streamlit @st.cache_data 데코레이터로 데이터 프레임 캐싱
    """
    def __init__(self, db_path: str):
        self.db_path = db_path  # DB 파일 경로

    @st.cache_data(ttl=3600)
    def load_data(_self) -> pd.DataFrame:
        """
        DB에서 시계열 OHLCV 데이터를 조회하여 판다스 DataFrame으로 반환
        - date: datetime 타입으로 파싱
        - 종목명: 'name' 컬럼을 '종목명'으로 변경
        - date_only: date만 추출한 컬럼 추가
        """
        # DB 연결 및 쿼리 실행
        conn = sqlite3.connect(_self.db_path)
        df = pd.read_sql_query(
            """
            SELECT date, ticker, name AS 종목명, open, high, low, close, volume, value, change_rate
            FROM market_ohlcv
            """,
            conn,
            parse_dates=["date"]  # date 컬럼을 datetime으로 변환
        )
        conn.close()
        # datetime에서 date 부분만 추출하여 별도 컬럼에 저장
        df['date_only'] = df['date'].dt.date
        return df

    def get_trading_days(self, df: pd.DataFrame) -> list:
        """
        데이터프레임에서 고유한 거래일 목록을 추출하여 정렬된 리스트로 반환
        - 빠른 연산이므로 캐싱 불필요
        """
        return sorted(df['date_only'].unique())


class CalendarManager:
    """
    거래일 기준으로 이전 영업일 조회 로직을 제공
    """
    @staticmethod
    def prev_trading_day(trading_days: list, target: date) -> date:
        """
        주어진 거래일 리스트에서 target 날짜 이전(또는 동일) 거래일을 반환
        - bisect를 사용하여 이진 탐색으로 빠르게 위치 계산
        """
        idx = bisect.bisect_left(trading_days, target)
        # target이 목록에 존재하면 그대로 반환
        if idx < len(trading_days) and trading_days[idx] == target:
            return target
        # 목록 범위 내에서 이전 인덱스의 거래일 반환, 없으면 첫 번째 거래일 반환
        return trading_days[idx-1] if idx > 0 else trading_days[0]


class FilterCondition:
    """
    단일 필터 조건을 표현하는 객체
    - name: 내부 처리용 필터 식별자
    - label: UI에 표시할 한글 라벨
    - logic: AND/OR 조건 결합 방식
    """
    def __init__(self, name: str, label: str, logic: str):
        self.name = name
        self.label = label
        self.logic = logic


class SidebarManager:
    """
    Streamlit 사이드바에 필터 및 조회 기간 UI를 렌더링하고 입력값을 반환
    """
    def __init__(self, config: Config, trading_days: list):
        self.config = config              # 설정 객체 (DB 경로 등)
        self.trading_days = trading_days  # 거래일 리스트

    def render(self):
        # 사이드바 제목
        st.sidebar.title("필터 설정")
        # DB 경로 입력란 (미사용 시에도 경로 확인용으로 표시)
        _ = st.sidebar.text_input("SQLite DB 경로", value=self.config.DB_FILE)

        # 기본 조회 기간: 마지막 거래일 기준 200일 전부터
        default_end = max(self.trading_days)
        default_start = default_end - timedelta(days=200)
        cols = st.sidebar.columns(2)
        start_date = cols[0].date_input("조회기간 - 부터", value=default_start)
        end_date = cols[1].date_input("까지", value=default_end)

        conditions, key_map = [], {}
        # D-필터 0~2: 일봉 양/음봉 필터
        for i in [0, 1, 2]:
            use = st.sidebar.checkbox(f"D-{i} 일봉", key=f"day{i}_use")
            if use:
                dir_col, logic_col = st.sidebar.columns([3,1])
                direction = dir_col.radio("", ["양봉", "음봉"], key=f"day{i}_dir", horizontal=True)
                logic = logic_col.radio("", ["AND", "OR"], key=f"day{i}_logic", horizontal=True)
                # 양봉이면 'pos', 음봉이면 'neg'
                typ = 'pos' if direction == '양봉' else 'neg'
                name = f"{typ}{i}"
                label = f"D-{i} {'양봉' if typ=='pos' else '음봉'}"
                conditions.append(FilterCondition(name, label, logic))
                key_map[name] = label
            st.sidebar.markdown("---")

        # 거래대금 필터: 기간 중 최대 거래대금 ≥ 500억
        if st.sidebar.checkbox("기간 중 거래대금 ≥500억", key="value_chk"):
            logic = st.sidebar.radio("", ["AND","OR"], key="value_logic", label_visibility="collapsed")
            conditions.append(FilterCondition("value_cond", "거래대금 ≥500억", logic))
            key_map["value_cond"] = "거래대금 ≥500억"
        st.sidebar.markdown("---")

        # 종가 상승 필터: 종가 상승율 < 3배
        if st.sidebar.checkbox("종가 상승 3배 미만", key="price_chk"):
            logic = st.sidebar.radio("", ["AND","OR"], key="price_logic", label_visibility="collapsed")
            conditions.append(FilterCondition("price_cond", "종가 상승 <3배", logic))
            key_map["price_cond"] = "종가 상승 <3배"
        st.sidebar.markdown("---")

        # 스팩/우선주 제외 및 종가 1,000원 이상 필터
        exclude_spc = st.sidebar.checkbox("스팩/우선주 제외/종가1000원 이상", key="ex_spc")
        key_map["spc"] = "스팩/우선주 제외/종가1000원 이상"
        st.sidebar.markdown("---")

        # 종목추천 버튼
        run = st.sidebar.button("종목추천")
        return start_date, end_date, conditions, key_map, exclude_spc, run


class MetricsManager:
    """
    선택된 각 조건별 종목 개수를 계산하여 화면에 메트릭으로 표시
    """
    def __init__(self, conditions: list, latest: dict, df_period: pd.DataFrame):
        self.conditions = conditions  # FilterCondition 목록
        self.latest = latest          # 최신 거래일별 DataFrame 사전
        self.df_period = df_period    # 조회 기간 데이터

    def show(self):
        # 조건이 없으면 메트릭 영역 생략
        if not self.conditions:
            return
        st.write("### 조건별 결과 개수")
        cols = st.columns(len(self.conditions))
        for idx, cond in enumerate(self.conditions):
            cnt = self._count(cond)
            cols[idx].metric(label=f"[{cond.label}]", value=f"{cnt}개")

    def _count(self, cond: FilterCondition) -> int:
        # D-필터 카운트: 양봉/음봉 개수
        if cond.name.startswith(('pos','neg')):
            df_day = self.latest[cond.name[-1]]
            if cond.name.startswith('pos'):
                return df_day[df_day['change_rate'] > 0]['ticker'].nunique()
            return df_day[df_day['change_rate'] < 0]['ticker'].nunique()
        # 거래대금 필터 카운트
        if cond.name == 'value_cond':
            return self.df_period.groupby('ticker')['value'].max().ge(5e10).sum()
        # 종가 상승율 필터 카운트
        if cond.name == 'price_cond':
            min_close = self.df_period.groupby('ticker')['close'].min()
            latest_c = self.latest['0'].set_index('ticker')['close']
            return (latest_c / min_close).lt(3).sum()
        return 0


class RecommendationEngine:
    """
    필터 조건 논리에 따라 최종 추천 종목 집합을 계산
    """
    def __init__(self, conditions, df_period, latest, exclude_spc: bool):
        self.conditions = conditions  # FilterCondition 목록
        self.df_period = df_period    # 조회 기간 데이터
        self.latest = latest          # 최신 거래일별 DataFrame 사전
        self.exclude_spc = exclude_spc  # 스팩/우선주 제외 여부

    def run(self) -> set:
        # 초기 결과 집합 설정
        result = None
        for cond in self.conditions:
            s = self._tickers_for(cond)
            # 첫 필터이면 s 할당, 이후 AND/OR로 결합
            result = s if result is None else (result & s if cond.logic=='AND' else result | s)
        # 필터가 하나도 없으면 최신 D-0 종목 전체
        if result is None:
            result = set(self.latest['0']['ticker'])
        # 스팩/우선주 및 종가 1,000원 이하 제외 처리
        if self.exclude_spc:
            result = self._exclude_spc(result)
        return result

    def _tickers_for(self, cond: FilterCondition) -> set:
        # D-필터 종목 추출
        if cond.name.startswith(('pos','neg')):
            df_day = self.latest[cond.name[-1]]
            mask = df_day['change_rate'] > 0 if cond.name.startswith('pos') else df_day['change_rate'] < 0
            return set(df_day[mask]['ticker'])
        # 거래대금 필터 종목 추출
        if cond.name == 'value_cond':
            return set(self.df_period.groupby('ticker')['value'].max().loc[lambda x: x>=5e10].index)
        # 종가 상승율 필터 종목 추출
        if cond.name == 'price_cond':
            min_close = self.df_period.groupby('ticker')['close'].min()
            latest_c = self.latest['0'].set_index('ticker')['close']
            return set((latest_c / min_close).loc[lambda x: x<3].index)
        return set()

    def _exclude_spc(self, tickers: set) -> set:
        # D-0 데이터에서 종목명에 '스팩' 포함, 우선주(코드 끝자리 != 0), 종가<1000 제외
        df0 = self.latest['0'].set_index('ticker')
        return {t for t in tickers if not (('스팩' in df0.loc[t,'종목명']) or (not t.endswith('0')) or (df0.loc[t,'close']<1000))}


class UIManager:
    """
    Streamlit UI의 메인 콘텐츠(제목, 조건, 결과)를 렌더링하는 클래스
    """
    @staticmethod
    def show_title():
        # 페이지 상단 제목 표시
        st.title("필터 조건 기반 종목 추천")

    @staticmethod
    def show_conditions(conditions, key_map, exclude_spc):
        # 현재 활성화된 필터 조건 요약 표시
        st.write("### 현재 필터 조건")
        expr = [f"[{c.label}]{c.logic}" for c in conditions]
        if exclude_spc:
            expr.append(f"[{key_map['spc']}]")
        st.info(" ".join(expr) if expr else "조건 없음")

    @staticmethod
    def show_results(tickers: set, latest: dict, end_date: date):
        # 최종 추천 종목 데이터를 표 형태로 표시
        df0 = latest['0']
        df_res = df0[df0['ticker'].isin(tickers)].copy()
        df_res = df_res.sort_values('value', ascending=False)
        df_res.index = range(1, len(df_res)+1)
        st.subheader(f"추천 종목 ({len(df_res)}개)에 대한 {end_date} 데이터")
        if not df_res.empty:
            # 컬럼명 한글화 및 순서 지정
            df_res = df_res.rename(columns={
                'ticker':'종목코드','종목명':'종목명','open':'시가','high':'고가',
                'low':'저가','close':'종가','volume':'거래량','value':'거래대금','change_rate':'등락률'
            })
            # 인덱스를 종목코드로 설정 후 데이터프레임 표시
            st.dataframe(df_res.set_index('종목코드')[['종목명','시가','고가','저가','종가','거래량','거래대금','등락률']], use_container_width=True)
        else:
            st.info("조건에 맞는 종목이 없습니다.")


class StockRecommenderApp:
    """
    애플리케이션의 전체 플로우를 관리하는 메인 클래스
    1) DB 업데이트
    2) 데이터 로드
    3) 사이드바 렌더링
    4) 필터 실행 및 결과 표시
    """
    def __init__(self):
        self.config = Config()                          # 설정 객체 생성
        self.updater = DatabaseUpdater(self.config)     # DB 업데이트 관리자
        self.data_manager = DataManager(self.config.DB_FILE)  # 데이터 로드 관리자
        self.calendar = CalendarManager()               # 거래일 계산 유틸

    def run(self):
        # Streamlit 페이지 레이아웃 설정: 전체 폭 사용
        st.set_page_config(layout="wide")
        UIManager.show_title()  # 제목 표시

        # 1) 앱 시작 시 DB 업데이트(한 번만 실행)
        self.updater.update()

        # 2) 캐시된 데이터 로드 및 거래일 리스트 생성
        df_all = self.data_manager.load_data()
        trading_days = self.data_manager.get_trading_days(df_all)

        # 3) 사이드바에서 조회 기간 및 필터 설정 입력 받기
        sidebar = SidebarManager(self.config, trading_days)
        start_date, end_date, conditions, key_map, exclude_spc, run = sidebar.render()

        # 4) '종목추천' 버튼 클릭 시 필터 연산 수행
        if run:
            # 조회 기간에 해당하는 데이터 추출
            df_period = df_all[(df_all['date_only']>=start_date) & (df_all['date_only']<=end_date)]
            if df_period.empty:
                st.warning("선택 기간에 데이터가 없습니다.")
                return

            # D-0, D-1, D-2 기준 최신 거래일 데이터 사전 생성
            latest = {
                str(i): df_all[df_all['date_only']==
                    self.calendar.prev_trading_day(trading_days, end_date - timedelta(days=i))]
                for i in [0,1,2]
            }

            # 5) 각 조건별 메트릭 출력
            MetricsManager(conditions, latest, df_period).show()
            # 6) 활성화된 조건 요약 출력
            UIManager.show_conditions(conditions, key_map, exclude_spc)

            # 7) 필터 로직으로 추천 종목 집합 계산
            tickers = RecommendationEngine(conditions, df_period, latest, exclude_spc).run()
            # 8) 최종 결과 표 형태로 출력
            UIManager.show_results(tickers, latest, end_date)
        else:
            # 버튼 미클릭 시 안내 메시지 표시
            st.sidebar.write("필터를 설정한 뒤 ‘종목추천’ 버튼을 눌러주세요.")


if __name__ == "__main__":
    StockRecommenderApp().run()
