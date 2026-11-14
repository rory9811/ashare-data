"""每周更新 A 股日线均线表。

数据源：baostock（免费、无需注册），前复权日线。
输出：
  data/ma_latest.csv  每只股票、ETF、指数一行：最新收盘价、50 日均线、200 日均线、趋势状态
  data/meta.json      本次运行的时间、交易日、成功和失败数量

趋势状态的判定与《A股主题账本》规则一致：
  完好：收盘价在 200 日均线之上，且 50 日均线在 200 日均线之上
  破位：收盘价在 200 日均线之下，且 50 日均线在 200 日均线之下
  震荡：其余情况
  数据不足：可用交易日少于 200 天
"""

import csv
import datetime as dt
import json
import os
import sys
import time

import baostock as bs

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
KEEP_TYPES = {"1": "股票", "2": "指数", "5": "ETF"}
LOOKBACK_CALENDAR_DAYS = 420  # 约 280 个交易日，足够算 200 日均线和 250 日高点


def to_display_code(bs_code: str) -> str:
    """sh.600519 -> 600519.SH"""
    market, num = bs_code.split(".")
    return f"{num}.{market.upper()}"


def classify(close, ma50, ma200):
    if ma200 is None or ma50 is None:
        return "数据不足"
    if close > ma200 and ma50 > ma200:
        return "完好"
    if close < ma200 and ma50 < ma200:
        return "破位"
    return "震荡"


def summarize(closes):
    """closes: 按日期升序的收盘价列表。返回 (收盘价, 50日均线, 200日均线, 250日最高收盘, 距高点%)"""
    close = closes[-1]
    ma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
    ma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None
    high = max(closes[-250:])
    drawdown = (close / high - 1) * 100 if high else None
    return close, ma50, ma200, high, drawdown


def login_with_retry(tries=3):
    for i in range(tries):
        lg = bs.login()
        if lg.error_code == "0":
            return
        print(f"login failed ({lg.error_code} {lg.error_msg}), retry {i + 1}", file=sys.stderr)
        time.sleep(10)
    raise SystemExit("baostock login failed")


def latest_trade_day(today: dt.date) -> str:
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


def fetch_universe():
    rs = bs.query_stock_basic()
    rows = []
    while rs.error_code == "0" and rs.next():
        code, name, _ipo, _out, typ, status = rs.get_row_data()
        if status == "1" and typ in KEEP_TYPES:
            rows.append((code, name, KEEP_TYPES[typ]))
    return rows


def fetch_closes(code, start, end):
    rs = bs.query_history_k_data_plus(code, "date,close", start_date=start, end_date=end,
                                      frequency="d", adjustflag="2")
    if rs.error_code != "0":
        return None, None, rs.error_msg
    closes, last_date = [], None
    while rs.next():
        d, c = rs.get_row_data()
        if c:
            closes.append(float(c))
            last_date = d
    return closes, last_date, None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    started = dt.datetime.now(dt.timezone.utc)
    login_with_retry()
    try:
        trade_day = latest_trade_day(dt.date.today())
        start = (dt.date.fromisoformat(trade_day) - dt.timedelta(days=LOOKBACK_CALENDAR_DAYS)).isoformat()
        universe = fetch_universe()
        print(f"trade day {trade_day}, {len(universe)} instruments", flush=True)

        out_rows, failures = [], []
        for i, (code, name, kind) in enumerate(universe, 1):
            closes, last_date, err = fetch_closes(code, start, trade_day)
            if err or not closes:
                failures.append({"code": code, "error": err or "empty"})
                continue
            close, ma50, ma200, high, dd = summarize(closes)
            out_rows.append({
                "code": to_display_code(code),
                "name": name,
                "kind": kind,
                "date": last_date,
                "close": round(close, 3),
                "ma50": round(ma50, 3) if ma50 is not None else "",
                "ma200": round(ma200, 3) if ma200 is not None else "",
                "status": classify(close, ma50, ma200),
                "high_250": round(high, 3),
                "pct_from_high": round(dd, 2) if dd is not None else "",
            })
            if i % 500 == 0:
                print(f"{i}/{len(universe)}", flush=True)
    finally:
        bs.logout()

    out_rows.sort(key=lambda r: r["code"])
    fields = ["code", "name", "kind", "date", "close", "ma50", "ma200", "status", "high_250", "pct_from_high"]
    with open(os.path.join(OUT_DIR, "ma_latest.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)

    meta = {
        "source": "baostock，前复权日线",
        "run_started_utc": started.isoformat(timespec="seconds"),
        "run_finished_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "trade_day": trade_day,
        "instruments": len(universe),
        "ok": len(out_rows),
        "failed": len(failures),
        "failed_sample": failures[:20],
    }
    with open(os.path.join(OUT_DIR, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: meta[k] for k in ("trade_day", "instruments", "ok", "failed")}, ensure_ascii=False))

    if not out_rows or len(out_rows) < 0.8 * len(universe):
        raise SystemExit("too many failures")


if __name__ == "__main__":
    main()
