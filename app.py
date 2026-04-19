"""
텐배거 후보 스크리너 (KOSPI/KOSDAQ)
- 시총 < 1조, 최근 3년 CAGR 기반 2028E 영업이익 ≥ 2,000억,
  매출/영업이익이 기준연도 대비 2배 이상 성장 예상 종목 필터링.
"""
import re
from datetime import datetime, timedelta

import FinanceDataReader as fdr
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from pykrx import stock

st.set_page_config(page_title="텐배거 후보 스크리너", layout="wide", page_icon="📈")


# ────────────────────────── 데이터 수집 ──────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def get_listings_with_cap() -> pd.DataFrame:
    """KRX 전체 상장 종목 + 당일(영업일) 시가총액."""
    day = datetime.today()
    df_kospi = df_kosdaq = pd.DataFrame()
    # 휴장일 보정: 최근 7영업일까지 역순 시도
    for _ in range(7):
        ymd = day.strftime("%Y%m%d")
        try:
            df_kospi = stock.get_market_cap_by_ticker(ymd, market="KOSPI")
            df_kosdaq = stock.get_market_cap_by_ticker(ymd, market="KOSDAQ")
            if not df_kospi.empty:
                break
        except Exception:
            pass
        day -= timedelta(days=1)

    df_kospi["Market"] = "KOSPI"
    df_kosdaq["Market"] = "KOSDAQ"
    cap = (
        pd.concat([df_kospi, df_kosdaq])
        .reset_index()
        .rename(columns={"티커": "Code", "시가총액": "MarketCap"})
    )

    names = pd.concat(
        [fdr.StockListing("KOSPI")[["Code", "Name"]],
         fdr.StockListing("KOSDAQ")[["Code", "Name"]]],
        ignore_index=True,
    )
    return cap.merge(names, on="Code", how="left")[["Code", "Name", "Market", "MarketCap"]]


@st.cache_data(ttl=86400, show_spinner=False)
def get_financial_history(code: str):
    """네이버 금융 '기업실적분석' 표에서 최근 연간 매출/영업이익 추출 (단위: 억원)."""
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.select_one("div.section.cop_analysis table")
        if table is None:
            return None

        # 연간 컬럼만 식별 (예: 2022.12, 2023.12, 2024.12, 2025.12(E))
        headers_txt = [th.get_text(strip=True) for th in table.select("thead th")]
        year_idx = [i for i, h in enumerate(headers_txt) if re.match(r"\d{4}\.\d{2}", h)][:4]

        rows = {}
        for tr in table.select("tbody tr"):
            th = tr.select_one("th")
            if not th:
                continue
            label = th.get_text(strip=True)
            tds = [td.get_text(strip=True).replace(",", "") for td in tr.select("td")]
            rows[label] = tds

        def pick(label):
            vals = []
            for i in year_idx:
                # tbody td는 헤더 첫 칸(th)을 제외 → 인덱스 보정
                data_i = i - 1 if len(headers_txt) > len(rows.get(label, [])) else i
                try:
                    v = rows[label][data_i]
                    vals.append(float(v) if v not in ("", "-", "N/A") else None)
                except (KeyError, IndexError, ValueError):
                    vals.append(None)
            return vals

        return {
            "years": [headers_txt[i] for i in year_idx],
            "revenue": pick("매출액"),
            "op_profit": pick("영업이익"),
        }
    except Exception:
        return None


# ────────────────────────── 추정 로직 ──────────────────────────
def cagr(start, end, n):
    if start is None or end is None or start <= 0 or end <= 0 or n <= 0:
        return None
    return (end / start) ** (1 / n) - 1


def project(base, g, years=3):
    if base is None or g is None:
        return None
    return base * (1 + g) ** years


# ────────────────────────── UI ──────────────────────────
st.title("📈 텐배거 후보 스크리너 — KOSPI / KOSDAQ")
st.caption("최근 3년 CAGR로 추정한 3년 뒤 실적이 2배 이상 성장할 중소형주를 발굴합니다.")

with st.sidebar:
    st.header("⚙️ 필터 조건")
    cap_max = st.slider("현재 시가총액 상한 (조원)", 0.1, 5.0, 1.0, 0.1)
    op_min = st.slider("3년 뒤 영업이익 하한 (억원)", 500, 5000, 2000, 100)
    mult = st.slider("매출·영업이익 성장 배수 (3년 뒤 / 기준연도)", 1.5, 5.0, 2.0, 0.1)
    base_label = st.text_input("기준연도 라벨", "2025")
    target_label = st.text_input("목표연도 라벨", "2028")
    limit = st.slider("크롤링 대상 종목 수 (속도 조절)", 50, 1500, 300, 50)
    run = st.button("🔍 스크리닝 실행", type="primary", use_container_width=True)

st.markdown(
    f"**조건** ① 시총 < **{cap_max}조** ② {target_label}E 영업이익 ≥ **{op_min:,}억** "
    f"③ 매출·영업이익 모두 {base_label}E 대비 **{mult}배 이상**"
)

if not run:
    st.info("👈 사이드바에서 조건을 설정한 뒤 '스크리닝 실행'을 눌러주세요.")
    st.stop()

with st.spinner("KRX 시가총액 수집 중..."):
    listings = get_listings_with_cap()

cap_thresh = cap_max * 1e12
small_caps = listings[listings["MarketCap"] < cap_thresh].copy()
target = small_caps.sort_values("MarketCap", ascending=False).head(limit)
st.info(f"전체 {len(listings):,}개 → 시총 {cap_max}조 미만 {len(small_caps):,}개 → 분석 {len(target)}개")

progress = st.progress(0.0, text="재무 데이터 수집 중...")
records = []
for i, row in enumerate(target.itertuples(index=False), 1):
    progress.progress(i / len(target), text=f"{i}/{len(target)} · {row.Name}")
    fin = get_financial_history(row.Code)
    if not fin:
        continue
    rev_v = [v for v in fin["revenue"] if v is not None]
    op_v = [v for v in fin["op_profit"] if v is not None]
    if len(rev_v) < 3 or len(op_v) < 3:
        continue

    rev_base, op_base = rev_v[-1], op_v[-1]
    rev_cagr = cagr(rev_v[0], rev_base, len(rev_v) - 1)
    op_cagr = cagr(op_v[0], op_base, len(op_v) - 1)
    rev_t, op_t = project(rev_base, rev_cagr), project(op_base, op_cagr)
    if rev_t is None or op_t is None or rev_base <= 0 or op_base <= 0:
        continue

    records.append({
        "종목코드": row.Code,
        "종목명": row.Name,
        "시장": row.Market,
        "시총(억)": round(row.MarketCap / 1e8),
        f"매출({base_label}E,억)": round(rev_base),
        f"영업이익({base_label}E,억)": round(op_base),
        "매출CAGR(%)": round(rev_cagr * 100, 1) if rev_cagr else None,
        "영업이익CAGR(%)": round(op_cagr * 100, 1) if op_cagr else None,
        f"매출({target_label}E,억)": round(rev_t),
        f"영업이익({target_label}E,억)": round(op_t),
        "매출성장배수": round(rev_t / rev_base, 2),
        "영업이익성장배수": round(op_t / op_base, 2),
    })
progress.empty()

if not records:
    st.warning("재무 데이터를 충분히 수집하지 못했습니다. 분석 종목 수를 늘려보세요.")
    st.stop()

df = pd.DataFrame(records)
op_col = f"영업이익({target_label}E,억)"
mask = (df[op_col] >= op_min) & (df["매출성장배수"] >= mult) & (df["영업이익성장배수"] >= mult)
final = df[mask].sort_values("영업이익성장배수", ascending=False).reset_index(drop=True)

st.subheader(f"🏆 최종 후보 {len(final)}개")
st.dataframe(final, use_container_width=True)

with st.expander("📊 분석된 전체 종목 (필터 전)"):
    st.dataframe(df.sort_values("영업이익성장배수", ascending=False), use_container_width=True)

st.download_button(
    "📥 결과 CSV 다운로드",
    data=final.to_csv(index=False).encode("utf-8-sig"),
    file_name=f"tenbagger_candidates_{datetime.now():%Y%m%d}.csv",
    mime="text/csv",
)
st.caption("⚠️ 본 정보는 투자 참고용이며, 추정치는 과거 CAGR 단순 외삽이라 한계가 있습니다.")
