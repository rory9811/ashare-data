"""趋势扫描：从价格倒推主题时用的原材料。

读数据分支里的 ma_latest.csv，按近 13 周相对大盘的强弱（rel_13w）把三组资产排一遍：
  一、大类资产和主要指数（美股：指数、利率、商品、汇率；A 股：几只宽基指数）——全部列出；
  二、成交额最大的股票（amt_pct ≥ 95，约为本市场前 5%；A 股排除 ST）——最强、最弱各 N 只；
  三、成交最活跃的 ETF（日均成交额排在本市场 ETF 前 10%，排除杠杆、反向和期权收益类）
      ——最强、最弱各 N 只。
它只给原材料。同一类资产（同一行业、同一种商品、同一个国家或同一种风格）一起出现在某一头，
说明可能有一股力量在推它们；是不是主题、驱动是什么、能不能持续几个季度，要去找原因、
看结构，按账本规则判断。找不到驱动的只是“现象”。
为什么这样找，见《股票价格理论手册》“驱动”一节的“从价格倒推”。

用法：
  python3 -I trend_scan.py --market us            # 自动下载最新数据
  python3 -I trend_scan.py --market cn --top 30
  python3 -I trend_scan.py --market us --csv ma_latest.csv
只依赖标准库和 curl。
"""

import argparse
import csv
import io
import re
import subprocess
import sys

DATA_URL = "https://raw.githubusercontent.com/rory9811/ashare-data/data-{m}/{m}/ma_latest.csv"
BENCH = {"us": "SPY", "cn": "000300.SH"}
# A 股没有商品和利率行，只列宽基指数；美股按 kind 取全部大类资产
CN_INDEXES = ["000001.SH", "000300.SH", "000905.SH", "000852.SH", "399006.SZ", "000688.SH"]
MACRO_KINDS = {"指数", "利率", "商品", "汇率"}
BIG_STOCK_PCT = 95
ETF_TOP_SHARE = 0.10
# 杠杆、反向和期权收益类 ETF 放大或改写了标的的走势，不代表一个主题
ETF_EXCLUDE = re.compile(r"2x|3x|1\.5x|ultra|\bbull\b|\bbear\b|inverse|\bshort\b|leveraged|\bdaily\b|"
                         r"option income|yieldmax|covered call|buywrite|buffer", re.I)


def load(market, path):
    if path:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    else:
        out = subprocess.run(["curl", "-sfL", "--retry", "3", DATA_URL.format(m=market)],
                             capture_output=True, text=True)
        if out.returncode != 0 or not out.stdout.startswith("code,"):
            sys.exit(f"读不到 {DATA_URL.format(m=market)}")
        text = out.stdout
    return list(csv.DictReader(io.StringIO(text)))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def line(r):
    pct = r.get("amt_pct") or "—"
    return (f"{r['code']:<11} {r['name'][:26]:<26} {r['trend']:<6} "
            f"13周 {fmt(r['chg_13w'])}  相对13周 {fmt(r['rel_13w'])}  相对4周 {fmt(r['rel_4w'])}  "
            f"距一年高点 {fmt(r['pct_from_high'])}  成交额分位 {pct}")


def fmt(v):
    x = num(v)
    return "   —  " if x is None else f"{x:+6.1f}"


def ends(rows, n):
    ranked = sorted((r for r in rows if num(r["rel_13w"]) is not None), key=lambda r: num(r["rel_13w"]))
    return ranked[::-1][:n], ranked[:n], len(ranked)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=sorted(BENCH), required=True)
    ap.add_argument("--top", type=int, default=25, help="股票和 ETF 每头列几只，默认 25")
    ap.add_argument("--csv", help="本地 ma_latest.csv；不给就下载")
    args = ap.parse_args()

    rows = load(args.market, args.csv)
    dates = sorted({r["date"] for r in rows if r["date"]})
    print(f"数据日期 {dates[-1] if dates else '未知'}；基准 {BENCH[args.market]}；"
          f"相对 = 同期涨跌减去基准涨跌（百分点）；方向按 50 日和 200 日均线")

    if args.market == "us":
        macro = [r for r in rows if r["kind"] in MACRO_KINDS]
    else:
        by_code = {r["code"]: r for r in rows}
        macro = [by_code[c] for c in CN_INDEXES if c in by_code]
    print("\n一、大类资产和主要指数（按相对 13 周从强到弱）")
    for r in sorted(macro, key=lambda r: -(num(r["rel_13w"]) or 0)):
        print("  " + line(r))

    stocks = [r for r in rows if r["kind"] == "股票" and (num(r["amt_pct"]) or 0) >= BIG_STOCK_PCT
              and not (args.market == "cn" and "ST" in r["name"].upper())]
    top, bottom, n = ends(stocks, args.top)
    print(f"\n二、成交额最大的股票（成交额分位 ≥ {BIG_STOCK_PCT}，共 {n} 只）")
    print("  最强：")
    for r in top:
        print("    " + line(r))
    print("  最弱：")
    for r in bottom:
        print("    " + line(r))

    etfs = [r for r in rows if r["kind"] == "ETF" and num(r["amt_20d"])]
    etfs.sort(key=lambda r: -num(r["amt_20d"]))
    active = [r for r in etfs[:max(1, round(len(etfs) * ETF_TOP_SHARE))] if not ETF_EXCLUDE.search(r["name"])]
    top, bottom, n = ends(active, args.top)
    print(f"\n三、成交最活跃的 ETF（日均成交额排在本市场 ETF 前 {round(ETF_TOP_SHARE * 100)}%，"
          f"去掉杠杆、反向和期权收益类后共 {n} 只）")
    print("  最强：")
    for r in top:
        print("    " + line(r))
    print("  最弱：")
    for r in bottom:
        print("    " + line(r))


if __name__ == "__main__":
    main()
