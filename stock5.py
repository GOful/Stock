import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, date, timedelta

# 페이지 설정
st.set_page_config(layout="wide", initial_sidebar_state="expanded")
# 사이드바 너비 조정 (버전별 클래스명 확인 필요)
st.markdown(
    """
    <style>
    .css-1d391kg {width: 300px;}  
    </style>
    """, unsafe_allow_html=True
)

# --- Helper ---
def load_data(db_path="market_ohlcv.db"):
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT date, ticker, name AS 종목명, open, high, low, close, volume, value, change_rate FROM market_ohlcv",
        conn, parse_dates=["date"]
    )
    conn.close()
    return df

# 초기 로드 및 기본 날짜 계산
df_all = load_data("market_ohlcv.db")
df_all['date_only'] = df_all['date'].dt.date
max_date = df_all['date_only'].max()
default_end = max_date
default_start = default_end - timedelta(days=200)

st.title("필터 조건 기반 종목 추천")

# --- 사이드바: 설정 ---
db_path = st.sidebar.text_input("SQLite DB 경로", value="market_ohlcv.db")
start_date = st.sidebar.date_input("시작 날짜", default_start)
end_date = st.sidebar.date_input("종료 날짜", default_end)
st.sidebar.header("필터조건 및 논리")
from streamlit import session_state
conds = []

def checkbox_logic(label, key):
    col1, col2 = st.sidebar.columns([3,1])
    use = col1.checkbox(label, key=key+"_chk")
    if not use and key+"_logic" in session_state:
        del session_state[key+"_logic"]
    logic = None
    if use:
        logic = col2.radio(
            "로직", ["AND","OR"], index=0,
            key=key+"_logic", label_visibility='collapsed'
        )
    return use, logic

# 필터 항목 설정
# key_to_label 맵 생성
key_to_label = {}
for i in [0,1,2]:
    label = f"D-{i} 양봉 (등락률 > 0)"
    key = f"pos{i}"
    use, logic = checkbox_logic(label, key)
    if use:
        conds.append((key, logic))
    key_to_label[key] = label

    label = f"D-{i} 음봉 (등락률 < 0)"
    key = f"neg{i}"
    use, logic = checkbox_logic(label, key)
    if use:
        conds.append((key, logic))
    key_to_label[key] = label

label = "기간 중 거래대금 ≥500억"
use, logic = checkbox_logic(label, "value_cond")
if use: conds.append(("value_cond", logic))
key_to_label["value_cond"] = label

label = "기간 중 종가 상승 3배 미만"
use, logic = checkbox_logic(label, "price_cond")
if use: conds.append(("price_cond", logic))
key_to_label["price_cond"] = label

use_spc = st.sidebar.checkbox("스팩/우선주 제외/종가1000원 이상", key="exclude_spc")
key_to_label["spc"] = "스팩/우선주 제외/종가1000원 이상"

# 데이터 로드 및 기간 필터링
df = load_data(db_path)
df['date_only'] = df['date'].dt.date
mask = (df['date_only'] >= start_date) & (df['date_only'] <= end_date)
df_period = df.loc[mask]
if df_period.empty:
    st.warning("선택 기간에 데이터가 없습니다.")
    st.stop()

# 날짜별 매핑 (거래일 기준 바로 전날짜 검색)
# dates 딕셔너리가 필요합니다 (D-0, D-1, D-2 기준 날짜 계산)
dates = {str(i): end_date - timedelta(days=i) for i in [0,1,2]}

def get_latest_df(df_all, target_date):
    df_day = df_all[df_all['date_only'] == target_date]
    if df_day.empty:
        return get_latest_df(df_all, target_date - timedelta(days=1))
    return df_day

latest = {}
for k, d in dates.items():
    latest[k] = get_latest_df(df, d)  # df에는 전체 기간 데이터}
for k, d in dates.items():
    latest[k] = get_latest_df(df, d)  # df에는 전체 기간 데이터


# --- 조건별 결과 개수 ---
st.write("### 조건별 결과 개수")
metric_count = len(conds) + (1 if use_spc else 0)
if metric_count > 0:
    cols = st.columns(metric_count)
    for idx, (cond, logic) in enumerate(conds):
        label = key_to_label.get(cond, cond)
        if cond.startswith('pos') or cond.startswith('neg'):
            df_day = latest.get(cond[-1], pd.DataFrame())
            cnt = df_day[df_day['change_rate']>0]['ticker'].nunique() if cond.startswith('pos') else df_day[df_day['change_rate']<0]['ticker'].nunique()
        elif cond == 'value_cond':
            cnt = df_period.groupby('ticker')['value'].max().ge(5e10).sum()
        else:
            min_close = df_period.groupby('ticker')['close'].min()
            latest_close = latest['0'].set_index('ticker')['close']
            cnt = (latest_close/min_close).lt(3).sum()
        cols[idx].metric(label=f"[{label}]", value=f"{cnt}개")
    if use_spc:
        spc_label = key_to_label.get('spc', 'spc')
        df0 = latest['0']
        cnt_spc = ((df0['종목명'].str.contains('스팩')) | (~df0['ticker'].str.endswith('0')) | (df0['close']<1000)).sum()
        cols[-1].metric(label=f"[{spc_label}]", value=f"{cnt_spc}개")

# --- 현재 필터 조건 ---
st.write("### 현재 필터 조건")
expr = []
for cond, logic in conds:
    label = key_to_label.get(cond, cond)
    expr.append(f"[{label}]{logic}")
if use_spc:
    expr.append(f"[{key_to_label.get('spc','spc')}]" )
st.info(" ".join(expr) if expr else "조건 없음")

# --- 추천 종목 ---
# --- 추천 종목 생성 ---
# 조건 결합
if conds:
    final = None
    for cond, logic in conds:
        # 추출
        if cond.startswith(('pos','neg')):
            df_day = latest[cond[-1]]
            s = set(df_day[df_day['change_rate']>0]['ticker']) if cond.startswith('pos') else set(df_day[df_day['change_rate']<0]['ticker'])
        elif cond == 'value_cond':
            s = set(df_period.groupby('ticker')['value'].max().loc[lambda x: x>=5e10].index)
        else:
            min_close = df_period.groupby('ticker')['close'].min()
            latest_close = latest['0'].set_index('ticker')['close']
            s = set((latest_close/min_close).loc[lambda x: x<3].index)
        # 결합
        if final is None:
            final = s
        else:
            final = final & s if logic=='AND' else final | s
else:
    # 조건이 없으면 전체 D-0 종목 기준
    final = set(latest['0']['ticker'])

# 스팩/우선주 제외 및 종가 1000원 이상 필터
if use_spc and final:
    df0 = latest['0'].set_index('ticker')
    # 스팩/우선주 이름 포함 또는 우선주 티커(not ending 0) 제외, 그리고 종가 < 1000 제외
    exclude = {
        t for t in final
        if ('스팩' in df0.loc[t,'종목명'])
           or (not t.endswith('0'))
           or (df0.loc[t,'close'] < 1000)
    }
    final -= exclude

if final is None:
    final = set()
# 스팩/우선주 제외
if use_spc and final:
    df0 = df.set_index('ticker')
    exclude = {t for t in final if '스팩' in df0.loc[t,'종목명'] or not t.endswith('0')}
    final -= exclude

# 헤더 출력
num_final = len(final)
st.subheader(f"추천 종목 ({num_final}개) - 기준일: {end_date}")

# 데이터 출력
if num_final > 0:
    df_res = latest['0'][latest['0']['ticker'].isin(final)].copy()
    df_res = df_res.sort_values('value', ascending=False)
    df_res.index = range(1, len(df_res)+1)
    df_res = df_res.rename(columns={
        'ticker':'종목코드','종목명':'종목명','open':'시가','high':'고가',
        'low':'저가','close':'종가','volume':'거래량','value':'거래대금','change_rate':'등락률'
    })
    st.dataframe(df_res[['종목코드','종목명','시가','고가','저가','종가','거래량','거래대금','등락률']].set_index('종목코드'))
else:
    st.info("조건에 맞는 종목이 없습니다.")
