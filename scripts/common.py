"""两个市场共用的计算和输出。

每只证券一行，列的含义见 README。所有数字都由这里的代码从复权收盘价算出，
账本只引用，不手算。

方向（白话）：
  上涨：50 日均线在 200 日均线之上，收盘价在 50 日均线之上
  上涨中回调：50 日均线在 200 日均线之上，收盘价在 50 日均线之下
  下跌：50 日均线在 200 日均线之下，收盘价在 50 日均线之下
  下跌中反弹：50 日均线在 200 日均线之下，收盘价在 50 日均线之上
  数据不足：可用交易日少于 200 天
涨跌幅按交易日算：1 周 = 5 个、4 周 = 20 个、13 周 = 60 个交易日。
相对大盘 = 同期涨跌幅 − 基准（美股 SPY，A 股沪深 300）的同期涨跌幅，单位是百分点。
"""

import csv
import json
import os

FIELDS = ["code", "name", "kind", "date", "close", "ma50", "ma200", "trend",
          "chg_1w", "chg_4w", "chg_13w", "rel_1w", "rel_4w", "rel_13w",
          "high_250", "pct_from_high"]
WINDOWS = {"1w": 5, "4w": 20, "13w": 60}


def direction(close, ma50, ma200):
    if ma50 is None or ma200 is None:
        return "数据不足"
    if ma50 > ma200:
        return "上涨" if close >= ma50 else "上涨中回调"
    return "下跌" if close <= ma50 else "下跌中反弹"


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
            and meta.get("ok", 0) >= 0.8 * max(meta.get("instruments", 1), 1))


def write_outputs(out_dir, rows, meta):
    os.makedirs(out_dir, exist_ok=True)
    rows = sorted(rows, key=lambda r: r["code"])
    with open(os.path.join(out_dir, "ma_latest.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    meta = dict(meta, columns=FIELDS)
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: meta.get(k) for k in ("market", "trade_day", "instruments", "ok", "failed")},
                     ensure_ascii=False))
