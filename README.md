# ashare-data

每周五傍晚自动更新的 A 股均线表，供《A股主题账本》每周体检读取价格趋势。

- 数据源：baostock，前复权日线，免费、无需注册
- 运行：GitHub Actions，北京时间周五 18:23 一次、周六 03:17 补一次（GitHub 定时常排队晚几个小时）。当天日线 baostock 还没入库就跳过，留给下一次；已是最新就不重复计算。也可以在 Actions 页面手动运行，手动运行总是重新计算
- 输出：
  - `data/ma_latest.csv`：每只股票、ETF、指数一行
  - `data/meta.json`：本次运行的交易日、成功和失败数量

## `ma_latest.csv` 的列

| 列 | 含义 |
| --- | --- |
| code | 代码，如 600519.SH、300750.SZ |
| name | 名称 |
| kind | 股票、指数或 ETF |
| date | 最后一个有数据的交易日 |
| close | 收盘价（前复权） |
| ma50 / ma200 | 50 日、200 日均线 |
| status | 完好、破位、震荡、数据不足 |
| high_250 | 近 250 个交易日最高收盘价 |
| pct_from_high | 收盘价距这个高点的百分比 |

状态规则：收盘价和 50 日均线都在 200 日均线之上记“完好”，都在之下记“破位”，其余记“震荡”，不足 200 个交易日记“数据不足”。

读取方式（不用登录）：

```
curl -sL https://raw.githubusercontent.com/rory9811/ashare-data/main/data/ma_latest.csv | grep -E "600519.SH|300750.SZ"
```
