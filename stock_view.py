import os
import sys
import subprocess
import sqlite3
import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import bisect

# ──── 0) 베이스 디렉토리(이 .py 파일이 있는 디렉토리) ──────
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# ──── 1) 파일 경로 설정 (모두 BASE_DIR 기준으로) ──────────
DATA_SCRIPT = os.path.join(BASE_DIR, "stock_data.py")
DB_FILE     = os.path.join(BASE_DIR, "market_ohlcv.db")

# ──── 2) 페이지 로드 시: DB 업데이트 및 상태 표시 ──────────
if "db_updated" not in st.session_state:
    with st.spinner("앱 시작: DB 업데이트 중입니다..."):
        subprocess.run([sys.executable, DATA_SCRIPT], check=True)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(date) FROM market_ohlcv")
    latest_date_str = cursor.fetchone()[0]
    conn.close()
    latest_date = datetime.strptime(latest_date_str, "%Y%m%d").date()
    st.success(f"DB 업데이트 완료: 최신 DB 날짜: {latest_date}")
    st.session_state["db_updated"] = True



# --- 데이터 로드 헬퍼 ---
@st.cache_data(ttl=3600)
def load_data(db_path=DB_FILE):
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT date, ticker, name AS 종목명, open, high, low, close, volume, value, change_rate FROM market_ohlcv",
        conn, parse_dates=["date"]
    )
    conn.close()
    df['date_only'] = df['date'].dt.date
    return df

@st.cache_data(ttl=3600)
def get_trading_days(df):
    return sorted(df['date_only'].unique())

def find_prev_trading_day(trading_days, target_date):
    idx = bisect.bisect_left(trading_days, target_date)
    if idx < len(trading_days) and trading_days[idx] == target_date:
        return target_date
    return trading_days[idx-1] if idx > 0 else trading_days[0]

# --- 초기 로드 및 기본 날짜 계산 ---
df_all = load_data()
trading_days = get_trading_days(df_all)
max_date = max(trading_days)
default_end = max_date
default_start = default_end - timedelta(days=200)

st.title("필터 조건 기반 종목 추천")

# --- 사이드바: DB 경로 & 날짜 선택 ---
db_path     = st.sidebar.text_input("SQLite DB 경로", value=DB_FILE)
col1, col2 = st.sidebar.columns(2)
with col1:
    start_date = st.date_input("조회기간 - 부터 ", value=default_start, key="start")
with col2:
    end_date = st.date_input("까지", value=default_end, key="end")

# --- 사이드바: 필터조건 및 논리 ---
conds = []
key_to_label = {}

# D-0, D-1, D-2 일봉 필터
for i in [0, 1, 2]:
    use = st.sidebar.checkbox(f"D-{i} 일봉", key=f"day{i}_use")
    if use:
        col_dir, col_logic = st.sidebar.columns([2,1])
        direction = col_dir.radio(
            label="candle",
            options=["양봉 (등락률 > 0)", "음봉 (등락률 < 0)"],
            key=f"day{i}_dir",
            horizontal=True,
            label_visibility="collapsed",
        )
        logic = col_logic.radio(
            label="AndOr ",
            options=["AND", "OR"],
            key=f"day{i}_logic",
            horizontal=True,
            label_visibility="collapsed",
        )
        typ = "pos" if direction.startswith("양봉") else "neg"
        cond_key = f"{typ}{i}"
        conds.append((cond_key, logic))
        key_to_label[cond_key] = f"D-{i} {'양봉' if typ=='pos' else '음봉'}"
    st.sidebar.markdown("---")

# 거래대금 필터
use_value = st.sidebar.checkbox("기간 중 거래대금 ≥500억", key="value_cond_chk")
if use_value:
    col1, col2 = st.sidebar.columns([3,1])
    _ = col1.write("")
    logic = col2.radio(
        "CondAndOr", ["AND", "OR"],
        key="value_cond_logic",
        label_visibility="collapsed"
    )
    conds.append(("value_cond", logic))
    key_to_label["value_cond"] = "기간 중 거래대금 ≥500억"
st.sidebar.markdown("---")

# 종가 상승 3배 미만
use_price = st.sidebar.checkbox("기간 중 종가 상승 3배 미만", key="price_cond_chk")
if use_price:
    col1, col2 = st.sidebar.columns([3,1])
    _ = col1.write("")
    logic = col2.radio(
        "AndOr1", ["AND", "OR"],
        key="price_cond_logic",
        label_visibility="collapsed"
    )
    conds.append(("price_cond", logic))
    key_to_label["price_cond"] = "기간 중 종가 상승 3배 미만"
st.sidebar.markdown("---")

# 스팩/우선주 제외 & 종가 1000원 이상
exclude_spc = st.sidebar.checkbox("스팩/우선주 제외/종가1000원 이상", key="exclude_spc")
key_to_label["spc"] = "스팩/우선주 제외/종가1000원 이상"
st.sidebar.markdown("---")

# --- "종목추천" 버튼 ---
run = st.sidebar.button("종목추천")

# --- 버튼을 눌렀을 때만 실행 ---
if run:
    df = load_data(db_path)
    df['date_only'] = df['date'].dt.date
    df_period = df[(df['date_only'] >= start_date) & (df['date_only'] <= end_date)]
    if df_period.empty:
        st.warning("선택 기간에 데이터가 없습니다.")
    else:
        dates = {str(i): end_date - timedelta(days=i) for i in [0,1,2]}
        latest = {}
        for k, target in dates.items():
            prev = find_prev_trading_day(trading_days, target)
            latest[k] = df_all[df_all['date_only'] == prev]

        st.write("### 조건별 결과 개수")
        metric_count = len(conds) + (1 if exclude_spc else 0)
        if metric_count > 0:
            cols = st.columns(metric_count)
            for idx, (cond, logic) in enumerate(conds):
                label = key_to_label.get(cond, cond)
                if cond.startswith(('pos','neg')):
                    day = cond[-1]
                    df_day = latest[day]
                    cnt = df_day[df_day['change_rate'] > 0]['ticker'].nunique() \
                          if cond.startswith('pos') else \
                          df_day[df_day['change_rate'] < 0]['ticker'].nunique()
                elif cond == 'value_cond':
                    cnt = df_period.groupby('ticker')['value'].max().ge(5e10).sum()
                else:
                    min_close = df_period.groupby('ticker')['close'].min()
                    latest_close = latest['0'].set_index('ticker')['close']
                    cnt = (latest_close / min_close).lt(3).sum()
                cols[idx].metric(label=f"[{label}]", value=f"{cnt}개")
            if exclude_spc:
                df0 = latest['0']
                cnt_spc = ((df0['종목명'].str.contains('스팩')) |
                           (~df0['ticker'].str.endswith('0')) |
                           (df0['close'] < 1000)).sum()
                cols[-1].metric(label=f"[{key_to_label['spc']}]", value=f"{cnt_spc}개")
        st.write("### 현재 필터 조건")
        expr = [f"[{key_to_label[c]}]{l}" for c,l in conds]
        if exclude_spc:
            expr.append(f"[{key_to_label['spc']}]")
        st.info(" ".join(expr) if expr else "조건 없음")

        if conds:
            final = None
            for cond, logic in conds:
                if cond.startswith(('pos','neg')):
                    day = cond[-1]
                    df_day = latest[day]
                    s = set(df_day[df_day['change_rate'] > 0]['ticker']) \
                        if cond.startswith('pos') \
                        else set(df_day[df_day['change_rate'] < 0]['ticker'])
                elif cond == 'value_cond':
                    s = set(df_period.groupby('ticker')['value'].max()
                            .loc[lambda x: x>=5e10].index)
                else:
                    min_close = df_period.groupby('ticker')['close'].min()
                    latest_close = latest['0'].set_index('ticker')['close']
                    s = set((latest_close / min_close).loc[lambda x: x<3].index)
                final = s if final is None else \
                        (final & s if logic=='AND' else final | s)
        else:
            final = set(latest['0']['ticker'])

        if exclude_spc and final:
            df0 = latest['0'].set_index('ticker')
            to_exclude = {t for t in final
                          if ('스팩' in df0.loc[t,'종목명']) or (not t.endswith('0')) or (df0.loc[t,'close'] < 1000)}
            final -= to_exclude

        df_res = latest['0'][latest['0']['ticker'].isin(final)].copy()
        df_res = df_res.sort_values('value', ascending=False)
        df_res.index = range(1, len(df_res) + 1)
        num_final = len(df_res)

        st.subheader(f"추천 종목 ({num_final}개)에 대한 {end_date} 데이터")
        if num_final > 0:
            df_res = df_res.rename(columns={
                'ticker':'종목코드','종목명':'종목명','open':'시가','high':'고가',
                'low':'저가','close':'종가','volume':'거래량',
                'value':'거래대금','change_rate':'등락률'
            })
            st.dataframe(df_res[['종목코드','종목명','시가','고가','저가','종가','거래량','거래대금','등락률']].set_index('종목코드'),use_container_width=True)
        else:
            st.info("조건에 맞는 종목이 없습니다.")
else:
    st.sidebar.write("필터를 설정한 뒤 ‘종목추천’ 버튼을 눌러주세요.")
