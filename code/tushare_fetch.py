# -*- coding: utf-8 -*-
"""
tushare_fetch.py — 通过 Tushare Pro 拉取更丰富的中美宏观/利率数据。
Enrich the dataset via Tushare Pro: full US Treasury curve, all SHIBOR tenors,
LPR, China CPI/PPI, and money supply M0/M1/M2 (quantity channel).

Token 读取顺序：环境变量 TS_TOKEN > code/ts_token.txt（均不入库）。
输出 / Outputs: data/ts_*.csv
"""
import os
import time
import warnings

import pandas as pd

warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
os.makedirs(DATA, exist_ok=True)


def get_token():
    tok = os.environ.get("TS_TOKEN")
    if tok:
        return tok.strip()
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ts_token.txt")
    if os.path.exists(p):
        return open(p, encoding="utf-8").read().strip()
    raise RuntimeError("未找到 Tushare token：设置环境变量 TS_TOKEN 或创建 code/ts_token.txt")


import tushare as ts  # noqa: E402

ts.set_token(get_token())
PRO = ts.pro_api()


def _retry(fn, *a, **k):
    for i in range(4):
        try:
            return fn(*a, **k)
        except Exception as e:  # noqa
            msg = repr(e)
            if "每分钟" in msg or "minute" in msg or "频率" in msg:
                time.sleep(20)
            else:
                print(f"    retry{i+1}: {msg[:90]}")
                time.sleep(3)
    return None


def fetch_by_year(fn_name, start, end, date_kw=("start_date", "end_date"), **extra):
    """日度接口按年分段抓取。"""
    fn = getattr(PRO, fn_name)
    frames = []
    for y in range(start, end + 1):
        kw = {date_kw[0]: f"{y}0101", date_kw[1]: f"{y}1231"}
        kw.update(extra)
        df = _retry(fn, **kw)
        if df is not None and len(df):
            frames.append(df)
        time.sleep(0.4)
    if frames:
        return pd.concat(frames, ignore_index=True)
    return pd.DataFrame()


def fetch_by_month(fn_name, start_m, end_m):
    fn = getattr(PRO, fn_name)
    df = _retry(fn, start_m=start_m, end_m=end_m)
    return df if df is not None else pd.DataFrame()


def main():
    print("== Tushare 抓取 ==")

    # 1. 美国国债收益率曲线（完整期限 m1..y30）
    us = fetch_by_year("us_tycr", 1990, 2026)
    if len(us):
        us.to_csv(os.path.join(DATA, "ts_us_tycr.csv"), index=False, encoding="utf-8-sig")
        print(f"  us_tycr      {us.shape} {list(us.columns)}")

    # 2. SHIBOR 全期限（on..1y）
    sh = fetch_by_year("shibor", 2006, 2026)
    if len(sh):
        sh.to_csv(os.path.join(DATA, "ts_shibor.csv"), index=False, encoding="utf-8-sig")
        print(f"  shibor       {sh.shape} {list(sh.columns)}")

    # 3. LPR 1Y/5Y
    lpr = fetch_by_year("shibor_lpr", 2013, 2026)
    if len(lpr):
        lpr.to_csv(os.path.join(DATA, "ts_lpr.csv"), index=False, encoding="utf-8-sig")
        print(f"  shibor_lpr   {lpr.shape} {list(lpr.columns)}")

    # 4. 中国 CPI（全国同比/环比/累计）
    frames = []
    for y in range(1995, 2027):
        d = fetch_by_month("cn_cpi", f"{y}01", f"{y}12")
        if len(d):
            frames.append(d)
        time.sleep(0.3)
    if frames:
        cpi = pd.concat(frames, ignore_index=True)
        cpi.to_csv(os.path.join(DATA, "ts_cn_cpi.csv"), index=False, encoding="utf-8-sig")
        print(f"  cn_cpi       {cpi.shape}")

    # 5. 中国 PPI（生产者价格，含通缩信号）
    frames = []
    for y in range(2000, 2027):
        d = fetch_by_month("cn_ppi", f"{y}01", f"{y}12")
        if len(d):
            frames.append(d)
        time.sleep(0.3)
    if frames:
        ppi = pd.concat(frames, ignore_index=True)
        ppi.to_csv(os.path.join(DATA, "ts_cn_ppi.csv"), index=False, encoding="utf-8-sig")
        print(f"  cn_ppi       {ppi.shape}")

    # 6. 货币供应 M0/M1/M2（数量型渠道核心）
    frames = []
    for y in range(2000, 2027):
        d = fetch_by_month("cn_m", f"{y}01", f"{y}12")
        if len(d):
            frames.append(d)
        time.sleep(0.3)
    if frames:
        mm = pd.concat(frames, ignore_index=True)
        mm.to_csv(os.path.join(DATA, "ts_cn_m.csv"), index=False, encoding="utf-8-sig")
        print(f"  cn_m (M2)    {mm.shape}")

    # 7. 中国 GDP 季度
    gdp = _retry(PRO.cn_gdp, start_q="1992Q1", end_q="2026Q4")
    if gdp is not None and len(gdp):
        gdp.to_csv(os.path.join(DATA, "ts_cn_gdp.csv"), index=False, encoding="utf-8-sig")
        print(f"  cn_gdp       {gdp.shape} {list(gdp.columns)}")

    print("DONE.")


if __name__ == "__main__":
    main()
