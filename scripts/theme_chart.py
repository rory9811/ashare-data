"""画一个主题的走势图，并算出价格结构：几只代表股和基准，两年周线。

输入：本仓库数据分支里的 ohlcv/<代码>.csv（每周一行：开、高、低、收、成交额），
本地 --ohlcv 文件夹里没有的会自动用 curl 下载。
输出三样东西：
  <out>.png         给模型看。上面每只代表股一格：周 K 线（对数刻度）、10 周和 40 周均线
                    （约等于 50 日和 200 日均线，和方向标签一致）、程序找出的波段高低点
                    （小三角）、当前或刚被突破的横盘区间（浅色方框），下面是每周成交额和
                    它的 10 周均值。最下面两格：左边各代码和基准的走势（起点 = 100），
                    右边相对基准的强弱（代码 ÷ 基准，起点 = 100）。
  <out>.widget.txt  账本文档里图表组件的完整代码（走势对比，数据已内嵌，隔周取一个点）。
  标准输出          数字摘要（区间涨跌、高低点、相对基准）、价格结构（见 structure 函数）、
                    趋势体质（见 path_stats、heat）。
图只用来看形状；写进账本的数字一律取自标准输出或 ma_latest.csv，不从图上读。
每一项结构读数对应哪种理论、怎么解读，见《股票价格理论手册》。

用法：
  python3 -I theme_chart.py --market us --codes MU,WDC,STX --out storage
只依赖标准库、matplotlib 和 curl。
"""

import argparse
import csv
import json
import math
import os
import re
import statistics
import subprocess
import sys

BENCH = {"us": "SPY", "cn": "000300.SH"}
DATA_URL = "https://raw.githubusercontent.com/rory9811/ashare-data/data-{m}/{m}/ohlcv/{f}.csv"
# 上涨、下跌 K 线的颜色，按各市场习惯：美股绿涨红跌，A 股红涨绿跌
CANDLE = {"us": ("#26a69a", "#ef5350"), "cn": ("#e5484d", "#26a69a")}
MIN_WEEKS = 26
VOL_BASE = 10  # 成交额倍数 = 本周成交额 ÷ 之前 10 周平均（约等于常用的 50 日均量）


# ---------- 读数据 ----------

def safe_name(code):
    """代码转文件名，和 common.py 一致：^GSPC → _GSPC，BZ=F → BZ_F。"""
    return re.sub(r"[^A-Za-z0-9.\-]", "_", code)


def ensure_file(folder, market, code):
    path = os.path.join(folder, safe_name(code) + ".csv")
    if not os.path.exists(path):
        os.makedirs(folder, exist_ok=True)
        url = DATA_URL.format(m=market, f=safe_name(code))
        r = subprocess.run(["curl", "-fsSL", "--retry", "2", "-o", path, url], capture_output=True, text=True)
        if r.returncode != 0:
            if os.path.exists(path):
                os.remove(path)
            return None
    return path


def load_bars(path):
    bars = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("close"):
                continue
            c = float(row["close"])
            num = lambda k, d: float(row[k]) if row.get(k) else d
            bars.append({"w": row["week"], "o": num("open", c), "h": num("high", c),
                         "l": num("low", c), "c": c, "a": num("amount", 0.0)})
    return bars


def common_start(series):
    """所有代码都有数据的第一周（下标）。"""
    firsts = [next((k for k, v in enumerate(vals) if v is not None), None) for vals in series.values()]
    if any(f is None for f in firsts):
        return None
    return max(firsts)


def rebase(vals, start):
    base = vals[start]
    out, last = [], None
    for v in vals[start:]:
        # 停牌等缺口沿用上一周的值，避免折线断开时误读成跳涨跳跌
        last = v if v is not None else last
        out.append(None if last is None else last / base * 100)
    return out


# ---------- 价格结构 ----------

def fmt(v):
    return f"{v:,.0f}" if abs(v) >= 10000 else f"{v:.4g}"


def pc(x, digits=0):
    return f"{x * 100:+.{digits}f}%"


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def weekly_sd(closes):
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    return statistics.stdev(rets) if len(rets) >= 26 else None


def swing_threshold(sd):
    """波段阈值（对数）：从一个高点回落、或从一个低点反弹超过它，才算一个新的波段。
    取周波动率的 3 倍，限制在 6% 到 35% 之间：大盘约 6%~7%，高波动的股票 20% 以上。
    这样找出来的是道氏理论说的“中期波动”（次级折返），不是每周的小起伏。"""
    return 0.15 if sd is None else min(max(3 * sd, 0.06), 0.35)


def zigzag(highs, lows, T):
    """用周最高、最低价找波段高低点。返回 (确认的高低点 [(下标, 'H'/'L')], 当前方向, 当前这段的极值下标)。
    最后一个极值还没有被反向超过阈值的走势确认，所以单独返回。"""
    piv, trend, ext = [], None, 0
    hi_i = lo_i = 0
    for i in range(1, len(highs)):
        if trend is None:
            if highs[i] > highs[hi_i]:
                hi_i = i
            if lows[i] < lows[lo_i]:
                lo_i = i
            if math.log(highs[hi_i] / lows[lo_i]) >= T:
                if hi_i > lo_i:
                    piv.append((lo_i, "L"))
                    trend, ext = "up", hi_i
                else:
                    piv.append((hi_i, "H"))
                    trend, ext = "down", lo_i
        elif trend == "up":
            if highs[i] >= highs[ext]:
                ext = i
            elif math.log(highs[ext] / lows[i]) >= T:
                piv.append((ext, "H"))
                trend, ext = "down", i
        else:
            if lows[i] <= lows[ext]:
                ext = i
            elif math.log(highs[i] / lows[ext]) >= T:
                piv.append((ext, "L"))
                trend, ext = "up", i
    return piv, trend, ext


def find_box(closes, end, T, min_len=6, max_len=52):
    """以第 end 周为终点、最长的横盘区间（用收盘价定上下沿）。条件：
      - 上下沿相距不超过 2 倍波段阈值；
      - 效率比（首尾净变化 ÷ 每周变化绝对值之和，Kaufman）不超过 0.3，即来回多、净进展少；
      - 收盘价至少两次穿过区间中线：买卖双方轮流占上风（拍卖理论的“平衡”）。
    返回 (起点, 终点, 下沿, 上沿) 或 None。"""
    best = None
    for n in range(min_len, max_len + 1):
        s = end - n + 1
        if s < 1:
            break
        seg = closes[s:end + 1]
        hi, lo = max(seg), min(seg)
        if math.log(hi / lo) > 2 * T:
            break
        path = sum(abs(math.log(closes[k] / closes[k - 1])) for k in range(s + 1, end + 1))
        er = abs(math.log(closes[end] / closes[s])) / path if path else 0
        mid = math.sqrt(hi * lo)
        side = [c > mid for c in seg]
        crosses = sum(a != b for a, b in zip(side, side[1:]))
        if er <= 0.3 and crosses >= 2:
            best = (s, end, lo, hi)
    return best


def vol_ratio(a, k):
    prev = [x for x in a[max(0, k - VOL_BASE):k] if x > 0]
    return a[k] / (sum(prev) / len(prev)) if prev and a[k] > 0 else None


def close_loc(b):
    """收盘在本周最高最低之间的位置：0 = 收在最低，100 = 收在最高。"""
    return round((b["c"] - b["l"]) / (b["h"] - b["l"]) * 100) if b["h"] > b["l"] else 50


def ma(closes, n, end=None):
    end = len(closes) - 1 if end is None else end
    return sum(closes[end - n + 1:end + 1]) / n if end + 1 >= n else None


def compare(a, b, T):
    d = math.log(b / a)
    return "抬高" if d > T / 4 else "降低" if d < -T / 4 else "持平"


STRUCT_NAME = {("抬高", "抬高"): "上升结构", ("降低", "降低"): "下降结构",
               ("抬高", "降低"): "扩张（振幅变大）", ("降低", "抬高"): "收敛（振幅变小）",
               ("持平", "持平"): "横向"}
RANGE_EN = {"向上突破": "breakout up", "向下跌破": "breakdown", "区间内": "in range",
            "突破后守住": "breakout held", "跌破后未收回": "breakdown held", "突破失败": "failed breakout",
            "跌破失败": "failed breakdown", "反向穿越": "range reversed", "单边运行": "trending"}
STRUCT_EN = {"上升结构": "HH+HL", "下降结构": "LH+LL", "扩张（振幅变大）": "expanding",
             "收敛（振幅变小）": "contracting", "横向": "flat"}


def dow_structure(bars, T):
    highs, lows, closes = [b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars]
    piv, trend, ext = zigzag(highs, lows, T)
    val = lambda p: highs[p[0]] if p[1] == "H" else lows[p[0]]
    out = {"摆动点": [[bars[i]["w"], "高" if t == "H" else "低", fmt(val((i, t)))] for i, t in piv[-6:]]}
    H = [p for p in piv if p[1] == "H"]
    L = [p for p in piv if p[1] == "L"]
    if len(H) >= 2 and len(L) >= 2:
        hc, lc = compare(val(H[-2]), val(H[-1]), T), compare(val(L[-2]), val(L[-1]), T)
        name = STRUCT_NAME.get((hc, lc), f"高点{hc}、低点{lc}")
        out["结构"] = (f"{name}：最近两个高点 {fmt(val(H[-2]))}（{bars[H[-2][0]]['w']}）→ {fmt(val(H[-1]))}"
                      f"（{bars[H[-1][0]]['w']}），{hc}；最近两个低点 {fmt(val(L[-2]))}（{bars[L[-2][0]]['w']}）→ "
                      f"{fmt(val(L[-1]))}（{bars[L[-1][0]]['w']}），{lc}")
        word = {"抬高": "higher", "降低": "lower", "持平": "flat"}
        out["_en"] = STRUCT_EN.get(name, f"highs {word[hc]} / lows {word[lc]}")
    else:
        out["结构"] = "两年内确认的波段不足两高两低，结构不明"
        out["_en"] = "unclear"
    c = closes[-1]
    if trend == "up":
        start = piv[-1][0]
        s = (f"自 {bars[start]['w']} 的低点 {fmt(lows[start])} 以来上涨 {pc(c / lows[start] - 1)}，"
             f"这段最高 {fmt(highs[ext])}（{bars[ext]['w']}），现价距它 {pc(c / highs[ext] - 1)}")
        if H:
            last_h = val(H[-1])
            s += (f"；已越过上一个确认高点 {fmt(last_h)}" if c > last_h else f"；还在上一个确认高点 {fmt(last_h)} 之下")
    elif trend == "down":
        start = piv[-1][0]
        s = (f"自 {bars[start]['w']} 的高点 {fmt(highs[start])} 以来下跌 {pc(c / highs[start] - 1)}，"
             f"这段最低 {fmt(lows[ext])}（{bars[ext]['w']}），现价距它 {pc(c / lows[ext] - 1)}")
        if L:
            last_l = val(L[-1])
            s += (f"；已跌破上一个确认低点 {fmt(last_l)}" if c < last_l else f"；仍在上一个确认低点 {fmt(last_l)} 之上")
    else:
        s = "两年内没有超过阈值的波段"
    out["当前一段"] = s
    if trend in ("up", "down") and len(piv) >= 2:
        out.update(dow_signals(piv, trend, ext, bars, val, T))
    out["_piv"] = [(i, t, val((i, t))) for i, t in piv]
    return out


def dow_signals(piv, trend, ext, bars, val, T):
    """道氏理论的两个经典读法：
      回撤比例——次级折返通常回吐前一段主要走势的三分之一到三分之二，超过三分之二更可能是主要趋势转向；
      反转确认——上升中先出现更低的高点、再跌破上一个低点，是下降趋势的确认信号（下降中反之）。
    另给出最近两段同向走势的幅度、周数和周均成交额，用来看力度是否减弱（缠论的背驰、威科夫的努力与结果）。"""
    closes, amts = [b["c"] for b in bars], [b["a"] for b in bars]
    c = closes[-1]
    out = {}
    last, prev = piv[-1], piv[-2]
    hi, lo = (val(last), val(prev)) if trend == "down" else (val(prev), val(last))
    if hi > lo:
        r = (hi - c) / (hi - lo) if trend == "down" else (c - lo) / (hi - lo)
        what = "回吐了上一段上涨" if trend == "down" else "收复了上一段下跌"
        out["中期回撤"] = f"这一段{what}（{fmt(lo)}→{fmt(hi)}）的 {round(r * 100)}%"
    # 主要趋势：两年里的最低点到其后的最高点（或最高点到其后的最低点）
    highs, lows = [b["h"] for b in bars], [b["l"] for b in bars]
    lo_i, hi_i = min(range(len(lows)), key=lows.__getitem__), max(range(len(highs)), key=highs.__getitem__)
    hi, lo = highs[hi_i], lows[lo_i]
    if hi > lo:
        up = hi_i > lo_i
        r = (hi - c) / (hi - lo) if up else (c - lo) / (hi - lo)
        head = (f"两年里从最低 {fmt(lo)}（{bars[lo_i]['w']}）到最高 {fmt(hi)}（{bars[hi_i]['w']}）的整段上涨，已回吐 {round(r * 100)}%"
                if up else
                f"两年里从最高 {fmt(hi)}（{bars[hi_i]['w']}）到最低 {fmt(lo)}（{bars[lo_i]['w']}）的整段下跌，已收复 {round(r * 100)}%")
        note = ("：不到三分之一，属于浅幅折返" if r < 1 / 3 else
                "：在三分之一到三分之二之间，道氏理论里正常的次级折返" if r <= 2 / 3 else
                "：超过三分之二，按道氏理论更可能是主要趋势转向，而不只是折返")
        out["主要趋势回撤"] = head + note
    H = [p for p in piv if p[1] == "H"]
    L = [p for p in piv if p[1] == "L"]
    if trend == "down" and len(H) >= 2 and L:
        lower_high = math.log(val(H[-1]) / val(H[-2])) < -T / 4
        if c < val(L[-1]):
            out["道氏信号"] = ("先出现更低的高点，又跌破上一个低点：下降趋势的确认信号" if lower_high else
                           "跌破了上一个低点，但前一个高点仍在抬高：上升结构受损，要看下一次反弹能否越过前高")
    if trend == "up" and len(L) >= 2 and H:
        higher_low = math.log(val(L[-1]) / val(L[-2])) > T / 4
        if c > val(H[-1]):
            out["道氏信号"] = ("先出现更高的低点，又越过上一个高点：上升趋势的确认信号" if higher_low else
                           "越过了上一个高点，但前一个低点仍在降低：下降结构受损，要看下一次回调能否守住前低")
    # 波段（含进行中的一段）
    points = [(i, val((i, t))) for i, t in piv] + [(ext, bars[ext]["h"] if trend == "up" else bars[ext]["l"])]
    legs = []
    for (i0, v0), (i1, v1) in zip(points, points[1:]):
        avg = mean([a for a in amts[i0 + 1:i1 + 1] if a > 0])
        legs.append({"up": v1 > v0, "chg": v1 / v0 - 1, "weeks": i1 - i0, "amt": avg, "end": bars[i1]["w"],
                     "live": i1 == ext})
    for up, label in ((True, "最近两段上涨"), (False, "最近两段下跌")):
        same = [g for g in legs if g["up"] == up][-2:]
        if same:
            out[label] = [f"{'进行中，' if g['live'] else ''}至 {g['end']}：{pc(g['chg'])}，{g['weeks']} 周"
                          + (f"，周均成交额 {money(g['amt'])}" if g["amt"] else "") for g in same]
    return out


def range_state(bars, T, has_vol):
    closes, highs, lows, amts = ([b[k] for b in bars] for k in "chla")
    last = len(bars) - 1
    ratio_txt = lambda k: (f"，那周成交额是之前 {VOL_BASE} 周平均的 {vol_ratio(amts, k):.1f} 倍"
                           if has_vol and vol_ratio(amts, k) else "")
    box = find_box(closes, last - 1, T)
    if box:
        s, e, lo, hi = box
        n = e - s + 1
        head = f"{bars[s]['w']} 起 {n} 周的收盘区间 {fmt(lo)}–{fmt(hi)}（高度 {pc(hi / lo - 1)}）"
        c = closes[last]
        if c > hi:
            return {"状态": "向上突破", "说明": f"本周收盘 {fmt(c)} 向上突破{head}的上沿{ratio_txt(last)}",
                    "_box": (s, last, lo, hi)}
        if c < lo:
            return {"状态": "向下跌破", "说明": f"本周收盘 {fmt(c)} 跌破{head}的下沿{ratio_txt(last)}",
                    "_box": (s, last, lo, hi)}
        txt = f"处在{head}之内，收盘位于区间的 {round((c - lo) / (hi - lo) * 100)}% 处（0 = 下沿，100 = 上沿）"
        probes = []
        for k in range(max(s, last - 2), last + 1):
            if highs[k] > hi * 1.005 and closes[k] <= hi:
                probes.append(f"{bars[k]['w']} 盘中冲过上沿到 {fmt(highs[k])} 但收回区间内{ratio_txt(k)}")
            if lows[k] < lo * 0.995 and closes[k] >= lo:
                probes.append(f"{bars[k]['w']} 盘中跌破下沿到 {fmt(lows[k])} 但收回区间内{ratio_txt(k)}")
        if probes:
            txt += "；近三周的试探：" + "；".join(probes)
        return {"状态": "区间内", "说明": txt, "_box": (s, last, lo, hi)}
    for k in range(1, 9):
        b = last - k
        box = find_box(closes, b - 1, T)
        if not box or lo_hi_inside(closes[b], box):
            continue
        s, e, lo, hi = box
        n = e - s + 1
        up = closes[b] > hi
        edge = hi if up else lo
        after = closes[b:]
        head = (f"{k} 周前（{bars[b]['w']}）{'向上突破' if up else '向下跌破'}了 {bars[s]['w']} 起 {n} 周的收盘区间"
                f" {fmt(lo)}–{fmt(hi)}{ratio_txt(b)}")
        held = all(x > hi for x in after) if up else all(x < lo for x in after)
        c = closes[last]
        if held:
            touch = min(lows[b + 1:]) if up else max(highs[b + 1:])
            near = (touch <= edge * (1 + T / 4)) if up else (touch >= edge * (1 - T / 4))
            tail = ("，此后收盘一直在区间之外" +
                    (f"，期间回踩到 {fmt(touch)}，接近原区间边沿后守住" if k > 1 and near else ""))
            state = "突破后守住" if up else "跌破后未收回"
        elif lo <= c <= hi:
            tail, state = "，此后收盘回到区间内：突破失败", "突破失败" if up else "跌破失败"
        else:
            tail = "，此后又反向穿过了整个区间" if (up and c < lo) or (not up and c > hi) else "，此后一度回到区间内"
            state = "反向穿越" if (up and c < lo) or (not up and c > hi) else ("突破后守住" if up else "跌破后未收回")
        return {"状态": state, "说明": head + tail, "_box": (s, last, lo, hi), "_edge": edge}
    er = efficiency(closes, last, 13)
    return {"状态": "单边运行", "说明": f"近期没有横盘区间：近 13 周效率比 {er:.2f}（1 = 一路单边，0 = 原地来回）"
            if er is not None else "近期没有横盘区间"}


def lo_hi_inside(c, box):
    return box[2] <= c <= box[3]


def efficiency(closes, end, n):
    if end < n:
        return None
    path = sum(abs(math.log(closes[k] / closes[k - 1])) for k in range(end - n + 1, end + 1))
    return abs(math.log(closes[end] / closes[end - n])) / path if path else 0


def weinstein(closes, sd):
    """温斯坦（Stan Weinstein）的四阶段，用 40 周均线（≈200 日）判断，和方向标签用同一条长期均线；
    他原书用 30 周，差别不大。均线 4 周变化不超过 0.25 倍周波动率算“走平”。"""
    if len(closes) < 80:
        return {"阶段": "数据不足", "说明": "不足 80 周，无法判断均线的方向和之前的趋势", "_en": "n/a"}
    m_now, m_4 = ma(closes, 40), ma(closes, 40, len(closes) - 5)
    m_13, m_39 = ma(closes, 40, len(closes) - 14), ma(closes, 40, len(closes) - 40)
    slope, prior = m_now / m_4 - 1, m_13 / m_39 - 1
    flat = 0.25 * (sd or 0.04)
    c = closes[-1]
    pos = c / m_now - 1
    above = c > m_now
    nums = f"40 周均线 4 周变化 {pc(slope, 1)}，现价在均线{'上方' if above else '下方'} {pc(abs(pos))[1:]}"
    if abs(slope) <= flat:
        if prior > 0:
            stage, en, txt = "第三阶段特征", "Stage 3?", "上升之后均线走平，价格围着均线来回：做顶的特征"
        else:
            stage, en, txt = "第一阶段特征", "Stage 1?", "下跌之后均线走平，价格围着均线来回：筑底的特征"
    elif slope > 0 and above:
        stage, en, txt = "第二阶段", "Stage 2", "价格在上行的 40 周均线之上：上升阶段"
    elif slope < 0 and not above:
        stage, en, txt = "第四阶段", "Stage 4", "价格在下行的 40 周均线之下：下降阶段"
    elif slope > 0:
        stage, en, txt = "第二阶段受损", "Stage 2 damaged", "价格跌破仍在上行的 40 周均线：上升中的深回调，或开始转入第三阶段"
    else:
        stage, en, txt = "第四阶段反弹", "Stage 4 bounce", "价格站上仍在下行的 40 周均线：下跌中的反弹，或第一阶段向第二阶段过渡的早期"
    return {"阶段": stage, "说明": f"{txt}（{nums}）", "_en": en}


def trend_template(closes, rs52):
    """米勒维尼（Minervini）趋势模板的周线版：强势上升趋势通常同时满足的八条。
    50/150/200 日均线换成 10/30/40 周均线；“相对强度排名 ≥70”换成“近 52 周跑赢基准”。"""
    if len(closes) < 56:
        return "不足 56 周，不算"
    c = closes[-1]
    m10, m30, m40, m40_4 = ma(closes, 10), ma(closes, 30), ma(closes, 40), ma(closes, 40, len(closes) - 5)
    hi52, lo52 = max(closes[-52:]), min(closes[-52:])
    items = [
        ("收盘在 30 周和 40 周均线之上", c > m30 and c > m40),
        ("30 周均线在 40 周均线之上", m30 > m40),
        ("40 周均线比 4 周前高", m40 > m40_4),
        ("10 周均线在 30 周和 40 周均线之上", m10 > m30 and m10 > m40),
        ("收盘在 10 周均线之上", c > m10),
        ("收盘比 52 周最低收盘高 30% 以上", c >= lo52 * 1.3),
        ("收盘距 52 周最高收盘不到 25%", c >= hi52 * 0.75),
        ("近 52 周跑赢基准", rs52 is not None and rs52 > 0),
    ]
    miss = [t for t, ok in items if not ok]
    return f"满足 {len(items) - len(miss)}/8" + (f"；未满足：{'；'.join(miss)}" if miss else "")


def volume_read(bars, sd):
    closes, amts = [b["c"] for b in bars], [b["a"] for b in bars]
    last = len(bars) - 1
    out = {}
    ups = [amts[k] for k in range(last - 12, last + 1) if closes[k] > closes[k - 1] and amts[k] > 0]
    downs = [amts[k] for k in range(last - 12, last + 1) if closes[k] < closes[k - 1] and amts[k] > 0]
    if ups and downs:
        r = mean(ups) / mean(downs)
        out["近13周上涨周与下跌周的平均成交额之比"] = round(r, 2)
    ranges = [math.log(b["h"] / b["l"]) for b in bars if b["l"] > 0]
    med_range = statistics.median(ranges) if ranges else 0
    big = []
    for k in range(max(1, last - 25), last + 1):
        r = vol_ratio(amts, k)
        chg = closes[k] / closes[k - 1] - 1
        wide = math.log(bars[k]["h"] / bars[k]["l"]) >= 2 * med_range or abs(math.log(1 + chg)) >= 1.5 * (sd or 0.04)
        if r and r >= 2 and wide:
            big.append(f"{bars[k]['w']}：成交额 {r:.1f} 倍，周涨跌 {pc(chg, 1)}，收在本周 {close_loc(bars[k])}% 处")
    out["近26周放量大幅波动的周"] = big[-4:] if big else "没有"
    out["近6周逐周"] = [
        [bars[k]["w"], pc(closes[k] / closes[k - 1] - 1, 1), pc(bars[k]["h"] / bars[k]["l"] - 1, 1)[1:],
         None if vol_ratio(amts, k) is None else round(vol_ratio(amts, k), 1), close_loc(bars[k])]
        for k in range(last - 5, last + 1)
    ]
    out["近6周逐周的列"] = ["周", "涨跌", "振幅（最高÷最低）", f"成交额倍数（÷之前{VOL_BASE}周平均）", "收盘位置（0 最低，100 最高）"]
    return out


def structure(code, bars, bench_by_week, is_bench=False):
    """一个代码的价格结构。每一项都是从周线算出的事实，解读放在手册里：
      波段阈值：多大的反向走势才算新波段（见 swing_threshold）；
      道氏结构：最近两个波段高点、低点是抬高还是降低，以及当前这一段；
      区间与突破：拍卖理论的平衡/失衡、威科夫的区间、布鲁克斯的突破与失败突破；
      温斯坦阶段；趋势模板（米勒维尼）；52 周位置（George-Hwang 2004 的 52 周高点效应）；
      相对强弱线（欧奈尔看它是否先于价格创新高）；量价（威科夫的努力与结果）。"""
    if len(bars) < 30:
        return {"说明": f"只有 {len(bars)} 周数据，不算结构"}
    closes = [b["c"] for b in bars]
    sd = weekly_sd(closes)
    T = swing_threshold(sd)
    has_vol = sum(1 for b in bars[-26:] if b["a"] > 0) >= 20
    out = {"波段阈值": pc(T)[1:], "周波动率": None if sd is None else pc(sd, 1)[1:]}
    dow = dow_structure(bars, T)
    out.update({k: v for k, v in dow.items() if not k.startswith("_")})
    rng = range_state(bars, T, has_vol)
    out["区间与突破"] = {k: v for k, v in rng.items() if not k.startswith("_")}
    st = weinstein(closes, sd)
    out["温斯坦阶段"] = {k: v for k, v in st.items() if not k.startswith("_")}
    c = closes[-1]
    hi52, lo52 = max(closes[-52:]), min(closes[-52:])
    pos = f"距 52 周最高收盘 {pc(c / hi52 - 1)}，比 52 周最低收盘高 {pc(c / lo52 - 1)}"
    if c >= hi52:
        pos += "；本周收盘创 52 周新高"
    elif c <= lo52:
        pos += "；本周收盘创 52 周新低"
    out["52周位置"] = pos
    rs52 = None
    if not is_bench:
        rs = [(b["w"], b["c"] / bench_by_week[b["w"]]) for b in bars if bench_by_week.get(b["w"])]
        if len(rs) >= 53:
            vals = [v for _, v in rs]
            rs52 = vals[-1] / vals[-53] - 1
            r13 = vals[-1] / vals[-14] - 1
            top, bot = max(vals[-52:]), min(vals[-52:])
            where = ("52 周新高" if vals[-1] >= top else "52 周新低" if vals[-1] <= bot
                     else f"52 周区间的 {round((vals[-1] - bot) / (top - bot) * 100)}% 处")
            out["相对强弱线"] = f"近 13 周 {pc(r13)}，近 52 周 {pc(rs52)}；现在处于{where}"
        out["趋势模板"] = trend_template(closes, rs52)
    if has_vol:
        out["量价"] = volume_read(bars, sd)
    else:
        out["量价"] = "没有成交额数据（指数或商品的部分代码），不读量价"
    out["_draw"] = {"piv": dow["_piv"], "box": rng.get("_box"), "edge": rng.get("_edge"),
                    "en": f"{dow['_en']} | {st['_en']} | {RANGE_EN.get(rng['状态'], '')}"}
    return out

def _vol(vals):
    """一段周收盘价的年化波动率（%），数据不够返回 None。"""
    rets = [math.log(b / a) for a, b in zip(vals, vals[1:]) if a and b]
    if len(rets) < 26:
        return None
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(52) * 100


def path_stats(vals):
    """用完整的两年周线（不重设起点）算趋势的“体质”，对应研究里的几个量：
    两年涨幅、前一年和近一年各自的涨幅、加速度（两年涨幅减前一年涨幅，
    Greenwood-Shleifer-You 2019 的定义）、近一年波动率比前一年的变化、
    近一年上涨周的占比（涨得是否是一小步一小步的，对应 Da-Gurun-Warachka 2014）。"""
    if len(vals) < 104 or vals[0] is None or vals[-53] is None or vals[-1] is None:
        return None
    v2, v1, v0 = vals[0], vals[-53], vals[-1]
    first, recent, two = v1 / v2 - 1, v0 / v1 - 1, v0 / v2 - 1
    last52 = [v for v in vals[-53:] if v]
    prev52 = [v for v in vals[:52] if v]
    ups = [b > a for a, b in zip(last52, last52[1:])]
    vol_now, vol_prev = _vol(last52), _vol(prev52)
    return {
        "two": two, "first": first, "recent": recent, "accel": two - first,
        "vol_now": vol_now, "vol_change": None if vol_now is None or vol_prev is None else vol_now - vol_prev,
        "up_share": sum(ups) / len(ups) if ups else None,
    }


def heat(codes, bench, raw):
    """主题层面（各代表股等权平均）的急涨检查，加上每只代表股的同一组数。
    急涨门槛照搬 Greenwood-Shleifer-You：两年涨幅 ≥100% 且超出大盘 ≥100 个百分点。
    原研究用的是整个行业的市值加权组合，这里只有 2 到 4 只代表股，只能当参考。
    这组数对向下的主题同样有用（两年跌了多少、是否越跌越快、波动是否飙升），
    但急涨门槛只对上涨成立；急跌没有对应的研究门槛，所以不设“急跌门槛”。"""
    b = path_stats(raw[bench])
    rows, stats = [], []
    for c in codes:
        st = path_stats(raw[c])
        if st is None:
            rows.append({"代码": c, "说明": "不足两年周线，不算"})
            continue
        stats.append(st)
        rows.append({
            "代码": c,
            "两年涨幅%": round(st["two"] * 100),
            "前一年涨幅%": round(st["first"] * 100),
            "近一年涨幅%": round(st["recent"] * 100),
            "加速度": round(st["accel"], 2),
            "近一年年化波动%": None if st["vol_now"] is None else round(st["vol_now"]),
            "波动变化（百分点）": None if st["vol_change"] is None else round(st["vol_change"]),
            "近一年上涨周占比%": None if st["up_share"] is None else round(st["up_share"] * 100),
        })
    out = {"各代码": rows}
    if stats and b:
        avg = lambda k: sum(st[k] for st in stats) / len(stats)
        two, net = avg("two"), avg("two") - b["two"]
        vc = [st["vol_change"] for st in stats if st["vol_change"] is not None]
        out["主题（等权）"] = {
            "两年涨幅%": round(two * 100),
            "两年超出基准（百分点）": round(net * 100),
            "加速度": round(avg("accel"), 2),
            "波动变化（百分点）": round(sum(vc) / len(vc)) if vc else None,
            "达到急涨门槛": two >= 1 and net >= 1,
        }
        out["基准两年涨幅%"] = round(b["two"] * 100)
    return out


def summary(code, weeks, idx, bench_idx):
    last = idx[-1]
    peak = max(range(len(idx)), key=lambda k: idx[k])
    low = min(range(len(idx)), key=lambda k: idx[k])
    line = {
        "代码": code,
        "区间涨跌%": round(last - 100, 1),
        "两年内最高点日期": weeks[peak],
        "距最高点%": round((last / idx[peak] - 1) * 100, 1),
        "最低点日期": weeks[low],
        "距最低点%": round((last / idx[low] - 1) * 100, 1),
    }
    if bench_idx is not None:
        line["相对基准，百分点"] = round(last - bench_idx[-1], 1)
    for n, label in ((13, "近13周涨跌%"), (26, "近26周涨跌%")):
        if len(idx) > n:
            line[label] = round((last / idx[-1 - n] - 1) * 100, 1)
    return line


LADDERS = ([1, 2, 5], [1, 1.5, 2, 3, 5, 7], [1, 1.2, 1.4, 1.6, 1.8, 2, 2.5, 3, 4, 5, 6, 8])


def log_ticks(lo, hi):
    """对数刻度的刻度值：先试 1-2-5，刻度太少再加密（和账本图表组件的算法一致）。"""
    for subs in LADDERS:
        ticks = [float(f"{m * 10 ** e:.3g}") for e in range(-1, 6) for m in subs]
        ticks = [t for t in ticks if lo * 0.97 <= t <= hi * 1.03]
        if len(ticks) >= 4:
            return ticks
    return ticks


def end_labels(ax, ends):
    """在图右侧写“代码 最新值”，上下错开，免得重叠。"""
    lo, hi = (math.log(v) for v in ax.get_ylim())
    gap = (hi - lo) * 0.05
    placed = None
    for v, c, col in sorted(ends, reverse=True):
        y = math.log(v) if placed is None or placed - math.log(v) >= gap else placed - gap
        placed = y
        y = math.exp(y)
        ax.text(1.005, y, f"{c} {v:.0f}", transform=ax.get_yaxis_transform(),
                va="center", fontsize=9, color=col)


def log_axis(ax, values):
    import matplotlib.ticker as mticker
    lo, hi = min(values), max(values)
    ax.set_yscale("log")
    ax.set_ylim(lo * 0.9, hi * 1.1)
    ax.yaxis.set_major_locator(mticker.FixedLocator(log_ticks(lo, hi)))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax.yaxis.set_minor_formatter(mticker.NullFormatter())
    ax.yaxis.set_minor_locator(mticker.NullLocator())



def money(v, _=None):
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= size:
            return f"{v / size:.3g}{unit}"
    return f"{v:.3g}"


def draw_candles(ax, axv, bars, draw, market, x_range):
    import matplotlib.dates as mdates
    import matplotlib.patches as mpatches
    import matplotlib.ticker as mticker
    from datetime import date
    up_col, down_col = CANDLE[market]
    xs = [mdates.date2num(date.fromisoformat(b["w"])) for b in bars]
    cols = [up_col if b["c"] >= b["o"] else down_col for b in bars]
    ax.vlines(xs, [b["l"] for b in bars], [b["h"] for b in bars], colors=cols, linewidth=0.9)
    bottoms = [min(b["o"], b["c"]) for b in bars]
    heights = [max(abs(b["c"] - b["o"]), b["c"] * 0.003) for b in bars]
    ax.bar(xs, heights, bottom=bottoms, width=4.2, color=cols, edgecolor=cols, linewidth=0.5)
    closes = [b["c"] for b in bars]
    for n, col, lw, lab in ((10, "#f59e0b", 1.2, "10w MA"), (40, "#2563eb", 1.8, "40w MA")):
        pts = [(x, ma(closes, n, k)) for k, x in enumerate(xs) if k + 1 >= n]
        if pts:
            ax.plot([p[0] for p in pts], [p[1] for p in pts], color=col, linewidth=lw, label=lab)
    for i, t, v in draw["piv"]:
        ax.plot(xs[i], v * (1.04 if t == "H" else 0.96), marker="v" if t == "H" else "^",
                color="0.25", markersize=6, linestyle="none")
    if draw["box"]:
        s, e, lo, hi = draw["box"]
        ax.add_patch(mpatches.Rectangle((xs[s] - 3, lo), xs[e] - xs[s] + 6, hi - lo,
                                        facecolor="#a78bfa", alpha=0.18, edgecolor="#7c3aed", linewidth=1))
    lo_all, hi_all = min(b["l"] for b in bars), max(b["h"] for b in bars)
    log_axis(ax, [lo_all, hi_all])
    ax.set_ylim(lo_all * 0.9, hi_all * 1.1)
    ax.grid(True, color="0.92")
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    ax.set_xlim(*x_range)
    amts = [b["a"] for b in bars]
    if any(a > 0 for a in amts):
        axv.bar(xs, amts, width=4.2, color=cols, alpha=0.8)
        avg = [(x, mean(amts[max(0, k - VOL_BASE + 1):k + 1])) for k, x in enumerate(xs) if k + 1 >= VOL_BASE]
        axv.plot([p[0] for p in avg], [p[1] for p in avg], color="0.3", linewidth=1)
        axv.yaxis.set_major_formatter(mticker.FuncFormatter(money))
        axv.yaxis.set_major_locator(mticker.MaxNLocator(3))
    else:
        axv.text(0.5, 0.5, "no turnover data", transform=axv.transAxes, ha="center", va="center", color="0.5")
    axv.set_ylabel("turnover", fontsize=8)
    axv.grid(True, color="0.92")
    axv.set_xlim(*x_range)


def draw_png(path, market, weeks, codes, bench, idx, bars, structs):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec, GridSpecFromSubplotSpec
    from datetime import date

    ncol = 1 if len(codes) == 1 else 2
    nrow = (len(codes) + ncol - 1) // ncol
    fig = plt.figure(figsize=(8 * ncol if ncol > 1 else 12, 5.2 * nrow + 4.6))
    outer = GridSpec(nrow + 1, 1, figure=fig, height_ratios=[5.2] * nrow + [4.2], hspace=0.32,
                     top=0.95, bottom=0.04)
    xs_all = [mdates.date2num(date.fromisoformat(w)) for w in weeks]
    x_range = (xs_all[0] - 5, xs_all[-1] + 5)
    up_word = "red" if market == "cn" else "green"
    down_word = "green" if market == "cn" else "red"
    locator = lambda: mdates.MonthLocator(bymonth=(1, 4, 7, 10))
    for k, c in enumerate(codes):
        row = GridSpecFromSubplotSpec(1, ncol, subplot_spec=outer[k // ncol], wspace=0.18)
        cell = GridSpecFromSubplotSpec(2, 1, subplot_spec=row[k % ncol], height_ratios=[3, 1], hspace=0.05)
        ax = fig.add_subplot(cell[0])
        axv = fig.add_subplot(cell[1], sharex=ax)
        draw_candles(ax, axv, bars[c], structs[c]["_draw"], market, x_range)
        last = bars[c][-1]
        ax.set_title(f"{c}  weekly, log scale  last close {fmt(last['c'])} ({last['w']})\n"
                     f"{structs[c]['_draw']['en']}", fontsize=10)
        plt.setp(ax.get_xticklabels(), visible=False)
        axv.xaxis.set_major_locator(locator())
        axv.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        axv.tick_params(axis="x", labelsize=8)

    bottom = GridSpecFromSubplotSpec(1, 2, subplot_spec=outer[nrow], wspace=0.22)
    ax1, ax2 = fig.add_subplot(bottom[0]), fig.add_subplot(bottom[1])
    colours = plt.get_cmap("tab10").colors
    ends = []
    for k, c in enumerate(codes):
        ax1.plot(xs_all, idx[c], color=colours[k], linewidth=1.8, label=c)
        ends.append((idx[c][-1], c, colours[k]))
    ax1.plot(xs_all, idx[bench], color="0.45", linewidth=1.5, linestyle="--", label=f"{bench} (benchmark)")
    ends.append((idx[bench][-1], bench, "0.35"))
    ax1.axhline(100, color="0.6", linewidth=0.8)
    log_axis(ax1, [v for c in codes + [bench] for v in idx[c] if v] + [100])
    ax1.set_title(f"weekly close, {weeks[0]} = 100 (log scale)", fontsize=10)
    ax1.legend(loc="upper left", fontsize=8, frameon=False)
    end_labels(ax1, ends)
    rel_ends, rel_all = [], [100]
    for k, c in enumerate(codes):
        rel = [a / b * 100 if a is not None and b else None for a, b in zip(idx[c], idx[bench])]
        ax2.plot(xs_all, rel, color=colours[k], linewidth=1.6, label=c)
        rel_ends.append((rel[-1], c, colours[k]))
        rel_all += [v for v in rel if v]
    ax2.axhline(100, color="0.45", linewidth=1, linestyle="--")
    log_axis(ax2, rel_all)
    end_labels(ax2, rel_ends)
    ax2.set_title(f"relative to {bench}, {weeks[0]} = 100 (above 100 = beat {bench})", fontsize=10)
    for a in (ax1, ax2):
        a.grid(True, color="0.92")
        a.xaxis.set_major_locator(locator())
        a.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        a.tick_params(axis="x", labelsize=8)
    fig.suptitle(f"Candles: {up_word} = week closed above its open, {down_word} = below. "
                 "Triangles = swing highs/lows found by the program. Shaded box = latest sideways range (by closes).",
                 fontsize=9, y=0.985)
    fig.savefig(path, dpi=90, bbox_inches="tight")
    plt.close(fig)

def slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower() or "x"


def finding(weeks, codes, bench, idx):
    """一句从完整周线算出的结论，和标准输出的摘要用同一套数字。"""
    def off_peak(c):
        return (idx[c][-1] / max(idx[c]) - 1) * 100
    parts = "、".join(f"{c} {off_peak(c):+.0f}%" for c in codes)
    return f"距两年内最高点：{parts}；{bench} {off_peak(bench):+.0f}%"


def widget_code(weeks, codes, bench, idx, component):
    # 隔周取一个点，最后一周一定保留，起点一周也保留
    keep = sorted(set(range(len(weeks) - 1, -1, -2)) | {0})
    rows = []
    for c in codes + [bench]:
        for k in keep:
            v = idx[c][k]
            if v is not None:
                rows.append("{d:'%s',s:'%s',v:%d}" % (weeks[k], c, round(v)))
    data = ",".join(rows)
    hue = ", ".join(f"'var(--cds-chart-categorical-{k + 1})'" for k in range(len(codes)))
    title = "代表股与大盘：两年周线走势"
    return (
        f"export default () => <claude.Visualize data-claude-component='{component}' "
        f"sources={{{{rows:{{kind:'data', data:[{data}]}}}}}}>{{({{rows, datum}}) => {{ "
        f"const bench = '{bench}'; const names = [...new Set(rows.map(r => r.s))]; "
        "const dates = [...new Set(rows.map(r => r.d))]; const stocks = names.filter(n => n !== bench); "
        "const left = 56, right = 640, y0 = 110, y1 = 350; const vals = rows.map(r => r.v).concat([100]); "
        "const vmin = Math.min(...vals), vmax = Math.max(...vals); "
        "const ladder = subs => [-1, 0, 1, 2, 3, 4, 5].flatMap(e => subs.map(m => +(m * Math.pow(10, e)).toPrecision(3))).filter(t => t >= vmin * 0.97 && t <= vmax * 1.03); "
        "const ticks = [[1, 2, 5], [1, 1.5, 2, 3, 5, 7], [1, 1.2, 1.4, 1.6, 1.8, 2, 2.5, 3, 4, 5, 6, 8]].map(ladder).find(t => t.length >= 4) || ladder([1, 1.2, 1.4, 1.6, 1.8, 2, 2.5, 3, 4, 5, 6, 8]); "
        "const lo = Math.log(vmin * 0.9), hi = Math.log(vmax * 1.1); "
        "const x = d => left + dates.indexOf(d) / (dates.length - 1) * (right - left); "
        "const y = v => y1 - (Math.log(v) - lo) / (hi - lo) * (y1 - y0); "
        f"const hues = [{hue}]; "
        "const colour = n => n === bench ? 'var(--cds-chart-muted)' : hues.at(stocks.indexOf(n)); "
        "const tag = n => n.replace(/[^A-Za-z0-9]+/g, '-').toLowerCase(); "
        "const series = names.map(n => ({n, pts: rows.filter(r => r.s === n)})); "
        "const ends = series.map(o => ({n: o.n, r: o.pts.at(-1)})).sort((a, b) => b.r.v - a.r.v); "
        "let prev = -100; const placed = ends.map(e => { const at = Math.max(y(e.r.v), prev + 15); prev = at; return {n: e.n, r: e.r, at}; }); "
        "const marks = dates.filter((d, k) => k > 0 && d.slice(0, 7) !== dates.at(k - 1).slice(0, 7) && ['01', '04', '07', '10'].includes(d.slice(5, 7))); "
        f"return <svg viewBox='0 0 760 380' role='img' aria-label='{title}' fontSize='12'>"
        f"<text data-claude-text-id='title' x='56' y='26' fontSize='16' fill='var(--cds-text-primary)'>{title}</text>"
        "<text x='56' y='48' fill='var(--cds-text-secondary)'>{`周收盘价，${dates.at(0)} 记为 100，最新一周 ${dates.at(-1)}；灰色虚线是基准 ${bench}`}</text>"
        f"<text data-claude-text-id='finding' x='56' y='68' fill='var(--cds-text-primary)'>{finding(weeks, codes, bench, idx)}</text>"
        "<text data-claude-text-id='scale-note' x='56' y='88' fill='var(--cds-text-secondary)'>纵轴是对数刻度：同样的高度代表同样的涨跌幅度</text>"
        "<g data-claude-anchor='y-axis'>{ticks.map(t => <g key={t}><line x1={left} x2={right} y1={y(t)} y2={y(t)} "
        "stroke={t === 100 ? 'var(--cds-chart-reference)' : 'var(--cds-chart-grid)'} strokeDasharray={t === 100 ? '4 3' : undefined}/>"
        "<text x={left - 8} y={y(t) + 4} textAnchor='end' fill='var(--cds-text-secondary)'>{t}</text></g>)}</g>"
        "<g data-claude-anchor='x-axis'>{marks.map(d => <g key={d}><line x1={x(d)} x2={x(d)} y1={y1} y2={y1 + 5} stroke='var(--cds-chart-axis)'/>"
        "<text x={x(d)} y={y1 + 20} textAnchor='middle' fill='var(--cds-text-secondary)'>{d.slice(0, 7)}</text></g>)}</g>"
        "{series.map(o => <g key={o.n} data-claude-anchor={`series-${tag(o.n)}`}>"
        "<path d={o.pts.map((r, k) => `${k ? 'L' : 'M'}${x(r.d).toFixed(1)},${y(r.v).toFixed(1)}`).join(' ')} fill='none' "
        "stroke={colour(o.n)} strokeWidth={o.n === bench ? 1.5 : 2.2} strokeDasharray={o.n === bench ? '5 4' : undefined}/>"
        "{o.pts.map(r => <circle key={r.d} cx={x(r.d)} cy={y(r.v)} r='5' fill='transparent' {...datum(r, 'v')}>"
        "<title>{`${r.s} ${r.d}：${r.v}`}</title></circle>)}</g>)}"
        "<g data-claude-anchor='end-labels'>{placed.map(p => <text key={p.n} x={right + 8} y={p.at + 4} "
        "fill={colour(p.n)} {...datum(p.r, 'v')}>{`${p.n} ${p.r.v}`}</text>)}</g>"
        "</svg>; }}</claude.Visualize>;"
    )



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=sorted(BENCH), required=True)
    ap.add_argument("--codes", required=True, help="逗号分隔，最多 4 个，例如 MU,WDC,SNDK")
    ap.add_argument("--out", required=True, help="输出文件名前缀")
    ap.add_argument("--ohlcv", default="ohlcv", help="周线文件夹，缺的文件自动下载到这里")
    ap.add_argument("--bench", help="默认美股 SPY，A 股 000300.SH")
    args = ap.parse_args()

    bench = args.bench or BENCH[args.market]
    codes = [c.strip() for c in args.codes.split(",") if c.strip() and c.strip() != bench][:4]
    bars, missing = {}, []
    for c in codes + [bench]:
        path = ensure_file(args.ohlcv, args.market, c)
        got = load_bars(path) if path else []
        if got:
            bars[c] = got
        else:
            missing.append(c)
    if missing:
        print(f"找不到周线：{', '.join(missing)}（代码写法见 README）", file=sys.stderr)
        codes = [c for c in codes if c in bars]
        if not codes or bench not in bars:
            sys.exit(1)

    weeks = [b["w"] for b in bars[bench]]
    by_week = {c: {b["w"]: b["c"] for b in bars[c]} for c in codes + [bench]}
    raw = {c: [by_week[c].get(w) for w in weeks] for c in codes + [bench]}
    start = common_start(raw)
    if start is None:
        sys.exit("有代码在两年内没有任何周线数据")
    idx = {c: rebase(raw[c], start) for c in codes + [bench]}
    notes = []
    if start > 0:
        late = [c for c in codes if raw[c][start - 1] is None]
        notes.append(f"走势对比的起点是所有代码都有数据的第一周 {weeks[start]}（{', '.join(late)} 在此之前没有数据）")
    shown = weeks[start:]
    if len(shown) < MIN_WEEKS:
        notes.append(f"只有 {len(shown)} 周数据，形状参考价值有限")
    for c in codes:
        if bars[c][-1]["w"] != weeks[-1]:
            notes.append(f"{c} 最新一周是 {bars[c][-1]['w']}，比基准的 {weeks[-1]} 旧（停牌或数据滞后）")

    structs = {c: structure(c, bars[c], by_week[bench]) for c in codes}
    structs[bench] = structure(bench, bars[bench], by_week[bench], is_bench=True)
    draw_png(args.out + ".png", args.market, shown, codes, bench, idx, bars, structs)
    component = "theme-price-" + slug("-".join(codes))
    code = widget_code(shown, codes, bench, idx, component)
    # 代码里没有双引号和反斜杠，原样放进 JSON 字符串也不用转义
    assert '"' not in code and "\\" not in code
    with open(args.out + ".widget.txt", "w", encoding="utf-8") as f:
        f.write(code)

    clean = lambda st: {k: v for k, v in st.items() if not k.startswith("_")}
    print(json.dumps({
        "数据": f"ohlcv 周线（复权），{shown[0]} 至 {shown[-1]}；走势对比起点记为 100",
        "基准": bench,
        "说明": notes,
        "各代码": [summary(c, shown, idx[c], idx[bench] if c != bench else None) for c in codes]
                 + [summary(bench, shown, idx[bench], None)],
        "价格结构（解读见《股票价格理论手册》）": {c: clean(structs[c]) for c in codes + [bench]},
        "趋势体质（两年完整周线）": heat(codes, bench, raw),
        "文件": [args.out + ".png", args.out + ".widget.txt"],
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
