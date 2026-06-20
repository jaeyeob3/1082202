"""
data_source.py — 한국 주식 재무지표 자동 수집

데이터 출처(모두 무료, API 키 불필요):
- pykrx: KRX에서 PER/PBR/배당수익률/EPS/BPS 등 펀더멘탈 스냅샷
- FinanceDataReader: 종목 마스터(이름↔코드) 및 주가 시계열

주의: 이 모듈은 인터넷 접속이 필요하다. KRX/네이버 서버에 직접 접속하므로
반드시 외부망이 열린 환경(사용자 PC)에서 실행해야 한다.

캐싱: 종목 마스터와 시장 스냅샷은 streamlit 캐시로 1회만 받아 재사용한다.
"""

from __future__ import annotations
import datetime as dt
from typing import Optional
import functools

import pandas as pd

# 무거운 외부 라이브러리는 import 실패가 앱 전체를 죽이지 않도록 보호한다.
try:
    from pykrx import stock
    import FinanceDataReader as fdr
    _IMPORT_ERROR = None
except Exception as _e:  # noqa
    stock = None
    fdr = None
    _IMPORT_ERROR = _e

from scoring import StockMetrics, MarketSnapshot


# ----------------------------- 영업일 계산 -----------------------------
def latest_business_day() -> str:
    """가장 최근 거래일(YYYYMMDD). pykrx가 직전 영업일 데이터를 보유."""
    d = dt.date.today()
    # 주말이면 금요일로 당김. pykrx는 미반영일이면 빈 DF를 주므로 호출측에서 보정.
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.strftime("%Y%m%d")


def _recent_valid_day(max_back: int = 10) -> str:
    """실데이터가 존재하는 최근 영업일을 탐색."""
    d = dt.date.today()
    for _ in range(max_back):
        if d.weekday() < 5:
            ymd = d.strftime("%Y%m%d")
            try:
                df = stock.get_market_fundamental(ymd, market="ALL")
                if df is not None and len(df) > 0:
                    return ymd
            except Exception:
                pass
        d -= dt.timedelta(days=1)
    return latest_business_day()


# ----------------------------- 종목 마스터 -----------------------------
@functools.lru_cache(maxsize=1)
def load_listing() -> pd.DataFrame:
    """KRX 전체 상장 종목(코드/이름)."""
    df = fdr.StockListing("KRX")
    # 컬럼명이 버전에 따라 다름 → 표준화
    cols = {c.lower(): c for c in df.columns}
    code_col = cols.get("code") or cols.get("symbol")
    name_col = cols.get("name")
    out = df[[code_col, name_col]].copy()
    out.columns = ["code", "name"]
    out["code"] = out["code"].astype(str).str.zfill(6)
    return out


def resolve_company(query: str) -> Optional[tuple[str, str]]:
    """
    회사명 또는 종목코드 → (코드, 정식명) 반환.
    - 6자리 숫자면 코드로 간주.
    - 그 외엔 이름 부분일치(정확일치 우선).
    """
    query = query.strip()
    listing = load_listing()
    if query.isdigit() and len(query) == 6:
        row = listing[listing["code"] == query]
        if len(row):
            return row.iloc[0]["code"], row.iloc[0]["name"]
        return query, query
    # 정확 일치
    exact = listing[listing["name"] == query]
    if len(exact):
        return exact.iloc[0]["code"], exact.iloc[0]["name"]
    # 부분 일치(짧은 이름 우선)
    part = listing[listing["name"].str.contains(query, case=False, na=False)]
    if len(part):
        part = part.assign(_l=part["name"].str.len()).sort_values("_l")
        return part.iloc[0]["code"], part.iloc[0]["name"]
    return None


# ----------------------------- 시장 스냅샷 -----------------------------
@functools.lru_cache(maxsize=4)
def market_snapshot(ymd: str) -> tuple[MarketSnapshot, "pd.DataFrame"]:
    """시장 전체 종목의 PER/PBR/DIV/ROE 분포."""
    df = stock.get_market_fundamental(ymd, market="ALL")  # index=코드
    df = df.copy()
    # ROE 도출: ROE = PBR/PER*100 (PER>0)
    df["ROE"] = df.apply(
        lambda r: (r["PBR"] / r["PER"] * 100.0) if r["PER"] and r["PER"] > 0 else None,
        axis=1,
    )
    snap = MarketSnapshot(
        per=[v for v in df["PER"].tolist() if v and v > 0],
        pbr=[v for v in df["PBR"].tolist() if v and v > 0],
        div=[v for v in df["DIV"].tolist() if v is not None],
        roe=[v for v in df["ROE"].tolist() if v is not None],
        ret_6m=[],  # 모멘텀은 종목 단위로만 계산(전시장 6M은 비용↑)
    )
    return snap, df


# ----------------------------- 가격/모멘텀 -----------------------------
def price_features(code: str) -> dict:
    """6개월 수익률 및 52주 가격대 위치."""
    end = dt.date.today()
    start = end - dt.timedelta(days=400)
    try:
        px = fdr.DataReader(code, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    except Exception:
        return {"price": None, "ret_6m": None, "pos_52w": None}
    if px is None or len(px) == 0:
        return {"price": None, "ret_6m": None, "pos_52w": None}
    close = px["Close"].dropna()
    last = float(close.iloc[-1])
    # 6개월(약 126 거래일) 수익률
    ret_6m = None
    if len(close) > 126:
        ref = float(close.iloc[-126])
        if ref > 0:
            ret_6m = (last / ref - 1) * 100.0
    # 52주 위치
    w52 = close.iloc[-252:] if len(close) >= 252 else close
    lo, hi = float(w52.min()), float(w52.max())
    pos = (last - lo) / (hi - lo) if hi > lo else 0.5
    return {"price": last, "ret_6m": ret_6m, "pos_52w": pos}


# ----------------------------- EPS 성장률 -----------------------------
def eps_growth_features(code: str, ymd: str) -> dict:
    """현재 EPS와 약 1년 전 EPS를 비교(YoY 근사)."""
    try:
        cur = stock.get_market_fundamental(ymd, market="ALL").loc[code]
        eps_now = float(cur.get("EPS")) if cur.get("EPS") else None
    except Exception:
        eps_now = None
    # 1년 전 영업일
    y = dt.datetime.strptime(ymd, "%Y%m%d").date() - dt.timedelta(days=365)
    eps_prev = None
    for _ in range(7):
        if y.weekday() < 5:
            try:
                prev = stock.get_market_fundamental(y.strftime("%Y%m%d"), market="ALL")
                if code in prev.index and prev.loc[code].get("EPS"):
                    eps_prev = float(prev.loc[code]["EPS"])
                    break
            except Exception:
                pass
        y -= dt.timedelta(days=1)
    return {"eps": eps_now, "eps_prev": eps_prev}


# ----------------------------- 통합 수집 -----------------------------
def fetch_metrics(query: str, dart_key: Optional[str] = None
                  ) -> tuple[Optional[StockMetrics], Optional[MarketSnapshot], str]:
    """
    회사명/코드를 받아 StockMetrics와 MarketSnapshot을 반환.
    dart_key가 주어지면 DART에서 부채비율/영업이익률/매출성장을 추가로 채운다.
    반환: (metrics, market, message). 실패 시 metrics=None.
    """
    if _IMPORT_ERROR is not None:
        return None, None, (
            "데이터 라이브러리(pykrx/FinanceDataReader) 로드에 실패했습니다. "
            "터미널에서 'pip install -r requirements.txt' 를 다시 실행해 주세요. "
            f"(원인: {_IMPORT_ERROR})"
        )
    resolved = resolve_company(query)
    if not resolved:
        return None, None, f"'{query}'에 해당하는 상장 종목을 찾지 못했습니다."
    code, name = resolved

    ymd = _recent_valid_day()
    snap, df = market_snapshot(ymd)

    if code not in df.index:
        return None, None, f"{name}({code})의 펀더멘탈 데이터가 없습니다(상장폐지/거래정지 가능)."

    row = df.loc[code]
    px = price_features(code)
    epsf = eps_growth_features(code, ymd)

    m = StockMetrics(
        name=name, code=code,
        per=_num(row.get("PER")),
        pbr=_num(row.get("PBR")),
        div=_num(row.get("DIV")),
        eps=epsf["eps"], eps_prev=epsf["eps_prev"],
        ret_6m=px["ret_6m"], pos_52w=px["pos_52w"], price=px["price"],
    )

    msg = f"기준일 {ymd} · {name}({code})"
    if dart_key:
        try:
            import dart
            fin = dart.latest_financials(code, dart_key)
            if fin:
                m.debt_ratio = fin.get("debt_ratio")
                m.op_margin = fin.get("op_margin")
                m.rev_growth = fin.get("rev_growth")
                m.net_margin = fin.get("net_margin")
                msg += f" · DART {fin.get('bsns_year')}년 재무 반영"
            else:
                msg += " · DART 재무 미발견(기본 지표만)"
        except Exception as e:
            msg += f" · DART 오류({str(e)[:40]})"
    return m, snap, msg


def _num(x):
    try:
        if x is None:
            return None
        x = float(x)
        return x if x == x else None  # NaN 제거
    except (TypeError, ValueError):
        return None
