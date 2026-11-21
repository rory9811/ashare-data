# ashare-data

每周自动计算的 A 股和美股行情表，供《A股主题账本》《美股主题账本》每周体检读取价格。
所有数字都由程序从复权收盘价算出，账本只引用，不手算。（仓库名沿用最初的 A 股版本，现在两个市场都在这里。）

| 市场 | 文件 | 数据源 | 证券范围 | 基准 | 运行时间（北京时间） |
| --- | --- | --- | --- | --- | --- |
| A 股 | `data/cn/ma_latest.csv`、`data/cn/history_w.csv`、`data/cn/meta.json` | baostock，前复权日线 | 全部 A 股、ETF、指数 | 沪深 300（000300.SH） | 周五 18:23，周六 03:17 补跑 |
| 美股 | `data/us/ma_latest.csv`、`data/us/history_w.csv`、`data/us/meta.json` | Yahoo Finance（yfinance），复权日线 | 全部在美上市股票（含 ADR）和 ETF，加主要指数、VIX、10 年期美债收益率、原油、天然气、黄金、白银、铜期货、美元指数 | SPY | 周六 06:13，09:43 补跑 |

GitHub 的定时任务常常排队晚几个小时，所以每个市场排两次。第二次发现数据已经覆盖最新交易日就直接跳过；A 股当天日线还没入库时也跳过，留给下一次。手动运行（Actions 页面 Run workflow）或改了代码触发的运行总是重新计算。

## `ma_latest.csv` 的列（两个市场相同）

| 列 | 含义 |
| --- | --- |
| code | 代码。A 股如 600519.SH；美股用 Yahoo 写法，如 MU、BRK-B、^GSPC、BZ=F |
| name、kind | 名称；股票、ETF、指数、利率、商品或汇率 |
| date | 最后一个有数据的交易日 |
| close | 复权收盘价 |
| ma50、ma200 | 50 日、200 日均线 |
| trend | 方向：上涨、上涨中回调、转跌、下跌、下跌中反弹、转涨、数据不足 |
| chg_1w、chg_4w、chg_13w | 近 1、4、13 周（5、20、60 个交易日）涨跌幅，% |
| rel_1w、rel_4w、rel_13w | 相对基准：同期涨跌幅减去基准的涨跌幅，百分点 |
| high_250、pct_from_high | 近 250 个交易日最高收盘价，收盘价距它的百分比 |

方向规则：50 日均线在 200 日均线之上时，收盘价在 50 日均线之上记“上涨”，在 50 日和 200 日均线之间记“上涨中回调”，跌破 200 日均线记“转跌”；50 日均线在 200 日均线之下时，收盘价在 50 日均线之下记“下跌”，在两条均线之间记“下跌中反弹”，站上 200 日均线记“转涨”；不足 200 个交易日记“数据不足”。收盘价在 200 日均线之上的三种（上涨、上涨中回调、转涨）归“上涨一边”，其余三种归“下跌一边”。

美股的 ^TNX 是 10 年期美债收益率乘以 10（例如 43.5 表示 4.35%）。

## `history_w.csv`：两年周线（画走势图用）

每行一个代码，第一列是代码，后面 104 列各是一周：值为那一周最后一个交易日的复权收盘价，列名写那一周的周五日期；那周没有数据（尚未上市、停牌）就留空。和 `ma_latest.csv` 出自同一次取数，所以图和数字对得上。

## 读取（不用登录）

只取需要的行，行首匹配加逗号，避免误匹配：

```
curl -sL https://raw.githubusercontent.com/rory9811/ashare-data/main/data/cn/ma_latest.csv | grep -E "^code,|^(000300\.SH|600519\.SH),"
curl -sL https://raw.githubusercontent.com/rory9811/ashare-data/main/data/us/ma_latest.csv | grep -E "^code,|^(SPY|MU|\^GSPC),"
```

## 代码

- `scripts/common.py`：两个市场共用的计算和输出
- `scripts/update_cn.py`、`scripts/update_us.py`：各市场取数
- `.github/workflows/cn-weekly.yml`、`us-weekly.yml`：定时运行并提交数据
