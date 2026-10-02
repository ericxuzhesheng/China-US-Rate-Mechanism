"""Quarter-cutoff snapshots and sample sensitivity for the existing rolling Taylor specification."""
import argparse
import io
import json
import os
from pathlib import Path
from urllib.request import urlopen

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm

ROOT = Path(__file__).resolve().parents[1]


def monthly(series, aggregation="last"):
    series = series.sort_index().dropna()
    return getattr(series.groupby(series.index.to_period("M").to_timestamp("M")), aggregation)()


def design(panel, country):
    if country == "US":
        frame = panel[["fed_funds_effective", "core_pce_yoy"]].rename(columns={"fed_funds_effective": "rate", "core_pce_yoy": "inflation"})
    else:
        frame = panel[["cn_shibor1w", "cn_cpi_yoy"]].rename(columns={"cn_shibor1w": "rate", "cn_cpi_yoy": "inflation"})
    # Reindex BEFORE lagging: a missing month must not be silently skipped.
    frame = frame.reindex(pd.date_range(frame.index.min(), frame.index.max(), freq="ME"))
    frame["lag_rate"] = frame.rate.shift(1)
    return frame.dropna()


def estimate(frame):
    if len(frame) < 36:
        raise ValueError("At least 36 complete monthly observations are required")
    fit = sm.OLS(frame.rate, sm.add_constant(frame[["inflation", "lag_rate"]])).fit(cov_type="HAC", cov_kwds={"maxlags": 12})
    beta, rho = float(fit.params.inflation), float(fit.params.lag_rate)
    denominator = 1 - rho
    stable = abs(denominator) >= .05 and abs(rho) < 1
    phi = beta / denominator if abs(denominator) > 1e-8 else np.nan
    gradient = np.array([1 / denominator, beta / denominator**2]) if abs(denominator) > 1e-8 else np.full(2, np.nan)
    covariance = fit.cov_params().loc[["inflation", "lag_rate"], ["inflation", "lag_rate"]].to_numpy()
    se = float(np.sqrt(max(0., gradient @ covariance @ gradient))) if np.isfinite(gradient).all() else np.nan
    return {"start": str(frame.index.min().date()), "end": str(frame.index.max().date()), "n": len(frame),
        "beta_inflation": beta, "rho": rho, "phi_long_run": phi, "phi_hac_se_delta": se,
        "phi_ci_low": phi - 1.96 * se, "phi_ci_high": phi + 1.96 * se,
        "long_run_stable": stable, "r_squared": float(fit.rsquared)}


def load_panels(cutoff, refresh):
    us = pd.read_csv(ROOT / "data/panel_us_monthly.csv", index_col=0, parse_dates=[0])
    cn = pd.read_csv(ROOT / "data/panel_cn_monthly.csv", index_col=0, parse_dates=[0])
    snapshot = ROOT / "data" / f"validation_{cutoff.strftime('%Y%m%d')}"
    snapshot.mkdir(parents=True, exist_ok=True)
    receipt = []
    if refresh:
        sources = {"FEDFUNDS": "fed_funds_effective", "PCEPILFE": "core_pce", "UNRATE": "unemployment", "DGS2": "ust_2y", "DGS10": "ust_10y"}
        series = {}
        for source, column in sources.items():
            url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={source}&cosd=1989-01-01&coed={cutoff.date()}"
            try:
                data = pd.read_csv(io.BytesIO(urlopen(url, timeout=30).read()))
                data.columns = ["date", "value"]
                data.date = pd.to_datetime(data.date)
                data.value = pd.to_numeric(data.value, errors="coerce")
                data = data.loc[data.date <= cutoff].dropna()
                if data.empty:
                    raise ValueError("empty result")
                data.to_csv(snapshot / f"{source}.csv", index=False)
                series[column] = monthly(data.set_index("date").value)
                receipt.append({"source": source, "url": url, "status": "refreshed", "last_observation": str(data.date.max().date())})
            except Exception as error:
                receipt.append({"source": source, "url": url, "status": "cached_fallback", "error_type": type(error).__name__})
        if series:
            fresh = pd.DataFrame(series)
            us = fresh.combine_first(us)
            if "core_pce" in series:
                # Do not bridge absent monthly price observations with forward fill.
                prices = series["core_pce"].reindex(pd.date_range(series["core_pce"].index.min(), series["core_pce"].index.max(), freq="ME"))
                us["core_pce_yoy"] = prices.pct_change(12, fill_method=None) * 100
        import tushare as ts
        pro = ts.pro_api(os.environ.get("TUSHARE_TOKEN") or os.environ.get("TS_TOKEN", ""), timeout=30)
        for endpoint, params in (("shibor", {"start_date": "20260101", "end_date": cutoff.strftime("%Y%m%d")}),
                                 ("cn_cpi", {"start_m": "202601", "end_m": cutoff.strftime("%Y%m")})):
            try:
                data = getattr(pro, endpoint)(**params)
                if data.empty:
                    raise ValueError("empty result")
                if endpoint == "shibor":
                    dates = pd.to_datetime(data.date)
                    values = pd.to_numeric(data["1w"], errors="raise")
                    column, aggregation = "cn_shibor1w", "mean"
                else:
                    dates = pd.to_datetime(data.month.astype(str), format="%Y%m") + pd.offsets.MonthEnd(0)
                    values = pd.to_numeric(data.nt_yoy, errors="raise")
                    column, aggregation = "cn_cpi_yoy", "last"
                series_cn = pd.Series(values.to_numpy(), index=pd.DatetimeIndex(dates))
                series_cn = series_cn[series_cn.index <= cutoff]
                fresh = monthly(series_cn, aggregation)
                cn = cn.reindex(cn.index.union(fresh.index))
                cn.loc[fresh.index, column] = fresh
                data.to_csv(snapshot / f"{endpoint}.csv", index=False)
                receipt.append({"source": endpoint, "params": params, "status": "refreshed", "last_observation": str(series_cn.index.max().date())})
            except Exception as error:
                receipt.append({"source": endpoint, "params": params, "status": "cached_fallback", "error_type": type(error).__name__})
    else:
        receipt.append({"source": "repository monthly panels", "status": "cached_only"})
        if (snapshot / "us_panel.csv").exists() and (snapshot / "cn_panel.csv").exists():
            us = pd.read_csv(snapshot / "us_panel.csv", index_col=0, parse_dates=[0])
            cn = pd.read_csv(snapshot / "cn_panel.csv", index_col=0, parse_dates=[0])
            receipt = json.loads((snapshot / "sources.json").read_text(encoding="utf-8"))["sources"]
    us, cn = us.loc[us.index <= cutoff].sort_index(), cn.loc[cn.index <= cutoff].sort_index()
    us.to_csv(snapshot / "us_panel.csv", index_label="date")
    cn.to_csv(snapshot / "cn_panel.csv", index_label="date")
    coverage = [{"country": country, "column": column, "last_observation": str(frame[column].dropna().index.max().date()) if frame[column].notna().any() else None}
                for country, frame, columns in (("US", us, ["fed_funds_effective", "core_pce_yoy", "ust_10y"]),
                                                 ("CN", cn, ["cn_shibor1w", "cn_cpi_yoy", "cn_10y"])) for column in columns]
    provenance = {"requested_cutoff": str(cutoff.date()), "sources": receipt, "coverage": coverage,
                  "vintage": "current retrieved/repository vintage; observation cutoff is not a historical release-time vintage"}
    (snapshot / "sources.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return us, cn, provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff", default="2026-09-30")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    cutoff = pd.Timestamp(args.cutoff)
    if not cutoff.is_quarter_end:
        raise ValueError("Use a calendar quarter-end cutoff")
    us, cn, provenance = load_panels(cutoff, args.refresh)
    samples, rolling = [], []
    designs = {"US": design(us, "US"), "CN": design(cn, "CN")}
    # Compare countries on exactly the same complete months.
    common = designs["US"].index.intersection(designs["CN"].index)
    for country, frame in designs.items():
        for label, start in (("common_2011", "2011"), ("common_2017", "2017"), ("common_2020", "2020")):
            selected = frame.loc[frame.index.intersection(common)].loc[start:]
            if len(selected) >= 36:
                samples.append({"country": country, "sample": label, **estimate(selected)})
        selected = frame.loc[frame.index.intersection(common)].loc["2011":]
        selected = selected.loc[~selected.index.year.isin([2020, 2021])]
        if len(selected) >= 36:
            samples.append({"country": country, "sample": "exclude_2020_2021", **estimate(selected)})
        for end in range(60, len(frame) + 1):
            window = frame.iloc[end - 60:end]
            if len(pd.date_range(window.index[0], window.index[-1], freq="ME")) != 60:
                continue
            rolling.append({"country": country, **estimate(window)})
    out = ROOT / "report" / "quarterly_validation"
    out.mkdir(parents=True, exist_ok=True)
    samples, rolling = pd.DataFrame(samples), pd.DataFrame(rolling)
    samples.to_csv(out / "sample_sensitivity.csv", index=False)
    rolling.to_csv(out / "rolling_parameters.csv", index=False)
    coverage = pd.DataFrame(provenance["coverage"])
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    for country, frame in rolling.groupby("country"):
        dates = pd.to_datetime(frame.end)
        axes[0].plot(dates, frame.beta_inflation, label=country)
        axes[1].plot(dates, frame.rho, label=country)
    axes[0].set_ylabel("Inflation coefficient (short run)")
    axes[1].set_ylabel("Lagged-rate coefficient")
    axes[1].axhline(1, color="gray", linestyle="--", linewidth=.8)
    for ax in axes:
        ax.legend(); ax.grid(alpha=.2)
    fig.suptitle("60-month rolling descriptive rate regressions (unclipped)")
    fig.tight_layout()
    fig.savefig(out / "rolling_parameters.png", dpi=150)
    plt.close(fig)
    report = "# Quarterly validation / 季度更新与样本敏感性\n\n"
    report += f"请求截止 {cutoff.date()}；各字段实际截止见下表。\n\n" + coverage.to_markdown(index=False) + "\n\n"
    report += samples.to_markdown(index=False, floatfmt=".4f")
    report += "\n\n![Rolling parameters](rolling_parameters.png)\n\n"
    report += "采用原 analysis3.py 的双变量滚动口径：利率 ~ 常数 + 通胀 + 滞后利率。美国为有效联邦基金利率/核心 PCE 同比，中国为 SHIBOR 1 周/CPI 同比；后者是货币市场利率代理，不能当作央行直接政策反应。此表不替代原包含就业/GDP 控制项的多变量结果。两国子样本使用同一组完整月份；滞后在完整月历上生成，删去 2020–2021 年不会把 2019 年错误当作 2022 年的上一月。\n\n"
    report += "标准误为 12 阶 HAC，长期系数为 beta/(1-rho)，区间使用 delta method。接近单位根时该近似可能不可靠：|1-rho|<0.05 或 |rho|>=1 标为不稳定，不能据其点估计判断 Taylor 原则。滚动曲线保留原数值，不再裁剪至固定范围。多次子样本比较为描述性敏感性分析，未经多重检验校正。\n\n"
    report += "最新观测月份与公开发布日不同；本快照不是历史实时信息集。中国国债收益率沿用仓库存量，本轮刷新聚焦反应函数变量，故不能把所有旧图表称为更新至请求日。请求状态与失败后的缓存回退记录在 data/validation_日期/sources.json。\n\n"
    report += "复跑缓存：`python code/quarterly_validation.py --cutoff 2026-09-30`；刷新：追加 `--refresh`。新季度改 cutoff，旧版面板和论文主表保留。\n"
    (out / "report.md").write_text(report, encoding="utf-8")
    print(json.dumps(provenance, indent=2))
    print(samples.to_string(index=False))


if __name__ == "__main__":
    main()
