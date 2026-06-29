# -*- coding: utf-8 -*-
"""
analysis.py — 中美利率决定机制差异的量化分析主程序。
Quantitative analysis of China vs US interest-rate determination mechanisms.

依赖 data_fetch.py 产出的 CSV。运行后输出：
  - figures/*.png       图表
  - report/results.md   关键回归与统计量的表格（供报告引用）
  - report/results.json 机器可读结果

涵盖模块：
  M1  数据对齐为月度面板
  M2  泰勒规则估计（美 / 中），含利率平滑（动态）
  M3  利率传导 / pass-through（ECM 误差修正模型）
  M4  政策利率—市场利率联动与波动率对比
  M5  中美 10Y 利差的协整与驱动（VECM / 回归）
  M6  期限结构：利差(10Y-2Y/10Y-1Y) 与货币周期
"""
import os
import json
import warnings

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import statsmodels.api as sm
from statsmodels.tsa.stattools import adfuller, coint

warnings.filterwarnings("ignore")
plt.rcParams["axes.unicode_minus"] = False
# 中文字体（Windows）
for f in ["Microsoft YaHei", "SimHei", "DejaVu Sans"]:
    try:
        matplotlib.rcParams["font.sans-serif"] = [f]
        break
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
FIG = os.path.join(ROOT, "figures")
REP = os.path.join(ROOT, "report")
os.makedirs(FIG, exist_ok=True)
os.makedirs(REP, exist_ok=True)

RESULTS = {}


def me(idx):
    """转月末时间戳 / month-end timestamp."""
    return pd.to_datetime(idx).to_period("M").to_timestamp("M")


# ---------------------------------------------------------------------------
# M1. 载入并对齐为月度面板
# ---------------------------------------------------------------------------
def load_us():
    us = pd.read_csv(os.path.join(DATA, "us_raw.csv"), parse_dates=["date"]).set_index("date")
    m = pd.DataFrame(index=pd.period_range(us.index.min(), us.index.max(), freq="M").to_timestamp("M"))
    # 月度：直接取；日度：月末
    def mfreq(col):
        s = us[col].dropna()
        return s.groupby(me(s.index)).last()
    for c in us.columns:
        m[c] = mfreq(c)
    # 通胀（同比）
    m["cpi_yoy"] = m["cpi"].pct_change(12) * 100
    m["core_pce_yoy"] = m["core_pce"].pct_change(12) * 100
    # 失业缺口（相对自然失业率近似 4.4%，亦可用 NROU）
    m["u_gap"] = m["unemployment"] - 4.4
    return m


def load_cn():
    # SHIBOR 1W：列 [date, rate, chg]
    sh = pd.read_csv(os.path.join(DATA, "cn_shibor_1w.csv"), encoding="utf-8-sig")
    sh.columns = ["date", "shibor1w", "chg"]
    sh["date"] = pd.to_datetime(sh["date"])
    sh = sh.set_index("date")["shibor1w"].dropna()
    shm = sh.groupby(me(sh.index)).mean()

    shon = pd.read_csv(os.path.join(DATA, "cn_shibor_on.csv"), encoding="utf-8-sig")
    shon.columns = ["date", "shiboron", "chg"]
    shon["date"] = pd.to_datetime(shon["date"])
    shon = shon.set_index("date")["shiboron"].dropna()
    shonm = shon.groupby(me(shon.index)).mean()

    # LPR：TRADE_DATE, LPR1Y, LPR5Y, RATE_1(1年期贷款基准)
    lpr = pd.read_csv(os.path.join(DATA, "cn_lpr.csv"))
    lpr["TRADE_DATE"] = pd.to_datetime(lpr["TRADE_DATE"])
    lpr = lpr.set_index("TRADE_DATE").sort_index()
    lpr1y = lpr["LPR1Y"].dropna()
    lpr1y_m = lpr1y.reindex(pd.date_range(lpr1y.index.min(), "2026-06-30", freq="D")).ffill()
    lpr1y_m = lpr1y_m.groupby(me(lpr1y_m.index)).last()

    # 国债收益率：曲线名称, 日期, 各期限
    by = pd.read_csv(os.path.join(DATA, "cn_bond_yield.csv"), encoding="utf-8-sig")
    cols = list(by.columns)
    # 识别日期列与期限列（中文列名）
    date_col = cols[1] if "日" in cols[1] else cols[0]
    # 仅保留国债（中债国债收益率曲线）
    name_col = cols[0]
    by = by[by[name_col].astype(str).str.contains("国债", na=False)]
    by[date_col] = pd.to_datetime(by[date_col])
    by = by.set_index(date_col).sort_index()

    def pick(term_options):
        for t in term_options:
            for c in by.columns:
                if str(c).strip() == t:
                    return pd.to_numeric(by[c], errors="coerce")
        return None

    y10 = pick(["10年"]); y2 = pick(["2年", "1年"]); y1 = pick(["1年"]); y3m = pick(["3月", "6月"])
    cn = pd.DataFrame()
    cn["cn_shibor1w"] = shm
    cn["cn_shibor_on"] = shonm
    cn["cn_lpr1y"] = lpr1y_m
    if y10 is not None:
        cn["cn_10y"] = y10.groupby(me(y10.index)).last()
    if y2 is not None:
        cn["cn_2y"] = y2.groupby(me(y2.index)).last()
    if y1 is not None:
        cn["cn_1y"] = y1.groupby(me(y1.index)).last()
    if y3m is not None:
        cn["cn_3m"] = y3m.groupby(me(y3m.index)).last()

    # CPI 同比（来自 cn_cpi_table：月份, 全国-当月, 全国-同比涨幅...）
    cpi = pd.read_csv(os.path.join(DATA, "cn_cpi_table.csv"), encoding="utf-8-sig")
    cpi.columns = ["m_raw"] + list(cpi.columns[1:])
    yoy_col = cpi.columns[2]  # 全国-同比涨幅
    cpi["date"] = pd.to_datetime(
        cpi["m_raw"].str.replace("年", "-").str.replace("月份", "").str.replace("月", ""),
        format="%Y-%m", errors="coerce")
    cpi = cpi.dropna(subset=["date"]).set_index("date").sort_index()
    cn["cn_cpi_yoy"] = pd.to_numeric(cpi[yoy_col], errors="coerce").groupby(me(cpi.index)).last()

    # GDP 同比（季度，release-date 近似为参考季；用季频前向填充到月）
    gdp = pd.read_csv(os.path.join(DATA, "cn_gdp_yearly.csv"), encoding="utf-8-sig")
    gdp.columns = ["item", "date", "value", "fcst", "prev"]
    gdp["date"] = pd.to_datetime(gdp["date"])
    g = gdp.set_index("date")["value"].dropna().sort_index()
    gm = g.reindex(pd.date_range(g.index.min(), "2026-06-30", freq="D")).ffill()
    cn["cn_gdp_yoy"] = gm.groupby(me(gm.index)).last()
    return cn


def adf_p(series):
    s = series.dropna()
    if len(s) < 12:
        return np.nan
    try:
        return adfuller(s, autolag="AIC")[1]
    except Exception:
        return np.nan


# ---------------------------------------------------------------------------
# M2. 泰勒规则估计
# ---------------------------------------------------------------------------
def taylor_us(m):
    """美联储反应函数（含利率平滑）：
       i_t = (1-rho)[c + a*pi_gap + b*(-u_gap)] + rho*i_{t-1} + e_t
       这里用 (-u_gap) 作为产出/就业缺口的正向代理（失业越低，越紧）。
    """
    d = pd.DataFrame({
        "i": m["fed_funds_effective"],
        "pi": m["core_pce_yoy"],
        "ugap": m["u_gap"],
    }).dropna()
    d = d.loc["1990":]  # 现代货币政策样本
    d["i_l1"] = d["i"].shift(1)
    d = d.dropna()
    X = sm.add_constant(pd.DataFrame({
        "pi": d["pi"], "neg_ugap": -d["ugap"], "i_l1": d["i_l1"]}))
    res = sm.OLS(d["i"], X).fit(cov_type="HAC", cov_kwds={"maxlags": 12})
    rho = res.params["i_l1"]
    long_run = {
        "phi_pi": res.params["pi"] / (1 - rho),
        "phi_gap": res.params["neg_ugap"] / (1 - rho),
        "rho": rho,
    }
    RESULTS["taylor_us"] = {
        "n": int(res.nobs), "R2": float(res.rsquared),
        "params": {k: float(v) for k, v in res.params.items()},
        "tvalues": {k: float(v) for k, v in res.tvalues.items()},
        "long_run": {k: float(v) for k, v in long_run.items()},
        "sample": f"{d.index.min().date()}..{d.index.max().date()}",
    }
    return res, d


def taylor_cn(cn):
    """中国'类泰勒规则'估计：以 7天 SHIBOR(货币市场利率) 对 CPI 同比与 GDP 同比反应。
       中国还受数量型工具影响，价格规则解释力通常弱 —— 这是关键差异点。
    """
    d = pd.DataFrame({
        "i": cn["cn_shibor1w"],
        "pi": cn["cn_cpi_yoy"],
        "g": cn["cn_gdp_yoy"],
    }).dropna()
    d = d.loc["2011":]
    d["i_l1"] = d["i"].shift(1)
    d = d.dropna()
    X = sm.add_constant(pd.DataFrame({"pi": d["pi"], "g": d["g"], "i_l1": d["i_l1"]}))
    res = sm.OLS(d["i"], X).fit(cov_type="HAC", cov_kwds={"maxlags": 12})
    rho = res.params["i_l1"]
    RESULTS["taylor_cn"] = {
        "n": int(res.nobs), "R2": float(res.rsquared),
        "params": {k: float(v) for k, v in res.params.items()},
        "tvalues": {k: float(v) for k, v in res.tvalues.items()},
        "long_run": {"phi_pi": float(res.params["pi"]/(1-rho)),
                     "phi_g": float(res.params["g"]/(1-rho)), "rho": float(rho)},
        "sample": f"{d.index.min().date()}..{d.index.max().date()}",
    }
    return res, d


# ---------------------------------------------------------------------------
# M3. 利率传导 / pass-through（ECM）
# ---------------------------------------------------------------------------
def ecm_passthrough(name, policy, market, start=None):
    """误差修正模型估计政策->市场利率传导：
       长期：market = a + b*policy + u
       短期：d(market) = g*d(policy) + lam*u_{t-1} + e
       返回长期传导系数 b、即期传导 g、调整速度 lam、半衰期。
    """
    d = pd.DataFrame({"m": market, "p": policy}).dropna()
    if start:
        d = d.loc[start:]
    if len(d) < 24:
        return None
    Xl = sm.add_constant(d["p"])
    lr = sm.OLS(d["m"], Xl).fit()
    b = float(lr.params["p"])
    u = lr.resid
    dd = pd.DataFrame({
        "dm": d["m"].diff(), "dp": d["p"].diff(), "u_l1": u.shift(1)
    }).dropna()
    Xs = sm.add_constant(dd[["dp", "u_l1"]])
    sr = sm.OLS(dd["dm"], Xs).fit(cov_type="HAC", cov_kwds={"maxlags": 6})
    lam = float(sr.params["u_l1"])
    g = float(sr.params["dp"])
    half_life = float(np.log(0.5) / np.log(1 + lam)) if -2 < lam < 0 else np.nan
    out = {
        "long_run_passthrough": b, "lr_t": float(lr.tvalues["p"]),
        "impact_passthrough": g, "impact_t": float(sr.tvalues["dp"]),
        "adj_speed_lambda": lam, "adj_t": float(sr.tvalues["u_l1"]),
        "half_life_months": half_life, "n": int(sr.nobs),
        "coint_p": float(coint(d["m"], d["p"])[1]),
        "sample": f"{d.index.min().date()}..{d.index.max().date()}",
    }
    RESULTS[f"ecm_{name}"] = out
    return out


# ---------------------------------------------------------------------------
# M4. 政策/市场利率的波动率与水平对比
# ---------------------------------------------------------------------------
def vol_compare(m, cn):
    out = {}
    pairs = {
        "us_fedfunds": m["fed_funds_effective"].loc["2011":],
        "us_3m": m["ust_3m"].loc["2011":],
        "us_10y": m["ust_10y"].loc["2011":],
        "cn_shibor1w": cn["cn_shibor1w"].loc["2011":],
        "cn_10y": cn.get("cn_10y", pd.Series(dtype=float)).loc["2011":],
        "cn_lpr1y": cn["cn_lpr1y"].loc["2011":],
    }
    for k, s in pairs.items():
        s = s.dropna()
        if len(s) > 12:
            out[k] = {"mean": float(s.mean()), "std": float(s.std()),
                      "min": float(s.min()), "max": float(s.max()),
                      "d_std": float(s.diff().std())}
    RESULTS["vol_compare"] = out
    return out


# ---------------------------------------------------------------------------
# M5. 中美 10Y 利差协整与驱动
# ---------------------------------------------------------------------------
def spread_analysis(m, cn):
    if "cn_10y" not in cn:
        return None
    d = pd.DataFrame({
        "us10": m["ust_10y"], "cn10": cn["cn_10y"],
    }).dropna().loc["2011":]   # 中国样本统一自 2011 年起
    d["spread"] = d["cn10"] - d["us10"]
    # 协整检验
    cp = float(coint(d["cn10"], d["us10"])[1])
    corr_level = float(d["cn10"].corr(d["us10"]))
    corr_diff = float(d["cn10"].diff().corr(d["us10"].diff()))
    RESULTS["spread"] = {
        "coint_p_cn_us_10y": cp,
        "corr_level": corr_level, "corr_diff": corr_diff,
        "mean_spread": float(d["spread"].mean()),
        "spread_2026": float(d["spread"].dropna().iloc[-1]),
        "sample": f"{d.index.min().date()}..{d.index.max().date()}",
    }
    # 图：中美10Y与利差
    fig, ax1 = plt.subplots(figsize=(10, 5))
    ax1.plot(d.index, d["us10"], label="US 10Y", color="#c0392b")
    ax1.plot(d.index, d["cn10"], label="CN 10Y", color="#2980b9")
    ax1.set_ylabel("Yield (%)")
    ax2 = ax1.twinx()
    ax2.fill_between(d.index, d["spread"], color="grey", alpha=0.2)
    ax2.axhline(0, color="black", lw=0.6)
    ax2.set_ylabel("CN - US 10Y spread (%)")
    ax1.legend(loc="upper right")
    ax1.set_title("China vs US 10Y Government Bond Yields and Spread")
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "spread_cn_us_10y.png"), dpi=130)
    plt.close(fig)
    return d


# ---------------------------------------------------------------------------
# 绘图
# ---------------------------------------------------------------------------
def plot_policy_rates(m, cn):
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(m["fed_funds_effective"].loc["2011":], label="US Fed Funds (eff.)", color="#c0392b")
    ax.plot(m["ust_3m"].loc["2011":], label="US 3M T-bill", color="#e67e22", alpha=0.7)
    ax.plot(cn["cn_shibor1w"].loc["2011":], label="CN SHIBOR 1W", color="#2980b9")
    ax.plot(cn["cn_lpr1y"].loc["2011":], label="CN LPR 1Y", color="#27ae60")
    ax.set_title("Policy / Money-Market Rates: US vs China (2011-)")
    ax.set_ylabel("%"); ax.legend()
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "policy_rates.png"), dpi=130)
    plt.close(fig)


def plot_taylor(res_us, d_us):
    fitted = res_us.fittedvalues
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(d_us.index, d_us["i"], label="Actual Fed Funds", color="black")
    ax.plot(d_us.index, fitted, label="Taylor-rule fitted", color="#c0392b", ls="--")
    ax.set_title("US: Estimated Taylor Rule Fit (with smoothing)")
    ax.set_ylabel("%"); ax.legend()
    fig.tight_layout(); fig.savefig(os.path.join(FIG, "taylor_us_fit.png"), dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------------------
def main():
    print("M1 载入数据 ...")
    m = load_us()
    cn = load_cn()
    m.to_csv(os.path.join(DATA, "panel_us_monthly.csv"))
    cn.to_csv(os.path.join(DATA, "panel_cn_monthly.csv"))
    print("  US panel", m.shape, "| CN panel", cn.shape, "| CN cols:", list(cn.columns))

    # 平稳性
    RESULTS["adf"] = {
        "us_fedfunds": adf_p(m["fed_funds_effective"]),
        "us_10y": adf_p(m["ust_10y"]),
        "cn_shibor1w": adf_p(cn["cn_shibor1w"]),
        "cn_10y": adf_p(cn["cn_10y"]) if "cn_10y" in cn else np.nan,
    }

    print("M2 泰勒规则 ...")
    res_us, d_us = taylor_us(m)
    res_cn, d_cn = taylor_cn(cn)
    plot_taylor(res_us, d_us)

    print("M3 利率传导 ECM ...")
    # 美国：联邦基金 -> 2Y / 10Y
    ecm_passthrough("us_ff_to_2y", m["fed_funds_effective"], m["ust_2y"], "2011")
    ecm_passthrough("us_ff_to_10y", m["fed_funds_effective"], m["ust_10y"], "2011")
    # 中国：SHIBOR1W -> 10Y；LPR1Y -> 10Y
    if "cn_10y" in cn:
        ecm_passthrough("cn_shibor_to_10y", cn["cn_shibor1w"], cn["cn_10y"], "2011")
        ecm_passthrough("cn_lpr_to_10y", cn["cn_lpr1y"], cn["cn_10y"], "2019")

    print("M4 波动率对比 ...")
    vol_compare(m, cn)

    print("M5 中美利差 ...")
    spread_analysis(m, cn)

    plot_policy_rates(m, cn)

    # 输出结果
    with open(os.path.join(REP, "results.json"), "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    write_results_md()
    print("DONE. 结果见 report/results.json, report/results.md, figures/*.png")


def write_results_md():
    r = RESULTS
    L = ["# 量化结果汇总 (auto-generated by analysis.py)\n"]
    L.append("> 本文件由代码自动生成，报告正文引用其中数值。运行环境见 code/requirements.txt。\n")

    L.append("## 1. 平稳性 ADF (p 值，原序列)\n")
    for k, v in r.get("adf", {}).items():
        L.append(f"- `{k}`: p = {v:.3f}" + ("（不能拒绝单位根→非平稳）" if (v==v and v>0.1) else ""))
    L.append("")

    def taylor_block(key, title, gap_name):
        t = r.get(key)
        if not t: return
        L.append(f"## {title}\n")
        L.append(f"样本：{t['sample']}，n={t['n']}，R²={t['R2']:.3f}\n")
        L.append("| 参数 | 系数 | t 值 |")
        L.append("|---|---|---|")
        for p in t["params"]:
            L.append(f"| {p} | {t['params'][p]:.3f} | {t['tvalues'][p]:.2f} |")
        lr = t["long_run"]
        L.append("")
        L.append(f"利率平滑 ρ = {lr['rho']:.3f}；长期反应系数：")
        for k, v in lr.items():
            if k != "rho":
                L.append(f"- {k} = {v:.3f}")
        L.append("")

    taylor_block("taylor_us", "2. 美国泰勒规则（核心PCE通胀 + 失业缺口，含平滑）", "u_gap")
    taylor_block("taylor_cn", "3. 中国类泰勒规则（CPI + GDP同比，含平滑）", "gdp")

    L.append("## 4. 利率传导 ECM（政策/货币市场利率 → 长端）\n")
    L.append("| 路径 | 长期传导β | 即期传导γ | 调整速度λ | 半衰期(月) | 协整p | n |")
    L.append("|---|---|---|---|---|---|---|")
    labels = {
        "ecm_us_ff_to_2y": "US: FedFunds→2Y",
        "ecm_us_ff_to_10y": "US: FedFunds→10Y",
        "ecm_cn_shibor_to_10y": "CN: SHIBOR1W→10Y",
        "ecm_cn_lpr_to_10y": "CN: LPR1Y→10Y",
    }
    for k, lab in labels.items():
        e = r.get(k)
        if e:
            hl = f"{e['half_life_months']:.1f}" if e['half_life_months']==e['half_life_months'] else "—"
            L.append(f"| {lab} | {e['long_run_passthrough']:.2f} | {e['impact_passthrough']:.2f} | {e['adj_speed_lambda']:.2f} | {hl} | {e['coint_p']:.3f} | {e['n']} |")
    L.append("")

    L.append("## 5. 利率水平与波动 (2011-, 月度)\n")
    L.append("| 序列 | 均值 | 标准差 | Δ标准差 | 最小 | 最大 |")
    L.append("|---|---|---|---|---|---|")
    for k, v in r.get("vol_compare", {}).items():
        L.append(f"| {k} | {v['mean']:.2f} | {v['std']:.2f} | {v['d_std']:.3f} | {v['min']:.2f} | {v['max']:.2f} |")
    L.append("")

    sp = r.get("spread")
    if sp:
        L.append("## 6. 中美 10Y 利差\n")
        L.append(f"- 样本：{sp['sample']}")
        L.append(f"- 水平相关系数 corr(CN10Y, US10Y) = {sp['corr_level']:.3f}")
        L.append(f"- 一阶差分相关 corr(ΔCN10Y, ΔUS10Y) = {sp['corr_diff']:.3f}")
        L.append(f"- 协整检验 p = {sp['coint_p_cn_us_10y']:.3f}")
        L.append(f"- 平均利差(CN-US) = {sp['mean_spread']:.2f}%，期末 = {sp['spread_2026']:.2f}%")
        L.append("")

    with open(os.path.join(REP, "results.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()
