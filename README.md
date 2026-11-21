# ashare-data

每周自动计算的 A 股和美股行情表，供《A股主题账本》《美股主题账本》每周体检读取价格。
所有数字都由程序从复权收盘价算出，账本只引用，不手算。（仓库名沿用最初的 A 股版本，现在两个市场都在这里。）

| 市场 | 文件 | 数据源 | 证券范围 | 基准 | 运行时间（北京时间） |
| --- | --- | --- | --- | --- | --- |
| A 股 | `data/cn/ma_latest.csv`、`data/cn/history_w.csv`、`data/cn/meta.json` | baostock，前复权日线 | 全部 A 股、ETF、指数 | 沪深 300（000300.SH） | 周五 18:23，周六 03:17 补跑 |
| 美股 | `data/us/ma_latest.csv`、`data/us/history_w.csv`、`data/us/meta.json` | Yahoo Finance（yfinance），复权日线 | 全部在美上市股票（含 ADR）和 ETF，加主要指数、VIX、10 年期美债收益率、原油、天然气、黄金、白银、铜期货、美元指数 | SPY | 周六 06:13，09:43 补跑 |

美股批量下载偶尔漏掉个别代码的最后几根日线（例如拆股当天），程序会逐个重取一次；仍然停在最新交易日之前的代码和日期写在 `meta.json` 的 `stale` 里，账本引用这些代码时要注明数据日期并另行核对。

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

画一个主题的走势图（下载周线和脚本，再运行；只用到 matplotlib）：

```
mkdir -p /tmp/chart && cd /tmp/chart
curl -sL https://raw.githubusercontent.com/rory9811/ashare-data/main/data/us/history_w.csv -o history_w.csv
curl -sL https://raw.githubusercontent.com/rory9811/ashare-data/main/scripts/theme_chart.py -o theme_chart.py
python3 -I theme_chart.py --market us --history history_w.csv --codes MU,WDC,STX --out storage
```

得到 `storage.png`（给模型看：上图是走势，起点记为 100；下图是相对基准的强弱；纵轴都是对数刻度，同样的高度代表同样的涨跌幅度）、`storage.widget.txt`（账本文档里图表组件的代码）和打印出来的数字摘要（区间涨跌、两年内高点和日期、距高点回撤、相对基准），以及“涨势体质”：两年涨幅、加速度、波动变化、上涨周占比，和主题是否达到急涨门槛（两年涨幅 ≥100% 且超出基准 ≥100 个百分点，取自 Greenwood、Shleifer、You 2019 对行业泡沫的研究）。图只用来看形状，数字以摘要和 `ma_latest.csv` 为准。

## 代码

- `scripts/common.py`：两个市场共用的计算和输出
- `scripts/update_cn.py`、`scripts/update_us.py`：各市场取数
- `scripts/theme_chart.py`：按代码画主题走势图
- `.github/workflows/cn-weekly.yml`、`us-weekly.yml`：定时运行并提交数据
