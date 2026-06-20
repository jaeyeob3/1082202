"""
scoring.py - 밸류+퀄리티 종합 점수화 및 5단계 투자등급 엔진 (순수 함수, 네트워크 불필요)

핵심: 각 지표를 '시장 전체 분포 대비 백분위'로 0~100 환산(상대평가) 후 가중합 →
5단계 등급(적극매수/매수/중립/매도/적극매도). DART 심화지표는 있으면 추가, 없으면 자동 제외.
ROE는 PBR/PER 항등식으로 도출.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Sequence
import math

RATINGS = [
    ("적극매수", "Strong Buy", 75),
    ("매수", "Buy", 60),
    ("중립", "Hold", 40),
    ("매도", "Sell", 25),
    ("적극매도", "Strong Sell", 0),
]


def score_to_rating(score):
    for kor, eng, low in RATINGS:
        if score >= low:
            return kor, eng
    return "적극매도", "Strong Sell"


@dataclass
class Weights:
    """상대 가중치. 집계 시 데이터가 있는 팩터만 모아 재정규화."""
    per: float = 0.15
    pbr: float = 0.14
    div: float = 0.08
    roe: float = 0.16
    eps_growth: float = 0.09
    earnings_quality: float = 0.06
    debt_ratio: float = 0.10
    op_margin: float = 0.10
    rev_growth: float = 0.05
    momentum: float = 0.07

    def get(self, key):
        return getattr(self, key, 0.0)


def percentile_rank(value, distribution: Sequence):
    vals = [v for v in distribution if v is not None and not _isnan(v)]
    if not vals or value is None or _isnan(value):
        return 50.0
    below = sum(1 for v in vals if v < value)
    equal = sum(1 for v in vals if v == value)
    return max(0.0, min(100.0, (below + 0.5 * equal) / len(vals) * 100.0))


def _isnan(x):
    try:
        return math.isnan(x)
    except (TypeError, ValueError):
        return False


def _clip(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


@dataclass
class StockMetrics:
    name: str
    code: str
    per: Optional[float] = None
    pbr: Optional[float] = None
    div: Optional[float] = None
    roe: Optional[float] = None
    eps: Optional[float] = None
    eps_prev: Optional[float] = None
    ret_6m: Optional[float] = None
    pos_52w: Optional[float] = None
    price: Optional[float] = None
    debt_ratio: Optional[float] = None   # DART
    op_margin: Optional[float] = None    # DART
    rev_growth: Optional[float] = None   # DART
    net_margin: Optional[float] = None   # DART (표시용)


@dataclass
class MarketSnapshot:
    per: list = field(default_factory=list)
    pbr: list = field(default_factory=list)
    div: list = field(default_factory=list)
    roe: list = field(default_factory=list)
    ret_6m: list = field(default_factory=list)


@dataclass
class FactorScore:
    key: str
    label: str
    raw: Optional[float]
    score: float
    weight: float
    note: str = ""


@dataclass
class Analysis:
    metrics: StockMetrics
    factors: list
    composite: float
    rating_kor: str
    rating_eng: str

    def to_dict(self):
        return {
            "name": self.metrics.name, "code": self.metrics.code,
            "composite": round(self.composite, 1),
            "rating_kor": self.rating_kor, "rating_eng": self.rating_eng,
            "factors": [
                {"key": f.key, "label": f.label, "raw": f.raw,
                 "score": round(f.score, 1), "weight": round(f.weight, 3),
                 "note": f.note} for f in self.factors],
        }


def derive_roe(m):
    """ROE(%) = EPS/BPS = PBR/PER (PER>0)."""
    if m.roe is not None and not _isnan(m.roe):
        return m.roe
    if m.per and m.pbr and m.per > 0:
        return (m.pbr / m.per) * 100.0
    return None


def analyze(m, market, weights=None):
    w = weights or Weights()
    specs = []  # (key, label, raw, score, note, include)

    if m.per and m.per > 0:
        pr = percentile_rank(m.per, market.per)
        specs.append(("per", "PER(주가수익비율)", m.per, 100.0 - pr,
                      f"시장 PER 백분위 {pr:.0f}% (낮을수록 저평가)", True))
    else:
        specs.append(("per", "PER(주가수익비율)", m.per, 30.0, "PER 적자/결측 → 감점", True))

    if m.pbr and m.pbr > 0:
        pr = percentile_rank(m.pbr, market.pbr)
        specs.append(("pbr", "PBR(주가순자산비율)", m.pbr, 100.0 - pr,
                      f"시장 PBR 백분위 {pr:.0f}% (낮을수록 저평가)", True))
    else:
        specs.append(("pbr", "PBR(주가순자산비율)", m.pbr, 40.0, "PBR 결측 → 중립", True))

    if m.div is not None and not _isnan(m.div):
        dr = percentile_rank(m.div, market.div) if market.div else min(m.div * 20, 100)
        specs.append(("div", "배당수익률", m.div, dr,
                      f"배당 {m.div:.2f}% (백분위 {dr:.0f}%)", True))
    else:
        specs.append(("div", "배당수익률", m.div, 50.0, "배당 정보 없음 → 중립", True))

    roe = derive_roe(m)
    if roe is not None and not _isnan(roe):
        rr = percentile_rank(roe, market.roe) if market.roe else _clip(roe * 4)
        specs.append(("roe", "ROE(자기자본이익률)", roe, rr,
                      f"ROE {roe:.1f}% (백분위 {rr:.0f}%)", True))
    else:
        specs.append(("roe", "ROE(자기자본이익률)", roe, 35.0, "ROE 산출 불가 → 감점", True))

    if m.eps is not None and m.eps_prev not in (None, 0) and not _isnan(m.eps):
        g = (m.eps - m.eps_prev) / abs(m.eps_prev) * 100.0
        specs.append(("eps_growth", "EPS 성장률", g, _clip(50.0 + g),
                      f"EPS 성장률 {g:+.1f}% (YoY)", True))
    else:
        specs.append(("eps_growth", "EPS 성장률", None, 50.0, "산출 불가 → 중립", True))

    if m.eps is not None and not _isnan(m.eps):
        sc, nt = (80.0, "흑자(EPS>0)") if m.eps > 0 else (15.0, "적자(EPS≤0) → 감점")
        specs.append(("earnings_quality", "이익의 질", m.eps, sc, nt, True))
    else:
        specs.append(("earnings_quality", "이익의 질", m.eps, 50.0, "정보 없음 → 중립", True))

    if m.debt_ratio is not None and not _isnan(m.debt_ratio):
        specs.append(("debt_ratio", "부채비율", m.debt_ratio, _clip(100.0 - m.debt_ratio / 3.0),
                      f"부채비율 {m.debt_ratio:.0f}% (낮을수록 안정)", True))
    else:
        specs.append(("debt_ratio", "부채비율", None, 50.0, "DART 미연동 → 제외", False))

    if m.op_margin is not None and not _isnan(m.op_margin):
        specs.append(("op_margin", "영업이익률", m.op_margin, _clip(40.0 + m.op_margin * 2.5),
                      f"영업이익률 {m.op_margin:.1f}%", True))
    else:
        specs.append(("op_margin", "영업이익률", None, 50.0, "DART 미연동 → 제외", False))

    if m.rev_growth is not None and not _isnan(m.rev_growth):
        specs.append(("rev_growth", "매출성장률", m.rev_growth, _clip(50.0 + m.rev_growth * 1.5),
                      f"매출성장률 {m.rev_growth:+.1f}% (YoY)", True))
    else:
        specs.append(("rev_growth", "매출성장률", None, 50.0, "DART 미연동 → 제외", False))

    mom = []
    if m.ret_6m is not None and not _isnan(m.ret_6m):
        mom.append(percentile_rank(m.ret_6m, market.ret_6m) if market.ret_6m else _clip(50 + m.ret_6m))
    if m.pos_52w is not None and not _isnan(m.pos_52w):
        mom.append(_clip(m.pos_52w * 100))
    mom_score = sum(mom) / len(mom) if mom else 50.0
    mom_note = f"6개월 수익률 {m.ret_6m:+.1f}%" if m.ret_6m is not None else "모멘텀 정보 제한"
    specs.append(("momentum", "모멘텀", m.ret_6m, mom_score, mom_note, True))

    incl = [s for s in specs if s[5]]
    wsum = sum(w.get(s[0]) for s in incl) or 1.0
    factors = [FactorScore(k, lb, rw, sc, w.get(k) / wsum, nt)
               for (k, lb, rw, sc, nt, _) in incl]
    composite = sum(f.score * f.weight for f in factors)
    kor, eng = score_to_rating(composite)
    return Analysis(m, factors, composite, kor, eng)
