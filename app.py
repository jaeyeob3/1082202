"""
app.py — 한국 주식 펀더멘탈 투자등급 사이트 (Streamlit)

실행: streamlit run app.py
회사명(예: 삼성전자) 또는 종목코드(예: 005930)를 입력하면
밸류+퀄리티 종합점수와 5단계 투자등급을 보여준다.
"""

import streamlit as st
import pandas as pd

from scoring import analyze, Weights, RATINGS
import data_source as ds


st.set_page_config(page_title="한국주식 펀더멘탈 등급", page_icon="📊", layout="centered")

RATING_COLOR = {
    "적극매수": "#0b6e4f",
    "매수": "#2e9e6b",
    "중립": "#9c8a1f",
    "매도": "#c2622d",
    "적극매도": "#b3261e",
}

st.markdown("""
<style>
.block-container {max-width: 820px;}
.hero {border-radius:16px;padding:26px 28px;margin-bottom:8px;
 background:linear-gradient(135deg,#0b3d2e 0%,#0b6e4f 60%,#2e9e6b 100%);color:#fff;}
.hero h1 {margin:0;font-size:30px;font-weight:800;}
.hero p {margin:8px 0 0;opacity:.9;font-size:14px;}
</style>
<div class="hero">
  <h1>📊 한국주식 펀더멘탈 투자등급</h1>
  <p>회사명을 입력하면 밸류에이션·퀄리티를 시장 분포 대비 평가해
  <b>적극매수 · 매수 · 중립 · 매도 · 적극매도</b> 5단계 등급을 산출합니다.</p>
</div>
""", unsafe_allow_html=True)

if "q" not in st.session_state:
    st.session_state.q = ""

st.write("**인기 종목 빠른 분석**")
EXAMPLES = ["삼성전자", "SK하이닉스", "현대차", "NAVER", "카카오", "KB금융"]
ecols = st.columns(len(EXAMPLES))
for c, ex in zip(ecols, EXAMPLES):
    if c.button(ex, use_container_width=True):
        st.session_state.q = ex

# ----- 사이드바: 가중치 조정 -----
with st.sidebar:
    st.header("⚙️ 평가 가중치")
    st.caption("기본값은 밸류 45% · 퀄리티 45% · 모멘텀 10%")
    w_per = st.slider("PER(저평가)", 0.0, 0.4, 0.18, 0.01)
    w_pbr = st.slider("PBR(저평가)", 0.0, 0.4, 0.17, 0.01)
    w_div = st.slider("배당수익률", 0.0, 0.4, 0.10, 0.01)
    w_roe = st.slider("ROE(수익성)", 0.0, 0.4, 0.22, 0.01)
    w_eg = st.slider("EPS 성장률", 0.0, 0.4, 0.09, 0.01)
    w_eq = st.slider("이익의 질", 0.0, 0.4, 0.06, 0.01)
    st.caption("심화 퀄리티(DART 연동 시)")
    w_debt = st.slider("부채비율(낮을수록↑)", 0.0, 0.4, 0.10, 0.01)
    w_opm = st.slider("영업이익률", 0.0, 0.4, 0.10, 0.01)
    w_rg = st.slider("매출성장률", 0.0, 0.4, 0.05, 0.01)
    w_mom = st.slider("모멘텀", 0.0, 0.4, 0.07, 0.01)
    weights = Weights(per=w_per, pbr=w_pbr, div=w_div, roe=w_roe,
                      eps_growth=w_eg, earnings_quality=w_eq,
                      debt_ratio=w_debt, op_margin=w_opm, rev_growth=w_rg,
                      momentum=w_mom)

    st.divider()
    st.subheader("🔑 DART 연동 (선택)")
    st.caption("키 입력 시 부채비율·영업이익률·매출성장 추가 분석. "
               "[무료 인증키 신청](https://opendart.fss.or.kr/)")
    dart_key = st.text_input("DART 인증키", type="password", placeholder="40자리 키")

query = st.text_input("회사명 또는 종목코드", key="q", placeholder="예: 삼성전자 / 005930")
go = st.button("🔍 분석하기", type="primary", use_container_width=True)
auto = bool(query.strip()) and query.strip() in EXAMPLES and not go


@st.cache_data(show_spinner=False, ttl=3600)
def run(query: str, dart_key: str):
    """데이터 수집만 캐시(점수는 가중치에 따라 매번 재계산)."""
    m, snap, msg = ds.fetch_metrics(query, dart_key=dart_key or None)
    if m is None:
        return None, msg
    return (m, snap, msg), None


if (go or auto) and query.strip():
    with st.spinner("KRX·네이버·DART에서 데이터 수집 중..."):
        try:
            res, err = run(query.strip(), dart_key)
        except Exception as e:
            res, err = None, f"데이터 수집 오류: {e}\n(외부 인터넷이 열린 환경에서 실행했는지 확인하세요.)"

    if err:
        st.error(err)
    else:
        m, snap, msg = res
        a = analyze(m, snap, weights=weights)  # 사이드바 가중치 반영
        color = RATING_COLOR.get(a.rating_kor, "#444")

        st.markdown(
            f"""
            <div style="border-radius:14px;padding:22px;background:{color};color:white;text-align:center;">
              <div style="font-size:15px;opacity:.85;">{m.name} ({m.code})</div>
              <div style="font-size:42px;font-weight:800;margin:6px 0;">{a.rating_kor}</div>
              <div style="font-size:14px;opacity:.9;">{a.rating_eng} · 종합점수 {a.composite:.1f} / 100</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.caption(msg)

        # 5단계 스펙트럼 표시
        st.write("")
        cols = st.columns(5)
        for col, (kor, eng, low) in zip(cols, RATINGS):
            on = (kor == a.rating_kor)
            col.markdown(
                f"<div style='text-align:center;padding:6px;border-radius:8px;"
                f"background:{RATING_COLOR[kor] if on else '#eee'};"
                f"color:{'white' if on else '#888'};font-size:12px;font-weight:{700 if on else 400};'>"
                f"{kor}<br><span style='font-size:10px;'>≥{low}</span></div>",
                unsafe_allow_html=True,
            )

        # 팩터 분해표
        st.subheader("📋 팩터별 점수")
        rows = []
        for f in a.factors:
            raw = "-" if f.raw is None else (f"{f.raw:.2f}" if isinstance(f.raw, float) else str(f.raw))
            rows.append({
                "팩터": f.label, "원시값": raw,
                "점수(0~100)": round(f.score, 1),
                "가중치": f"{f.weight*100:.0f}%",
                "기여": round(f.score * f.weight, 1),
                "설명": f.note,
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # 핵심 지표 요약
        st.subheader("🔢 핵심 지표")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("현재가", f"{m.price:,.0f}원" if m.price else "-")
        c2.metric("PER", f"{m.per:.1f}" if m.per else "-")
        c3.metric("PBR", f"{m.pbr:.2f}" if m.pbr else "-")
        c4.metric("배당수익률", f"{m.div:.2f}%" if m.div is not None else "-")

        if any(v is not None for v in (m.debt_ratio, m.op_margin, m.rev_growth, m.net_margin)):
            d1, d2, d3, d4 = st.columns(4)
            d1.metric("부채비율", f"{m.debt_ratio:.0f}%" if m.debt_ratio is not None else "-")
            d2.metric("영업이익률", f"{m.op_margin:.1f}%" if m.op_margin is not None else "-")
            d3.metric("매출성장률", f"{m.rev_growth:+.1f}%" if m.rev_growth is not None else "-")
            d4.metric("순이익률", f"{m.net_margin:.1f}%" if m.net_margin is not None else "-")

        st.info(
            "**해석 가이드**  점수는 *시장 전체 분포 대비 상대 위치*입니다. "
            "예) PER 점수 80 = 시장에서 저평가 상위 20%권. "
            "ROE는 PBR/PER 항등식으로 도출한 근사치입니다."
        )

st.divider()
st.caption(
    "⚠️ 본 도구는 공개 데이터에 기반한 **참고용 정량 분석**이며 투자 권유가 아닙니다. "
    "재무제표 주석·산업 동향·질적 요인은 반영되지 않습니다. 투자 판단과 책임은 본인에게 있습니다."
)
