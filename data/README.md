# 数据说明 / Data Dictionary

所有 CSV 由 `code/data_fetch.py` 抓取自公开来源。日期为 ISO 格式。

| 文件 | 内容 | 来源 | 关键列 |
|---|---|---|---|
| `us_raw.csv` | 美国利率/宏观原始序列 | FRED | fed_funds_effective, ust_2y, ust_10y, core_pce, unemployment ... |
| `cn_bond_yield.csv` | 中债国债收益率曲线（多期限，日度） | 中债登 (akshare) | 曲线名称, 日期, 1年/2年/10年 ... |
| `cn_shibor_1w.csv` / `cn_shibor_on.csv` | SHIBOR 1周/隔夜（日度） | 同业拆借中心 (akshare) | 日期, 利率 |
| `cn_lpr.csv` | LPR 历史（事件频率） | 央行 (akshare) | TRADE_DATE, LPR1Y, LPR5Y |
| `cn_cpi_table.csv` | CPI 全国/城市/农村 同比、环比 | 统计局 (akshare) | 月份, 全国-同比涨幅 |
| `cn_gdp_yearly.csv` | GDP 同比（季度） | 统计局 (akshare) | 日期, 现值 |
| `panel_us_monthly.csv` | 对齐后的美国月度面板 | 本项目派生 | — |
| `panel_cn_monthly.csv` | 对齐后的中国月度面板 | 本项目派生 | cn_shibor1w, cn_10y, cn_lpr1y, cn_cpi_yoy, cn_gdp_yoy |

> 部分原始 CSV 的列名为中文（utf-8-sig 编码）。在终端用 GBK 显示可能呈乱码，但文件本身正确。
> 数据随时间更新；复现时数值可能因数据修订而轻微变动。
