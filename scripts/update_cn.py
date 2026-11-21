"""A 股：每周更新全部股票、ETF 和指数的均线、方向、涨跌幅和相对沪深 300 强弱。

数据源：baostock（免费、无需注册），前复权日线。
输出：data/cn/ma_latest.csv、data/cn/history_w.csv、data/cn/meta.json（列见 common.py 和 README）。

每周排了两次（周五傍晚、周六凌晨补跑）：
  - 最新交易日的日线 baostock 还没入库时，本次跳过，不写文件，留给下一次；
  - 定时运行时，数据已覆盖最新交易日且成功率达标，也跳过；
  - 手动运行或改了代码触发的运行（环境变量 FORCE=1）总是重新计算。
"""

import datetime as dt
import os
import sys
import time

import baostock as bs

from common import add_relative, already_done, summarize, weekly_closes, write_outputs

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT_DIR = os.path.join(ROOT, "data", "cn")
LEGACY = [os.path.join(ROOT, "data", n) for n in ("ma_latest.csv", "meta.json")]
BENCH = "000300.SH"
KEEP_TYPES = {"1": "股票", "2": "指数", "5": "ETF"}
LOOKBACK_CALENDAR_DAYS = 760  # 两年多一点：够画 104 周走势，也够算 200 日均线和 250 日高点


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


def fetch_closes(code, start, end):
    rs = bs.query_history_k_data_plus(code, "date,close", start_date=start, end_date=end,
                                      frequency="d", adjustflag="2")
    if rs.error_code != "0":
        return None, None, rs.error_msg
    dates, closes = [], []
    while rs.next():
        d, c = rs.get_row_data()
        if c:
            dates.append(d)
            closes.append(float(c))
    return dates, closes, None


def day_loaded(trade_day):
    """用上证指数探一下：最新交易日的日线是否已经入库。"""
    dates, _closes, err = fetch_closes("sh.000001", trade_day, trade_day)
    return err is None and bool(dates) and dates[-1] == trade_day


def fetch_universe():
    rs = bs.query_stock_basic()
    rows = []
    while rs.error_code == "0" and rs.next():
        code, name, _ipo, _out, typ, status = rs.get_row_data()
        if status == "1" and typ in KEEP_TYPES:
            rows.append((code, name, KEEP_TYPES[typ]))
    return rows


def main():
    started = dt.datetime.now(dt.timezone.utc)
    login_with_retry()
    try:
        trade_day = latest_trade_day(dt.date.today())
        if os.environ.get("FORCE") != "1" and already_done(OUT_DIR, trade_day):
            print(f"cn data for {trade_day} already up to date, skip")
            return
        if not day_loaded(trade_day):
            print(f"{trade_day} daily bars not loaded in baostock yet, skip; the next scheduled run retries")
            return
        start = (dt.date.fromisoformat(trade_day) - dt.timedelta(days=LOOKBACK_CALENDAR_DAYS)).isoformat()
        universe = fetch_universe()
        print(f"trade day {trade_day}, {len(universe)} instruments", flush=True)
        rows, history, failures = [], {}, []
        for i, (code, name, kind) in enumerate(universe, 1):
            dates, closes, err = fetch_closes(code, start, trade_day)
            if err or not closes:
                failures.append({"code": code, "error": err or "empty"})
            else:
                shown = to_display_code(code)
                rows.append(summarize(shown, name, kind, dates, closes))
                history[shown] = weekly_closes(dates, closes)
            if i % 500 == 0:
                print(f"{i}/{len(universe)}", flush=True)
    finally:
        bs.logout()

    has_bench = add_relative(rows, BENCH)
    write_outputs(OUT_DIR, rows, {
        "market": "cn",
        "source": "baostock，前复权日线",
        "benchmark": BENCH if has_bench else None,
        "run_started_utc": started.isoformat(timespec="seconds"),
        "run_finished_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "trade_day": trade_day,
        "instruments": len(universe),
        "ok": len(rows),
        "failed": len(failures),
        "failed_sample": failures[:20],
    }, history)
    # 2026-11-21 起数据搬到 data/cn/；旧位置的文件在第一次新版运行时删除。
    for path in LEGACY:
        if os.path.exists(path):
            os.remove(path)
    if not rows or len(rows) < 0.8 * len(universe):
        raise SystemExit("too many failures")


if __name__ == "__main__":
    main()
