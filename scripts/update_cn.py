"""A 股：每周更新全部股票、ETF 和指数的均线、方向、涨跌幅和相对沪深 300 强弱，以及两年周线。

数据源：baostock（免费、无需注册），前复权日线。baostock 不处理 ETF 的份额拆分，这里按价格跳变识别并折算
（见 adjust_etf_splits），处理过的 ETF 记在 meta.json 的 etf_splits_adjusted 和 etf_split_sample。
输出：data/cn/ma_latest.csv、history_w.csv、ohlcv/<代码>.csv、meta.json（列见 common.py 和 README）。

六千多个代码逐个查询，一台机器要三四个小时，所以分三步，由工作流把中间一步并行：
  plan             决定这次要不要算、算哪个交易日（写进 GITHUB_OUTPUT）：
                   - 最新交易日的日线 baostock 还没入库时跳过，留给下一次定时运行；
                   - 定时运行时，数据已覆盖最新交易日且成功率达标，也跳过；
                   - 手动运行或改了代码触发的运行（环境变量 FORCE=1）总是重新计算。
  fetch --shard i --of n --trade-day D
                   取全部代码里第 i 份（按代码排序后每隔 n 个取一个），结果写到 shards/cn-i.json。
  merge --of n     读齐 n 份结果，算相对沪深 300 的强弱，写出最终文件；缺任何一份都报错，不发布残缺数据。
不带参数运行等于 plan + fetch（只分一份）+ merge，供本地手动使用。
"""

import argparse
import datetime as dt
import glob
import json
import os
import sys
import time

import baostock as bs

from common import add_liquidity_rank, add_relative, already_done, summarize, weekly_bars, write_outputs

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT_DIR = os.path.join(ROOT, "data", "cn")
SHARD_DIR = os.path.join(ROOT, "shards")
BENCH = "000300.SH"
KEEP_TYPES = {"1": "股票", "2": "指数", "5": "ETF"}
LOOKBACK_CALENDAR_DAYS = 760  # 两年多一点：够画 104 周走势，也够算 200 日均线和 250 日高点
# A 股 ETF 单日涨跌幅最多 20%（多数是 10%），收盘价一天之内变成前一天的 1.35 倍以上或 1/1.35 以下，
# 只可能是份额拆分或合并。baostock 的前复权只处理股票的送转和分红，不处理 ETF 的拆分。
ETF_SPLIT_JUMP = 1.35


def to_display_code(bs_code):
    """sh.600519 -> 600519.SH"""
    market, num = bs_code.split(".")
    return f"{num}.{market.upper()}"


def login_with_retry(tries=3):
    for i in range(tries):
        lg = bs.login()
        if lg.error_code == "0":
            return
        print(f"login failed ({lg.error_code} {lg.error_msg}), retry {i + 1}", file=sys.stderr)
        time.sleep(10)
    raise SystemExit("baostock login failed")


def latest_trade_day(today):
    start = (today - dt.timedelta(days=20)).isoformat()
    rs = bs.query_trade_dates(start_date=start, end_date=today.isoformat())
    days = []
    while rs.error_code == "0" and rs.next():
        d, is_trading = rs.get_row_data()
        if is_trading == "1":
            days.append(d)
    if not days:
        raise SystemExit("no trading day found")
    return days[-1]


def _f(x):
    return float(x) if x else None


def fetch_bars(code, start, end):
    """前复权日线：返回 ({日期, 开, 高, 低, 收, 成交额} 各一列, 错误信息)。停牌日没有收盘价，跳过。"""
    rs = bs.query_history_k_data_plus(code, "date,open,high,low,close,amount", start_date=start,
                                      end_date=end, frequency="d", adjustflag="2")
    if rs.error_code != "0":
        return None, rs.error_msg
    cols = {k: [] for k in ("dates", "opens", "highs", "lows", "closes", "amounts")}
    while rs.next():
        d, o, h, l, c, a = rs.get_row_data()
        if c:
            for k, v in zip(cols, (d, _f(o), _f(h), _f(l), float(c), _f(a))):
                cols[k].append(v)
    return cols, None


def adjust_etf_splits(bars):
    """把 ETF 份额拆分（或合并）之前的开高低收按拆分比例折算，效果等同前复权；成交额不受拆分影响，不改。
    比例用拆分当天的开盘价 ÷ 前一天收盘价估计：开盘价已经按新份额计价，和真实比例只差隔夜涨跌，通常不到 1%。
    返回发生拆分的日期列表。"""
    closes, opens = bars["closes"], bars["opens"]
    split_days = []
    for i in range(1, len(closes)):
        jump = closes[i] / closes[i - 1]
        if 1 / ETF_SPLIT_JUMP <= jump <= ETF_SPLIT_JUMP:
            continue
        factor = (opens[i] or closes[i]) / closes[i - 1]
        for key in ("opens", "highs", "lows", "closes"):
            series = bars[key]
            for j in range(i):
                if series[j] is not None:
                    series[j] *= factor
        split_days.append(bars["dates"][i])
    return split_days


def day_loaded(trade_day):
    """用上证指数探一下：最新交易日的日线是否已经入库。"""
    bars, err = fetch_bars("sh.000001", trade_day, trade_day)
    return err is None and bool(bars["dates"]) and bars["dates"][-1] == trade_day


def fetch_universe():
    rs = bs.query_stock_basic()
    rows = []
    while rs.error_code == "0" and rs.next():
        code, name, _ipo, _out, typ, status = rs.get_row_data()
        if status == "1" and typ in KEEP_TYPES:
            rows.append((code, name, KEEP_TYPES[typ]))
    return sorted(rows)


def now_utc():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def plan():
    """返回要计算的交易日；不需要计算时返回 None。"""
    login_with_retry()
    try:
        trade_day = latest_trade_day(dt.date.today())
        if os.environ.get("FORCE") != "1" and already_done(OUT_DIR, trade_day):
            print(f"cn data for {trade_day} already up to date, skip")
            return None
        if not day_loaded(trade_day):
            print(f"{trade_day} daily bars not loaded in baostock yet, skip; the next scheduled run retries")
            return None
        return trade_day
    finally:
        bs.logout()


def fetch(shard, of, trade_day):
    started = now_utc()
    start = (dt.date.fromisoformat(trade_day) - dt.timedelta(days=LOOKBACK_CALENDAR_DAYS)).isoformat()
    login_with_retry()
    try:
        universe = fetch_universe()
        part = universe[shard::of]
        print(f"trade day {trade_day}, shard {shard}/{of}: {len(part)} of {len(universe)} instruments", flush=True)
        rows, history, failures, splits = [], {}, [], []
        for i, (code, name, kind) in enumerate(part, 1):
            bars, err = fetch_bars(code, start, trade_day)
            if err or not bars["closes"]:
                failures.append({"code": code, "error": err or "empty"})
            else:
                shown = to_display_code(code)
                if kind == "ETF":
                    days = adjust_etf_splits(bars)
                    if days:
                        splits.append({"code": shown, "dates": days})
                rows.append(summarize(shown, name, kind, bars["dates"], bars["closes"], bars["amounts"]))
                history[shown] = weekly_bars(**bars)
            if i % 200 == 0:
                print(f"{i}/{len(part)}", flush=True)
    finally:
        bs.logout()
    os.makedirs(SHARD_DIR, exist_ok=True)
    with open(os.path.join(SHARD_DIR, f"cn-{shard}.json"), "w", encoding="utf-8") as f:
        json.dump({"shard": shard, "of": of, "trade_day": trade_day, "universe": len(universe),
                   "part": len(part), "started": started, "rows": rows, "history": history,
                   "failures": failures, "etf_splits": splits}, f, ensure_ascii=False)


def merge(of):
    parts = [json.load(open(p, encoding="utf-8")) for p in sorted(glob.glob(os.path.join(SHARD_DIR, "cn-*.json")))]
    got = sorted(p["shard"] for p in parts)
    if got != list(range(of)) or any(p["of"] != of for p in parts):
        raise SystemExit(f"expected shards 0..{of - 1}, got {got}")
    trade_days = {p["trade_day"] for p in parts}
    universes = {p["universe"] for p in parts}
    if len(trade_days) != 1 or len(universes) != 1:
        raise SystemExit(f"shards disagree: trade days {trade_days}, universe sizes {universes}")
    total = universes.pop()
    if sum(p["part"] for p in parts) != total:
        raise SystemExit(f"shards cover {sum(p['part'] for p in parts)} of {total} instruments")
    trade_day = trade_days.pop()
    universe = total
    rows = [r for p in parts for r in p["rows"]]
    history = {c: b for p in parts for c, b in p["history"].items()}
    failures = [f for p in parts for f in p["failures"]]
    splits = sorted((s for p in parts for s in p.get("etf_splits", [])), key=lambda s: s["code"])
    has_bench = add_relative(rows, BENCH)
    add_liquidity_rank(rows)
    write_outputs(OUT_DIR, rows, {
        "market": "cn",
        "source": "baostock，前复权日线",
        "benchmark": BENCH if has_bench else None,
        "run_started_utc": min(p["started"] for p in parts),
        "run_finished_utc": now_utc(),
        "trade_day": trade_day,
        "instruments": universe,
        "ok": len(rows),
        "failed": len(failures),
        "failed_sample": failures[:20],
        "etf_splits_adjusted": len(splits),
        "etf_split_sample": splits[:20],
        "shards": of,
    }, history)
    if not rows or len(rows) < 0.8 * universe:
        raise SystemExit("too many failures")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", nargs="?", choices=["plan", "fetch", "merge"])
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--of", type=int, default=1)
    ap.add_argument("--trade-day")
    args = ap.parse_args()
    if args.step == "plan":
        trade_day = plan()
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"run={'true' if trade_day else 'false'}\ntrade_day={trade_day or ''}\n")
    elif args.step == "fetch":
        if not args.trade_day:
            raise SystemExit("fetch needs --trade-day")
        fetch(args.shard, args.of, args.trade_day)
    elif args.step == "merge":
        merge(args.of)
    else:
        trade_day = plan()
        if trade_day:
            fetch(0, 1, trade_day)
            merge(1)


if __name__ == "__main__":
    main()
