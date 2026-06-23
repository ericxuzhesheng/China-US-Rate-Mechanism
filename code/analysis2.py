# -*- coding: utf-8 -*-
"""
analysis2.py — 扩展量化分析与可视化（Tushare 增强数据）。
Extended analysis & visualization using Tushare-enriched data.

产出 figures/*.png（约 18 张）与 report/results2.json / results2.md。
模块：
  收益率曲线演变与快照、期限利差周期、曲线 PCA（水平/斜率/曲率）、
  货币数量渠道(M2)、实际利率(名义-CPI/PPI/盈亏平衡)、中美通胀、
  滚动相关、Granger 因果、利率走廊、货币深化。
"""
import os
import json
import warnings

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import cm

import statsmodels.api as sm
from statsmodels.tsa.stattools import grangercausalitytests

warnings.filterwarnings("ignore")
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
FIG = os.path.join(ROOT, "figures")
REP = os.path.join(ROOT, "report")
os.makedirs(FIG, exist_ok=True)
R = {}

BLUE, RED, GREEN, ORANGE, PURP = "#2563eb", "#dc2626", "#16a34a", "#ea580c", "#7c3aed"


def me(idx):
    return pd.to_datetime(idx).to_period("M").to_timestamp("M")


def load():
    d = {}
    # 美国国债曲线
    us = pd.read_csv(os.path.join(DATA, "ts_us_tycr.csv"), encoding="utf-8-sig")
    us["date"] = pd.to_datetime(us["date"].astype(str), format="%Y%m%d")
    us = us.set_index("date").sort_index().apply(pd.to_numeric, errors="coerce")
    d["us_curve"] = us
    # SHIBOR
    sh = pd.read_csv(os.path.join(DATA, "ts_shibor.csv"), encoding="utf-8-sig")
    sh["date"] = pd.to_datetime(sh["date"].astype(str), format="%Y%m%d")
    sh = sh.set_index("date").sort_index().apply(pd.to_numeric, errors="coerce")
    d["shibor"] = sh
    # LPR
    lpr = pd.read_csv(os.path.join(DATA, "ts_lpr.csv"), encoding="utf-8-sig")
    lpr["date"] = pd.to_datetime(lpr["date"].astype(str), format="%Y%m%d")
    d["lpr"] = lpr.set_index("date").sort_index().apply(pd.to_numeric, errors="coerce")
    # CPI / PPI / M
    def mload(f, col_month="month"):
        x = pd.read_csv(os.path.join(DATA, f), encoding="utf-8-sig")
        x["date"] = pd.to_datetime(x[col_month].astype(str), format="%Y%m")
        return x.set_index("date").sort_index()
    d["cpi"] = mload("ts_cn_cpi.csv")
    d["ppi"] = mload("ts_cn_ppi.csv")
    d["m"] = mload("ts_cn_m.csv")
    # 中国国债曲线（akshare 月度面板）
    d["cn_panel"] = pd.read_csv(os.path.join(DATA, "panel_cn_monthly.csv"), parse_dates=[0], index_col=0)
    d["us_panel"] = pd.read_csv(os.path.join(DATA, "panel_us_monthly.csv"), parse_dates=[0], index_col=0)
    # 中国国债曲线（日度，多期限）来自 akshare cn_bond_yield
    by = pd.read_csv(os.path.join(DATA, "cn_bond_yield.csv"), encoding="utf-8-sig")
    cols = list(by.columns)
    by = by[by[cols[0]].astype(str).str.contains("国债", na=False)].copy()
    by[cols[1]] = pd.to_datetime(by[cols[1]])
    by = by.set_index(cols[1]).sort_index()
    d["cn_curve"] = by
    return d


# ---------------------------------------------------------------------------
def fig_us_curve_heatmap(d):
    us = d["us_curve"][["m3", "m6", "y1", "y2", "y3", "y5", "y7", "y10", "y20", "y30"]].copy()
    usm = us.groupby(me(us.index)).last().loc["2000":]
    fig, ax = plt.subplots(figsize=(11, 5))
    im = ax.imshow(usm.T.values, aspect="auto", cmap="RdYlBu_r", origin="lower",
                   extent=[0, len(usm), 0, usm.shape[1]])
    ax.set_yticks(np.arange(usm.shape[1]) + 0.5)
    ax.set_yticklabels(usm.columns)
    xt = np.linspace(0, len(usm) - 1, 8).astype(int)
    ax.set_xticks(xt); ax.set_xticklabels([usm.index[i].strftime("%Y") for i in xt])
    ax.set_title("US Treasury Yield Curve Evolution (2000-2026)")
    fig.colorbar(im, label="Yield (%)")
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "f05_us_curve_heatmap.png"), dpi=130); plt.close(fig)


def fig_cn_curve_heatmap(d):
    by = d["cn_curve"]
    terms = [c for c in ["3月", "6月", "1年", "3年", "5年", "7年", "10年", "30年"] if c in by.columns]
    cn = by[terms].apply(pd.to_numeric, errors="coerce")
    cnm = cn.groupby(me(cn.index)).last().loc["2007":]
    fig, ax = plt.subplots(figsize=(11, 5))
    im = ax.imshow(cnm.T.values, aspect="auto", cmap="RdYlBu_r", origin="lower",
                   extent=[0, len(cnm), 0, cnm.shape[1]])
    ax.set_yticks(np.arange(cnm.shape[1]) + 0.5); ax.set_yticklabels(terms)
    xt = np.linspace(0, len(cnm) - 1, 8).astype(int)
    ax.set_xticks(xt); ax.set_xticklabels([cnm.index[i].strftime("%Y") for i in xt])
    ax.set_title("China Treasury Yield Curve Evolution (2007-2026)")
    fig.colorbar(im, label="Yield (%)")
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "f06_cn_curve_heatmap.png"), dpi=130); plt.close(fig)


def fig_curve_snapshots(d):
    us = d["us_curve"]; by = d["cn_curve"]
    tenors_us = {"m3": 0.25, "m6": 0.5, "y1": 1, "y2": 2, "y3": 3, "y5": 5, "y7": 7, "y10": 10, "y20": 20, "y30": 30}
    tenors_cn = {"3月": 0.25, "6月": 0.5, "1年": 1, "3年": 3, "5年": 5, "7年": 7, "10年": 10, "30年": 30}
    dates = ["2019-12-31", "2022-12-31", "2026-06-15"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for dt in dates:
        s = us.loc[:dt].iloc[-1]
        xs = [tenors_us[k] for k in tenors_us if k in s.index and pd.notna(s[k])]
        ys = [s[k] for k in tenors_us if k in s.index and pd.notna(s[k])]
        axes[0].plot(xs, ys, marker="o", label=dt[:7])
    axes[0].set_title("US Treasury Curve Snapshots"); axes[0].set_xlabel("Maturity (yrs)"); axes[0].set_ylabel("%"); axes[0].legend()
    bym = by.apply(pd.to_numeric, errors="coerce")
    for dt in dates:
        s = bym.loc[:dt]
        if len(s) == 0:
            continue
        s = s.iloc[-1]
        xs = [tenors_cn[k] for k in tenors_cn if k in s.index and pd.notna(s[k])]
        ys = [s[k] for k in tenors_cn if k in s.index and pd.notna(s[k])]
        axes[1].plot(xs, ys, marker="s", label=dt[:7])
    axes[1].set_title("China Treasury Curve Snapshots"); axes[1].set_xlabel("Maturity (yrs)"); axes[1].set_ylabel("%"); axes[1].legend()
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "f07_curve_snapshots.png"), dpi=130); plt.close(fig)


def fig_term_spread(d):
    us = d["us_curve"].groupby(me(d["us_curve"].index)).last()
    us_spread = (us["y10"] - us["y2"]).loc["2007":]
    cnp = d["cn_panel"]
    cn_spread = (cnp["cn_10y"] - cnp.get("cn_2y", cnp.get("cn_1y"))).loc["2007":]
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(us_spread.index, us_spread, label="US 10Y-2Y", color=RED)
    ax.plot(cn_spread.index, cn_spread, label="CN 10Y-2Y", color=BLUE)
    ax.axhline(0, color="black", lw=0.7)
    ax.fill_between(us_spread.index, us_spread, 0, where=(us_spread < 0), color=RED, alpha=0.15)
    ax.set_title("Term Spread (10Y-2Y): US vs China — Inversion Signals")
    ax.set_ylabel("Spread (%)"); ax.legend()
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "f08_term_spread.png"), dpi=130); plt.close(fig)
    R["term_spread"] = {"us_last": float(us_spread.dropna().iloc[-1]), "cn_last": float(cn_spread.dropna().iloc[-1])}


def yield_pca(df, terms, label, fname):
    X = df[terms].apply(pd.to_numeric, errors="coerce").dropna()
    Xc = X - X.mean()
    U, S, Vt = np.linalg.svd(Xc.values, full_matrices=False)
    evr = (S ** 2) / (S ** 2).sum()
    pcs = Xc.values @ Vt.T
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    tn = np.arange(len(terms))
    for i in range(3):
        axes[0].plot(tn, Vt[i], marker="o", label=f"PC{i+1} ({evr[i]*100:.1f}%)")
    axes[0].set_xticks(tn); axes[0].set_xticklabels(terms, rotation=45)
    axes[0].set_title(f"{label}: Yield-Curve Loadings (Level/Slope/Curvature)"); axes[0].legend()
    axes[1].plot(X.index, pcs[:, 0], label="PC1 Level", color=BLUE)
    axes[1].plot(X.index, pcs[:, 1], label="PC2 Slope", color=RED)
    axes[1].set_title(f"{label}: Principal Component Scores"); axes[1].legend()
    fig.tight_layout(); fig.savefig(os.path.join(FIG, fname), dpi=130); plt.close(fig)
    R[f"pca_{label}"] = {"evr_top3": [float(x) for x in evr[:3]]}


def fig_pca(d):
    us = d["us_curve"].groupby(me(d["us_curve"].index)).last().loc["2000":]
    yield_pca(us, ["m3", "m6", "y1", "y2", "y3", "y5", "y7", "y10"], "US", "f09a_us_pca.png")
    by = d["cn_curve"].groupby(me(d["cn_curve"].index)).last().loc["2007":]
    terms = [c for c in ["3月", "6月", "1年", "3年", "5年", "7年", "10年"] if c in by.columns]
    yield_pca(by, terms, "CN", "f09b_cn_pca.png")


def fig_m2_vs_rate(d):
    m = d["m"]
    sh1w = d["shibor"]["1w"].groupby(me(d["shibor"].index)).mean()
    fig, ax1 = plt.subplots(figsize=(11, 5))
    ax1.plot(m.index, m["m2_yoy"], color=PURP, label="M2 YoY (%)")
    ax1.plot(m.index, m["m1_yoy"], color=GREEN, alpha=0.7, label="M1 YoY (%)")
    ax1.set_ylabel("Money supply YoY (%)"); ax1.legend(loc="upper left")
    ax2 = ax1.twinx()
    ax2.plot(sh1w.index, sh1w, color=BLUE, label="SHIBOR 1W (%)")
    ax2.set_ylabel("SHIBOR 1W (%)"); ax2.legend(loc="upper right")
    ax1.set_title("China: Quantity Channel (M1/M2 growth) vs Price (SHIBOR)")
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "f10_m2_vs_rate.png"), dpi=130); plt.close(fig)
    R["m2"] = {"m2_yoy_last": float(m["m2_yoy"].dropna().iloc[-1]),
               "m2_yoy_mean": float(m["m2_yoy"].dropna().mean())}


def fig_real_rates(d):
    cnp = d["cn_panel"]; usp = d["us_panel"]
    cpi = d["cpi"]["nt_yoy"].groupby(me(d["cpi"].index)).last()
    ppi = d["ppi"]["ppi_yoy"].groupby(me(d["ppi"].index)).last()
    cn10 = cnp["cn_10y"]
    real_cpi = (cn10 - cpi).dropna()
    real_ppi = (cn10 - ppi).dropna()
    us_real = (usp["ust_10y"] - usp["core_pce_yoy"]).dropna()
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(real_cpi.loc["2008":].index, real_cpi.loc["2008":], label="CN real (10Y - CPI)", color=BLUE)
    ax.plot(real_ppi.loc["2008":].index, real_ppi.loc["2008":], label="CN real (10Y - PPI)", color=ORANGE)
    ax.plot(us_real.loc["2008":].index, us_real.loc["2008":], label="US real (10Y - core PCE)", color=RED)
    ax.axhline(0, color="black", lw=0.7)
    ax.set_title("Ex-post Real 10Y Yields: China (vs CPI & PPI) and US")
    ax.set_ylabel("%"); ax.legend()
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "f11_real_rates.png"), dpi=130); plt.close(fig)
    R["real_rate"] = {"cn_real_ppi_last": float(real_ppi.iloc[-1]),
                      "cn_real_cpi_last": float(real_cpi.iloc[-1]),
                      "us_real_last": float(us_real.iloc[-1])}


def fig_inflation(d):
    cpi = d["cpi"]["nt_yoy"].groupby(me(d["cpi"].index)).last()
    ppi = d["ppi"]["ppi_yoy"].groupby(me(d["ppi"].index)).last()
    usp = d["us_panel"]
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(cpi.loc["2005":].index, cpi.loc["2005":], label="CN CPI YoY", color=BLUE)
    ax.plot(ppi.loc["2005":].index, ppi.loc["2005":], label="CN PPI YoY", color=ORANGE)
    ax.plot(usp["core_pce_yoy"].loc["2005":].index, usp["core_pce_yoy"].loc["2005":], label="US core PCE YoY", color=RED)
    ax.axhline(0, color="black", lw=0.6); ax.axhline(2, color="grey", ls="--", lw=0.6)
    ax.set_title("Inflation: China CPI/PPI vs US core PCE")
    ax.set_ylabel("YoY %"); ax.legend()
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "f14_inflation.png"), dpi=130); plt.close(fig)


def fig_rolling_corr(d):
    usp = d["us_panel"]; cnp = d["cn_panel"]
    df = pd.DataFrame({"us": usp["ust_10y"], "cn": cnp["cn_10y"]}).dropna()
    rc = df["us"].diff().rolling(24).corr(df["cn"].diff())
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(rc.index, rc, color=PURP)
    ax.axhline(0, color="black", lw=0.7)
    ax.fill_between(rc.index, rc, 0, where=(rc < 0), color=RED, alpha=0.15)
    ax.set_title("Rolling 24M Correlation of ΔUS10Y and ΔCN10Y")
    ax.set_ylabel("corr");
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "f13_rolling_corr.png"), dpi=130); plt.close(fig)
    R["rolling_corr_last"] = float(rc.dropna().iloc[-1])


def fig_corridor(d):
    sh = d["shibor"]
    on = sh["on"].groupby(me(sh.index)).mean()
    w1 = sh["1w"].groupby(me(sh.index)).mean()
    lpr = d["lpr"]["1y"].reindex(pd.date_range(d["lpr"].index.min(), "2026-06-30", freq="D")).ffill()
    lprm = lpr.groupby(me(lpr.index)).last()
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(on.loc["2014":].index, on.loc["2014":], label="SHIBOR O/N", color=GREEN, alpha=0.8)
    ax.plot(w1.loc["2014":].index, w1.loc["2014":], label="SHIBOR 1W (~DR007)", color=BLUE)
    ax.plot(lprm.loc["2014":].index, lprm.loc["2014":], label="LPR 1Y", color=RED)
    ax.set_title("China Rate Corridor: Money-Market (O/N,1W) vs Loan Prime Rate")
    ax.set_ylabel("%"); ax.legend()
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "f17_corridor.png"), dpi=130); plt.close(fig)


def granger(d):
    usp = d["us_panel"]; cnp = d["cn_panel"]
    df = pd.DataFrame({"us": usp["ust_10y"], "cn": cnp["cn_10y"]}).dropna().diff().dropna()
    out = {}
    try:
        g1 = grangercausalitytests(df[["cn", "us"]], maxlag=3, verbose=False)  # US -> CN
        out["US_to_CN_p_lag3"] = float(g1[3][0]["ssr_ftest"][1])
        g2 = grangercausalitytests(df[["us", "cn"]], maxlag=3, verbose=False)  # CN -> US
        out["CN_to_US_p_lag3"] = float(g2[3][0]["ssr_ftest"][1])
    except Exception as e:
        out["err"] = repr(e)[:100]
    R["granger"] = out


def fig_passthrough_bar(d=None):
    res = json.load(open(os.path.join(REP, "results.json"), encoding="utf-8"))
    labels, beta, impact = [], [], []
    mp = {"ecm_us_ff_to_2y": "US FF→2Y", "ecm_us_ff_to_10y": "US FF→10Y",
          "ecm_cn_shibor_to_10y": "CN SHIBOR→10Y", "ecm_cn_lpr_to_10y": "CN LPR→10Y"}
    for k, lab in mp.items():
        e = res.get(k)
        if e:
            labels.append(lab); beta.append(e["long_run_passthrough"]); impact.append(e["impact_passthrough"])
    x = np.arange(len(labels)); w = 0.35
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(x - w/2, beta, w, label="Long-run β", color=BLUE)
    ax.bar(x + w/2, impact, w, label="Impact γ", color=ORANGE)
    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_title("Interest-Rate Pass-Through: Long-run vs Impact")
    ax.legend()
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "f15_passthrough_bar.png"), dpi=130); plt.close(fig)


def fig_taylor_cn(d):
    cnp = d["cn_panel"]
    cpi = d["cpi"]["nt_yoy"].groupby(me(d["cpi"].index)).last()
    df = pd.DataFrame({"i": cnp["cn_shibor1w"], "pi": cpi, "g": cnp["cn_gdp_yoy"]}).dropna().loc["2011":]
    df["i_l1"] = df["i"].shift(1); df = df.dropna()
    X = sm.add_constant(df[["pi", "g", "i_l1"]])
    r = sm.OLS(df["i"], X).fit()
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(df.index, df["i"], label="Actual SHIBOR 1W", color="black")
    ax.plot(df.index, r.fittedvalues, label="China rule fitted", color=RED, ls="--")
    ax.set_title("China: Estimated Policy Rule Fit (SHIBOR 1W)")
    ax.set_ylabel("%"); ax.legend()
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "f04_taylor_cn_fit.png"), dpi=130); plt.close(fig)


def fig_money_deepening(d):
    # M2/名义GDP 近似货币深化（用 M2 水平 与 GDP 同比无法直接得比率，改画 M2 余额 trillion 与 增速）
    m = d["m"]
    fig, ax1 = plt.subplots(figsize=(11, 5))
    ax1.plot(m.index, m["m2"] / 1e4, color=PURP, label="M2 balance (trillion CNY)")
    ax1.set_ylabel("M2 (trn CNY)"); ax1.legend(loc="upper left")
    ax2 = ax1.twinx()
    ax2.plot(m.index, m["m2_yoy"], color=BLUE, alpha=0.5, label="M2 YoY %")
    ax2.set_ylabel("M2 YoY %"); ax2.legend(loc="upper right")
    ax1.set_title("China Money Supply M2: Level and Growth")
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "f18_m2_deepening.png"), dpi=130); plt.close(fig)


def main():
    d = load()
    print("loaded.")
    fns = [fig_us_curve_heatmap, fig_cn_curve_heatmap, fig_curve_snapshots, fig_term_spread,
           fig_pca, fig_m2_vs_rate, fig_real_rates, fig_inflation, fig_rolling_corr,
           fig_corridor, fig_passthrough_bar, fig_taylor_cn, fig_money_deepening, granger]
    for fn in fns:
        try:
            fn(d); print("  OK", fn.__name__)
        except Exception as e:
            print("  FAIL", fn.__name__, repr(e)[:140])
    json.dump(R, open(os.path.join(REP, "results2.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    lines = ["# 扩展量化结果 (analysis2.py)\n"]
    lines.append("```json"); lines.append(json.dumps(R, ensure_ascii=False, indent=2)); lines.append("```")
    open(os.path.join(REP, "results2.md"), "w", encoding="utf-8").write("\n".join(lines))
    print("DONE.")


if __name__ == "__main__":
    main()
