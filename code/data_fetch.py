# -*- coding: utf-8 -*-
"""
data_fetch.py — 抓取中美利率决定机制研究所需的宏观与利率数据。
Fetch US (FRED, no API key) and China (akshare) macro / rate series.

输出 / Outputs: data/*.csv

数据频率说明：
- 美国：月度为主（FEDFUNDS, CPI, 失业率），日度国债收益率重采样为月末。
- 中国：DR007/SHIBOR 日度→月度；LPR/MLF/OMO 政策利率；CPI、GDP。
"""
import os
import io
import time
import urllib.request
import warnings

import pandas as pd

warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
os.makedirs(DATA, exist_ok=True)


# ----------------------------------------------------------------------------
# 1. 美国数据 — FRED 公共 CSV 端点（无需 API key）
# ----------------------------------------------------------------------------
FRED_SERIES = {
    "FEDFUNDS": "fed_funds_effective",      # 联邦基金有效利率（月，%）
    "DFF": "fed_funds_daily",               # 联邦基金日度
    "DGS3MO": "ust_3m",                     # 3个月国债收益率（日，%）
    "DGS2": "ust_2y",                       # 2年
    "DGS10": "ust_10y",                     # 10年
    "DGS30": "ust_30y",                     # 30年
    "T10Y2Y": "ust_10y2y_spread",           # 10Y-2Y 利差
    "CPIAUCSL": "cpi",                      # CPI 总指数（月，SA）
    "PCEPILFE": "core_pce",                 # 核心 PCE 价格指数（月，SA）
    "UNRATE": "unemployment",               # 失业率（月，%）
    "GDPC1": "real_gdp",                    # 实际 GDP（季，链式）
    "GDPPOT": "potential_gdp",              # 潜在 GDP（季，CBO）
    "T10YIE": "breakeven_10y",              # 10年盈亏平衡通胀
    "DTWEXBGS": "usd_broad_index",          # 美元广义指数
}


def fetch_fred(series_id: str) -> pd.Series:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    for attempt in range(3):
        try:
            raw = urllib.request.urlopen(url, timeout=30).read().decode("utf-8")
            df = pd.read_csv(io.StringIO(raw))
            df.columns = ["date", "value"]
            df["date"] = pd.to_datetime(df["date"])
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            return df.set_index("date")["value"].dropna()
        except Exception as e:  # noqa
            print(f"  [retry {attempt+1}] {series_id}: {e}")
            time.sleep(2)
    return pd.Series(dtype=float)


def fetch_us():
    print("== 抓取美国数据 (FRED) ==")
    out = {}
    for sid, name in FRED_SERIES.items():
        s = fetch_fred(sid)
        if not s.empty:
            out[name] = s
            print(f"  OK {sid:10s} -> {name:22s} n={len(s):5d}  {s.index.min().date()}..{s.index.max().date()}")
        else:
            print(f"  FAIL {sid}")
    if out:
        df = pd.DataFrame(out)
        df.to_csv(os.path.join(DATA, "us_raw.csv"))
        print(f"  saved -> data/us_raw.csv shape={df.shape}")
    return out


# ----------------------------------------------------------------------------
# 2. 中国数据 — akshare
# ----------------------------------------------------------------------------
def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa
        print(f"  akshare FAIL {getattr(fn,'__name__',fn)}: {repr(e)[:100]}")
        return None


def fetch_cn():
    print("== 抓取中国数据 (akshare) ==")
    import akshare as ak

    # 2.1 LPR 历史（含 1Y、5Y；早期含一年期贷款基准、Shibor 等列）
    lpr = _safe(ak.macro_china_lpr)
    if lpr is not None:
        lpr.to_csv(os.path.join(DATA, "cn_lpr.csv"), index=False, encoding="utf-8-sig")
        print(f"  OK LPR shape={lpr.shape} cols={list(lpr.columns)[:6]}")

    # 2.2 中国国债收益率曲线（日度，多期限）
    ytd = _safe(ak.bond_china_yield, start_date="20070101")
    if ytd is not None:
        ytd.to_csv(os.path.join(DATA, "cn_bond_yield.csv"), index=False, encoding="utf-8-sig")
        print(f"  OK CN bond yield shape={ytd.shape} cols={list(ytd.columns)[:8]}")

    # 2.3 银行间质押式回购 / SHIBOR（DR007 代理）
    # 优先 DR 系列；若失败则用 SHIBOR / 银行间拆借
    shibor = _safe(ak.rate_interbank, market="上海银行同业拆借市场", symbol="Shibor人民币", indicator="1周")
    if shibor is not None:
        shibor.to_csv(os.path.join(DATA, "cn_shibor_1w.csv"), index=False, encoding="utf-8-sig")
        print(f"  OK SHIBOR 1W shape={shibor.shape}")
    shibor_on = _safe(ak.rate_interbank, market="上海银行同业拆借市场", symbol="Shibor人民币", indicator="隔夜")
    if shibor_on is not None:
        shibor_on.to_csv(os.path.join(DATA, "cn_shibor_on.csv"), index=False, encoding="utf-8-sig")
        print(f"  OK SHIBOR ON shape={shibor_on.shape}")

    # 2.4 中国 CPI（月度同比）
    cpi = _safe(ak.macro_china_cpi_monthly)
    if cpi is not None:
        cpi = pd.DataFrame(cpi)
        cpi.to_csv(os.path.join(DATA, "cn_cpi.csv"), index=False, encoding="utf-8-sig")
        print(f"  OK CN CPI monthly shape={cpi.shape}")
    cpi2 = _safe(ak.macro_china_cpi)
    if cpi2 is not None:
        cpi2.to_csv(os.path.join(DATA, "cn_cpi_table.csv"), index=False, encoding="utf-8-sig")
        print(f"  OK CN CPI table shape={cpi2.shape}")

    # 2.5 中国 GDP（季度同比）
    gdp = _safe(ak.macro_china_gdp_yearly)
    if gdp is not None:
        gdp = pd.DataFrame(gdp)
        gdp.to_csv(os.path.join(DATA, "cn_gdp_yearly.csv"), index=False, encoding="utf-8-sig")
        print(f"  OK CN GDP yearly shape={gdp.shape}")

    # 2.6 公开市场操作 7天逆回购利率（政策利率锚）— 尝试多个接口
    omo = _safe(ak.macro_china_market_margin_sz)  # placeholder; real OMO below
    # 7天逆回购中标利率
    rev = _safe(ak.tool_china_repo) if hasattr(ak, "tool_china_repo") else None
    # 央行公开市场操作（含 7D 逆回购利率）
    omo2 = _safe(ak.macro_china_central_bank_balance) if hasattr(ak, "macro_china_central_bank_balance") else None
    if omo2 is not None:
        omo2.to_csv(os.path.join(DATA, "cn_pboc_balance.csv"), index=False, encoding="utf-8-sig")
        print(f"  OK PBOC balance shape={omo2.shape}")

    # 2.7 中美利差相关：人民币汇率
    fx = _safe(ak.macro_usd_cny) if hasattr(ak, "macro_usd_cny") else None
    fx2 = _safe(ak.currency_boc_safe) if hasattr(ak, "currency_boc_safe") else None
    if fx2 is not None:
        fx2.to_csv(os.path.join(DATA, "cn_fx_boc.csv"), index=False, encoding="utf-8-sig")
        print(f"  OK FX BOC shape={fx2.shape}")

    print("  中国数据抓取结束。")


if __name__ == "__main__":
    fetch_us()
    fetch_cn()
    print("\nDONE. 文件位于 data/ 目录。")
