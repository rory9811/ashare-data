"""美股：每周更新全部在美上市股票（含 ADR）和 ETF，以及主要指数、波动率、美债收益率、
商品期货和美元指数的均线、方向、涨跌幅和相对 SPY 强弱。

数据源：Yahoo Finance（经 yfinance），复权日线。
证券清单：Nasdaq Trader 公布的 nasdaqlisted.txt、otherlisted.txt（全部在美上市证券），
去掉测试代码、权证、单位、配股权、优先股和债券。
输出：data/us/ma_latest.csv、data/us/history_w.csv、data/us/meta.json（列见 common.py 和 README）。
代码用 Yahoo 写法：BRK-B、^GSPC、BZ=F。

每周排了两次（北京时间周六早上、上午补跑）：
  - 定时运行时，SPY 最新一根日线不比现有数据新，就跳过；
  - 手动运行或改了代码触发的运行（环境变量 FORCE=1）总是重新计算；
  - 美股盘中运行时，丢掉当天还没收盘的那根日线。
"""

import datetime as dt
import os
import re
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from common import add_relative, already_done, summarize, weekly_closes, write_outputs

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT_DIR = os.path.join(ROOT, "data", "us")
BENCH = "SPY"
SYMBOL_FILES = [
    ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", "Symbol"),
    ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", "ACT Symbol"),
]
EXCLUDE = re.compile(r"\b(warrants?|units?|rights?|preferred|notes? due|debentures?|subordinated|when issued)\b",
                     re.I)
NAME_SUFFIX = re.compile(r"\s+-\s+(Common Stock|Common Shares|Ordinary Shares)$", re.I)
EXTRAS = [
    ("^GSPC", "标普500指数", "指数"), ("^NDX", "纳斯达克100指数", "指数"),
    ("^IXIC", "纳斯达克综合指数", "指数"), ("^DJI", "道琼斯工业指数", "指数"),
    ("^RUT", "罗素2000指数", "指数"), ("^SOX", "费城半导体指数", "指数"),
    ("^VIX", "VIX波动率指数", "指数"), ("^TNX", "10年期美债收益率（数值为收益率×10）", "利率"),
    ("BZ=F", "布伦特原油期货", "商品"), ("CL=F", "WTI原油期货", "商品"),
    ("NG=F", "美国天然气期货", "商品"), ("GC=F", "黄金期货", "商品"),
    ("SI=F", "白银期货", "商品"), ("HG=F", "铜期货", "商品"),
    ("DX-Y.NYB", "美元指数", "汇率"),
]
CHUNK = 100
NY = ZoneInfo("America/New_York")


def parse_symbol_file(text, sym_col):
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("File Creation Time")]
    header = lines[0].split("|")
    idx = {h: i for i, h in enumerate(header)}
    out = []
    for line in lines[1:]:
        f = line.split("|")
        if len(f) != len(header) or f[idx["Test Issue"]] != "N":
            continue
        sym, name = f[idx[sym_col]].strip(), f[idx["Security Name"]].strip()
        if not sym or any(c in sym for c in "$^ ") or EXCLUDE.search(name):
            continue
        kind = "ETF" if f[idx["ETF"]] == "Y" else "股票"
        out.append((sym.replace(".", "-"), NAME_SUFFIX.sub("", name), kind))
    return out


def fetch_universe():
    seen, rows = set(), []
    for url, col in SYMBOL_FILES:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8", "replace")
        for sym, name, kind in parse_symbol_file(text, col):
            if sym not in seen:
                seen.add(sym)
                rows.append((sym, name, kind))
    for sym, name, kind in EXTRAS:
        if sym not in seen:
            seen.add(sym)
            rows.append((sym, name, kind))
    return rows


def _series(df, ticker):
    if isinstance(df.columns, pd.MultiIndex):
        if ticker not in df.columns.get_level_values(0):
            return None
        s = df[ticker]["Close"]
    else:
        s = df["Close"]
    s = s.dropna()
    return s if len(s) else None


def fetch(tickers):
    """返回 {代码: (日期列表, 收盘价列表)}，日期升序；整块失败时重试两次。"""
    for attempt in range(3):
        try:
            df = yf.download(tickers, period="2y", interval="1d", auto_adjust=True,
                             group_by="ticker", threads=True, progress=False)
        except Exception as e:  # 网络或限流
            print(f"download error ({e!r}), retry {attempt + 1}", file=sys.stderr, flush=True)
            time.sleep(30 * (attempt + 1))
            continue
        out = {}
        for t in tickers:
            s = _series(df, t)
            if s is not None:
                out[t] = ([d.strftime("%Y-%m-%d") for d in s.index], [float(x) for x in s.values])
        if out or attempt == 2:
            return out
        time.sleep(30 * (attempt + 1))
    return {}


def drop_unfinished_bar(dates, closes, now_ny):
    """美股盘中运行时，当天那根日线还没收盘，丢掉。"""
    if dates and dates[-1] == now_ny.date().isoformat() and now_ny.time() < dt.time(16, 30):
        return dates[:-1], closes[:-1]
    return dates, closes


def main():
    started = dt.datetime.now(dt.timezone.utc)
    now_ny = started.astimezone(NY)
    bench = fetch([BENCH]).get(BENCH)
    if not bench:
        raise SystemExit("cannot download SPY")
    trade_day = drop_unfinished_bar(*bench, now_ny)[0][-1]
    if os.environ.get("FORCE") != "1" and already_done(OUT_DIR, trade_day):
        print(f"us data for {trade_day} already up to date, skip")
        return

    universe = fetch_universe()
    print(f"trade day {trade_day}, {len(universe)} instruments", flush=True)
    rows, history, failures = [], {}, []
    for i in range(0, len(universe), CHUNK):
        part = universe[i:i + CHUNK]
        got = fetch([sym for sym, _, _ in part])
        for sym, name, kind in part:
            if sym not in got:
                failures.append({"code": sym, "error": "empty"})
                continue
            dates, closes = drop_unfinished_bar(*got[sym], now_ny)
            if not closes:
                failures.append({"code": sym, "error": "no finished bar"})
                continue
            rows.append(summarize(sym, name, kind, dates, closes))
            history[sym] = weekly_closes(dates, closes)
        print(f"{min(i + CHUNK, len(universe))}/{len(universe)}, ok {len(rows)}", flush=True)
        time.sleep(1)

    has_bench = add_relative(rows, BENCH)
    write_outputs(OUT_DIR, rows, {
        "market": "us",
        "source": "Yahoo Finance（yfinance），复权日线",
        "benchmark": BENCH if has_bench else None,
        "run_started_utc": started.isoformat(timespec="seconds"),
        "run_finished_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "trade_day": trade_day,
        "instruments": len(universe),
        "ok": len(rows),
        "failed": len(failures),
        "failed_sample": failures[:20],
    }, history)
    if not rows or len(rows) < 0.8 * len(universe):
        raise SystemExit("too many failures")


if __name__ == "__main__":
    main()
