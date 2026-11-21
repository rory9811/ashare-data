"""两个市场共用的计算和输出。

每只证券一行，列的含义见 README。所有数字都由这里的代码从复权收盘价算出，
账本只引用，不手算。

方向（白话，六种加数据不足）：
  50 日均线在 200 日均线之上时——
    上涨：收盘价在 50 日均线之上
    上涨中回调：收盘价在 50 日和 200 日均线之间
    转跌：收盘价已跌破 200 日均线
  50 日均线在 200 日均线之下时——
    下跌：收盘价在 50 日均线之下
    下跌中反弹：收盘价在 50 日和 200 日均线之间
    转涨：收盘价已站上 200 日均线
  数据不足：可用交易日少于 200 天
前三种收盘价在 200 日均线之上（上涨、上涨中回调、转涨），归"上涨一边"；
另外三种在 200 日均线之下，归"下跌一边"。
涨跌幅按交易日算：1 周 = 5 个、4 周 = 20 个、13 周 = 60 个交易日。
相对大盘 = 同期涨跌幅 − 基准（美股 SPY，A 股沪深 300）的同期涨跌幅，单位是百分点。

另出一份两年周线（history_w.csv），供画走势图：每行一个代码，每列一周，
值是那一周最后一个交易日的复权收盘价，列名写那一周的周五日期。
"""

import csv
import datetime as dt
import json
import os

FIELDS = ["code", "name", "kind", "date", "close", "ma50", "ma200", "trend",
          "chg_1w", "chg_4w", "chg_13w", "rel_1w", "rel_4w", "rel_13w",
          "high_250", "pct_from_high"]
WINDOWS = {"1w": 5, "4w": 20, "13w": 60}
HISTORY_WEEKS = 104


def direction(close, ma50, ma200):
    if ma50 is None or ma200 is None:
        return "数据不足"
    if ma50 > ma200:
        if close >= ma50:
            return "上涨"
        return "上涨中回调" if close >= ma200 else "转跌"
    if close <= ma50:
        return "下跌"
    return "下跌中反弹" if close <= ma200 else "转涨"


def change(closes, n):
    """最近 n 个交易日的涨跌幅（%）；数据不够返回 None。"""
    if len(closes) <= n or not closes[-1 - n]:
        return None
    return (closes[-1] / closes[-1 - n] - 1) * 100


def _r(x, nd=2):
    return "" if x is None else round(x, nd)


def summarize(code, name, kind, dates, closes):
    """dates、closes 按日期升序。返回一行（dict），相对大盘的列先留空。"""
    close = closes[-1]
    ma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
    ma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None
    high = max(closes[-250:])
    row = {
        "code": code, "name": name, "kind": kind, "date": dates[-1],
        "close": _r(close, 4), "ma50": _r(ma50, 4), "ma200": _r(ma200, 4),
        "trend": direction(close, ma50, ma200),
        "high_250": _r(high, 4),
        "pct_from_high": _r((close / high - 1) * 100 if high else None),
    }
    for key, n in WINDOWS.items():
        row[f"chg_{key}"] = _r(change(closes, n))
        row[f"rel_{key}"] = ""
    return row


def week_end(day):
    """某个日期所在那一周的周五（日期字符串进，日期字符串出）。"""
    d = dt.date.fromisoformat(day)
    return (d + dt.timedelta(days=4 - d.weekday())).isoformat()


def weekly_closes(dates, closes):
    """日线压成周线：{周五日期: 那一周最后一个交易日的收盘价}。"""
    out = {}
    for d, c in zip(dates, closes):
        out[week_end(d)] = c
    return out


def add_relative(rows, bench_code):
    """用基准那一行的涨跌幅，给每一行算相对大盘。基准缺失时相对列留空。"""
    bench = next((r for r in rows if r["code"] == bench_code), None)
    if bench is None:
        return False
    for r in rows:
        for key in WINDOWS:
            a, b = r[f"chg_{key}"], bench[f"chg_{key}"]
            r[f"rel_{key}"] = "" if a == "" or b == "" else round(a - b, 2)
    return True


def read_meta(out_dir):
    try:
        with open(os.path.join(out_dir, "meta.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def already_done(out_dir, trade_day):
    """数据已经覆盖这个交易日、成功率也达标，就不必重算。"""
    meta = read_meta(out_dir)
    return (meta.get("trade_day") == trade_day
            and meta.get("columns") == FIELDS
            and meta.get("history_weeks") == HISTORY_WEEKS
            and meta.get("ok", 0) >= 0.8 * max(meta.get("instruments", 1), 1))


def _write_history(path, history, trade_day):
    last = dt.date.fromisoformat(week_end(trade_day))
    weeks = [(last - dt.timedelta(weeks=k)).isoformat() for k in range(HISTORY_WEEKS - 1, -1, -1)]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["code"] + weeks)
        for code in sorted(history):
            series = history[code]
            values = [series.get(wk) for wk in weeks]
            if any(v is not None for v in values):
                w.writerow([code] + ["" if v is None else f"{v:.6g}" for v in values])


def write_outputs(out_dir, rows, meta, history):
    """rows：每只一行的汇总；history：{代码: weekly_closes(...) 的结果}。"""
    os.makedirs(out_dir, exist_ok=True)
    rows = sorted(rows, key=lambda r: r["code"])
    with open(os.path.join(out_dir, "ma_latest.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    _write_history(os.path.join(out_dir, "history_w.csv"), history, meta["trade_day"])
    meta = dict(meta, columns=FIELDS, history_weeks=HISTORY_WEEKS)
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: meta.get(k) for k in ("market", "trade_day", "instruments", "ok", "failed")},
                     ensure_ascii=False))
