# -*- coding: utf-8 -*-
"""
analysis3.py — 第三批分析：SVAR 脉冲响应、时变（滚动）参数、领先滞后、
子样本泰勒规则、描述统计与相关矩阵。产出 figures/f2x_*.png 与 report/results3.json。
"""
import os, json, warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import statsmodels.api as sm
from statsmodels.tsa.api import VAR

warnings.filterwarnings("ignore")
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data"); FIG = os.path.join(ROOT, "figures"); REP = os.path.join(ROOT, "report")
R = {}
BLUE, RED, GREEN, ORANGE, PURP = "#2563eb", "#dc2626", "#16a34a", "#ea580c", "#7c3aed"


def load():
    usp = pd.read_csv(os.path.join(DATA, "panel_us_monthly.csv"), parse_dates=[0], index_col=0)
    cnp = pd.read_csv(os.path.join(DATA, "panel_cn_monthly.csv"), parse_dates=[0], index_col=0)
    cpi = pd.read_csv(os.path.join(DATA, "ts_cn_cpi.csv"), encoding="utf-8-sig")
    cpi["pm"] = pd.to_datetime(cpi["month"].astype(str), format="%Y%m").dt.to_period("M")
    cpi_s = cpi.drop_duplicates("pm").set_index("pm")["nt_yoy"]
    # 按年-月对齐到面板（面板为月末索引）
    cnp["cn_cpi_yoy2"] = pd.Series(cnp.index.to_period("M"), index=cnp.index).map(cpi_s)
    # 若面板已有 cn_cpi_yoy 则回填
    if "cn_cpi_yoy" in cnp.columns:
        cnp["cn_cpi_yoy2"] = cnp["cn_cpi_yoy2"].fillna(cnp["cn_cpi_yoy"])
    return usp, cnp


# --- SVAR IRF ---------------------------------------------------------------
def svar_irf(df, order, shock, resp, label, fname, color):
    d = df[order].dropna()
    model = VAR(d)
    res = model.fit(maxlags=4, ic="aic")
    irf = res.irf(24)
    si = order.index(shock); ri = order.index(resp)
    irfs = irf.orth_irfs[:, ri, si]
    cum = np.cumsum(irfs)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(range(len(irfs)), irfs, color=color, marker="o", ms=3, label=f"{resp} response")
    ax.axhline(0, color="black", lw=0.6)
    ax.set_title(f"{label}: IRF of {resp} to a {shock} shock (orthogonalized)")
    ax.set_xlabel("Months"); ax.set_ylabel("Response"); ax.legend()
    fig.tight_layout(); fig.savefig(os.path.join(FIG, fname), dpi=130); plt.close(fig)
    R[f"svar_{label}_{resp}"] = {"peak": float(np.max(np.abs(irfs))), "lag_order": int(res.k_ar)}


def fig_svar(usp, cnp):
    us = pd.DataFrame({
        "dpolicy": usp["fed_funds_effective"].diff(),
        "infl": usp["core_pce_yoy"],
        "unemp": usp["unemployment"],
        "d10y": usp["ust_10y"].diff(),
    }).dropna().loc["1995":]
    svar_irf(us, ["infl", "unemp", "dpolicy", "d10y"], "dpolicy", "d10y", "US", "f20_svar_us.png", RED)

    cn = pd.DataFrame({
        "dpolicy": cnp["cn_shibor1w"].diff(),
        "infl": cnp["cn_cpi_yoy2"],
        "growth": cnp["cn_gdp_yoy"],
        "d10y": cnp["cn_10y"].diff(),
    }).dropna().loc["2011":]
    svar_irf(cn, ["infl", "growth", "dpolicy", "d10y"], "dpolicy", "d10y", "CN", "f21_svar_cn.png", BLUE)


# --- 时变 Taylor 系数（滚动）------------------------------------------------
def fig_rolling_taylor(usp, cnp):
    def roll_phi(i, pi, win=60):
        df = pd.DataFrame({"i": i, "pi": pi}).dropna()
        df["il1"] = df["i"].shift(1); df = df.dropna()
        out = {}
        for t in range(win, len(df)):
            s = df.iloc[t-win:t]
            X = sm.add_constant(s[["pi", "il1"]])
            try:
                r = sm.OLS(s["i"], X).fit()
                rho = r.params["il1"]
                if rho < 0.999:
                    out[df.index[t]] = r.params["pi"]/(1-rho)
            except Exception:
                pass
        return pd.Series(out)
    us_phi = roll_phi(usp["fed_funds_effective"], usp["core_pce_yoy"]).clip(-2, 6)
    cn_phi = roll_phi(cnp["cn_shibor1w"], cnp["cn_cpi_yoy2"]).clip(-2, 6)
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(us_phi.index, us_phi, label="US φπ (rolling 60M)", color=RED)
    ax.plot(cn_phi.index, cn_phi, label="CN φπ (rolling 60M)", color=BLUE)
    ax.axhline(1, color="black", ls="--", lw=0.8, label="Taylor principle φπ=1")
    ax.set_title("Time-Varying Inflation Response φπ (rolling Taylor rule)")
    ax.set_ylabel("φπ"); ax.legend()
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "f22_rolling_taylor.png"), dpi=130); plt.close(fig)


# --- 滚动 beta：ΔCN10Y ~ ΔUS10Y --------------------------------------------
def fig_rolling_beta(usp, cnp):
    df = pd.DataFrame({"us": usp["ust_10y"].diff(), "cn": cnp["cn_10y"].diff()}).dropna()
    win = 36; betas = {}
    for t in range(win, len(df)):
        s = df.iloc[t-win:t]
        X = sm.add_constant(s["us"])
        r = sm.OLS(s["cn"], X).fit()
        betas[df.index[t]] = r.params["us"]
    b = pd.Series(betas)
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(b.index, b, color=PURP)
    ax.axhline(0, color="black", lw=0.7)
    ax.set_title("Rolling 36M Beta of ΔCN10Y on ΔUS10Y (spillover sensitivity)")
    ax.set_ylabel("β"); fig.tight_layout()
    fig.savefig(os.path.join(FIG, "f23_rolling_beta.png"), dpi=130); plt.close(fig)
    R["rolling_beta_last"] = float(b.dropna().iloc[-1])


# --- 领先滞后 互相关 --------------------------------------------------------
def fig_xcorr(usp, cnp):
    df = pd.DataFrame({"us": usp["ust_10y"].diff(), "cn": cnp["cn_10y"].diff()}).dropna()
    lags = range(-12, 13)
    xc = [df["us"].corr(df["cn"].shift(-k)) for k in lags]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar(list(lags), xc, color=[RED if k < 0 else (GREEN if k > 0 else "grey") for k in lags])
    ax.axvline(0, color="black", lw=0.7)
    ax.set_title("Cross-correlation: corr(ΔUS10Y_t, ΔCN10Y_{t+k})")
    ax.set_xlabel("Lag k (k>0: US leads CN)"); ax.set_ylabel("corr")
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "f24_xcorr.png"), dpi=130); plt.close(fig)
    R["xcorr_us_leads_max"] = {"k": int(list(lags)[int(np.nanargmax(xc))]), "corr": float(np.nanmax(xc))}


# --- 相关矩阵热力图 ---------------------------------------------------------
def fig_corr_matrix(usp, cnp):
    df = pd.DataFrame({
        "US_FF": usp["fed_funds_effective"], "US_2Y": usp["ust_2y"], "US_10Y": usp["ust_10y"],
        "US_corePCE": usp["core_pce_yoy"],
        "CN_SHIBOR": cnp["cn_shibor1w"], "CN_10Y": cnp["cn_10y"], "CN_LPR": cnp["cn_lpr1y"],
        "CN_CPI": cnp["cn_cpi_yoy2"],
    }).loc["2011":].dropna()
    C = df.corr()
    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.imshow(C.values, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(C))); ax.set_xticklabels(C.columns, rotation=45, ha="right")
    ax.set_yticks(range(len(C))); ax.set_yticklabels(C.columns)
    for i in range(len(C)):
        for j in range(len(C)):
            ax.text(j, i, f"{C.values[i,j]:.2f}", ha="center", va="center",
                    color="white" if abs(C.values[i,j]) > 0.5 else "black", fontsize=8)
    ax.set_title("Correlation Matrix: US & China Rates and Inflation (2011-)")
    fig.colorbar(im, fraction=0.046)
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "f25_corr_matrix.png"), dpi=130); plt.close(fig)


# --- 子样本泰勒规则 ---------------------------------------------------------
def subsample_taylor(cnp):
    def est(start, end):
        d = pd.DataFrame({"i": cnp["cn_shibor1w"], "pi": cnp["cn_cpi_yoy2"], "g": cnp["cn_gdp_yoy"]}).dropna().loc[start:end]
        d["il1"] = d["i"].shift(1); d = d.dropna()
        X = sm.add_constant(d[["pi", "g", "il1"]])
        r = sm.OLS(d["i"], X).fit()
        rho = r.params["il1"]
        return {"n": int(r.nobs), "R2": float(r.rsquared), "phi_pi": float(r.params["pi"]/(1-rho)),
                "phi_g": float(r.params["g"]/(1-rho)), "rho": float(rho)}
    R["subsample_cn"] = {"2011-2016": est("2011", "2016"), "2017-2026": est("2017", "2026")}


def summary_stats(usp, cnp):
    df = pd.DataFrame({
        "US_FF": usp["fed_funds_effective"], "US_10Y": usp["ust_10y"], "US_corePCE": usp["core_pce_yoy"],
        "CN_SHIBOR1W": cnp["cn_shibor1w"], "CN_10Y": cnp["cn_10y"], "CN_LPR1Y": cnp["cn_lpr1y"],
        "CN_CPI": cnp["cn_cpi_yoy2"],
    }).loc["2011":]
    R["summary"] = {c: {"mean": float(df[c].mean()), "std": float(df[c].std()),
                        "min": float(df[c].min()), "max": float(df[c].max()),
                        "n": int(df[c].dropna().shape[0])} for c in df.columns}


def main():
    usp, cnp = load()
    for fn in [fig_svar, fig_rolling_taylor, fig_rolling_beta, fig_xcorr, fig_corr_matrix]:
        try:
            fn(usp, cnp); print("OK", fn.__name__)
        except Exception as e:
            print("FAIL", fn.__name__, repr(e)[:160])
    subsample_taylor(cnp); summary_stats(usp, cnp)
    json.dump(R, open(os.path.join(REP, "results3.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("DONE")


if __name__ == "__main__":
    main()
