"""
dart.py — DART 오픈API로 심화 재무지표 수집

제공 지표: 부채비율, 영업이익률, 매출성장률(YoY), 순이익률
필요: DART 오픈API 무료 키 (https://opendart.fss.or.kr → 인증키 신청).

흐름:
1) corpCode.xml(zip)을 받아 종목코드 → DART corp_code 매핑
2) 단일회사 전체 재무제표(fnlttSinglAcntAll)에서 매출/영업이익/순이익/부채/자본 추출
3) 비율 지표 산출

parse_financials()는 네트워크 없이 단위 테스트가 가능하도록 분리되어 있다.
"""

from __future__ import annotations
import io
import zipfile
import functools
import xml.etree.ElementTree as ET
from typing import Optional

import requests

DART_BASE = "https://opendart.fss.or.kr/api"
TIMEOUT = 30


# ----------------------------- corp_code 매핑 -----------------------------
@functools.lru_cache(maxsize=2)
def corp_code_map(api_key: str) -> dict:
    """종목코드(6자리) → DART corp_code(8자리) 매핑."""
    r = requests.get(f"{DART_BASE}/corpCode.xml",
                     params={"crtfc_key": api_key}, timeout=TIMEOUT)
    r.raise_for_status()
    # 키 오류 시 JSON 에러가 옴
    if r.headers.get("content-type", "").startswith("application/json"):
        raise RuntimeError(f"DART corpCode 오류: {r.text[:200]}")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    xml = z.read(z.namelist()[0])
    root = ET.fromstring(xml)
    m = {}
    for item in root.iter("list"):
        sc = (item.findtext("stock_code") or "").strip()
        cc = (item.findtext("corp_code") or "").strip()
        if sc and sc != " ":
            m[sc.zfill(6)] = cc
    return m


def corp_code_for(stock_code: str, api_key: str) -> Optional[str]:
    return corp_code_map(api_key).get(stock_code.zfill(6))


# ----------------------------- 숫자 파싱 -----------------------------
def _to_num(s) -> Optional[float]:
    if s in (None, "", "-"):
        return None
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


# ----------------------------- 재무제표 파싱 -----------------------------
def parse_financials(rows: list, year: int) -> dict:
    """
    fnlttSinglAcntAll의 list(계정 행 리스트)에서 비율 지표를 산출.
    각 행: account_nm, thstrm_amount(당기), frmtrm_amount(전기) 등.
    """
    acc = {}
    for row in rows:
        nm = (row.get("account_nm") or "").replace(" ", "")
        # 동일 계정명이 여러 재무제표(BS/IS)에 나올 수 있어 첫 등장만 사용
        acc.setdefault(nm, row)

    def cur(*names):
        for n in names:
            r = acc.get(n)
            if r is not None:
                v = _to_num(r.get("thstrm_amount"))
                if v is not None:
                    return v
        return None

    def prev(*names):
        for n in names:
            r = acc.get(n)
            if r is not None:
                v = _to_num(r.get("frmtrm_amount"))
                if v is not None:
                    return v
        return None

    revenue = cur("매출액", "수익(매출액)", "영업수익", "매출")
    revenue_prev = prev("매출액", "수익(매출액)", "영업수익", "매출")
    op = cur("영업이익", "영업이익(손실)")
    net = cur("당기순이익", "당기순이익(손실)", "당기순이익(손실)")
    liab = cur("부채총계")
    equity = cur("자본총계")

    debt_ratio = (liab / equity * 100.0) if liab is not None and equity not in (None, 0) else None
    op_margin = (op / revenue * 100.0) if op is not None and revenue not in (None, 0) else None
    net_margin = (net / revenue * 100.0) if net is not None and revenue not in (None, 0) else None
    rev_growth = ((revenue - revenue_prev) / abs(revenue_prev) * 100.0) \
        if revenue is not None and revenue_prev not in (None, 0) else None

    return {
        "debt_ratio": debt_ratio,
        "op_margin": op_margin,
        "net_margin": net_margin,
        "rev_growth": rev_growth,
        "bsns_year": year,
        "_raw": {"revenue": revenue, "op": op, "net": net,
                 "liab": liab, "equity": equity},
    }


# ----------------------------- 재무제표 수집 -----------------------------
def _fetch_one(corp_code: str, api_key: str, year: int, reprt: str = "11011") -> Optional[dict]:
    """연결(CFS) 우선, 없으면 별도(OFS) 재무제표 시도."""
    for fs_div in ("CFS", "OFS"):
        try:
            r = requests.get(f"{DART_BASE}/fnlttSinglAcntAll.json",
                             params={"crtfc_key": api_key, "corp_code": corp_code,
                                     "bsns_year": str(year), "reprt_code": reprt,
                                     "fs_div": fs_div}, timeout=TIMEOUT)
            j = r.json()
        except Exception:
            continue
        if j.get("status") == "000" and j.get("list"):
            return parse_financials(j["list"], year)
    return None


def latest_financials(stock_code: str, api_key: str,
                      try_years: Optional[list] = None) -> Optional[dict]:
    """
    종목코드로 최근 연간 사업보고서(11011) 재무비율을 반환.
    최근 연도부터 역순으로 시도(공시 시점 차이 보정).
    """
    import datetime as dt
    cc = corp_code_for(stock_code, api_key)
    if not cc:
        return None
    years = try_years or [dt.date.today().year - 1, dt.date.today().year - 2,
                          dt.date.today().year]
    for y in years:
        data = _fetch_one(cc, api_key, y)
        if data and any(data.get(k) is not None
                        for k in ("debt_ratio", "op_margin", "rev_growth")):
            return data
    return None
