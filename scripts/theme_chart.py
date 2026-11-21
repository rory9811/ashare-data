"""画一个主题的走势图：几只代表股和基准，两年周线，起点记为 100。

输入：history_w.csv（本仓库 data/<market>/ 下，每周自动更新）和代码列表。
输出三样东西：
  <out>.png         给模型看。上图：各代码走势，起点 = 100，基准是灰色虚线；
                    下图：相对基准的强弱（代码 ÷ 基准，起点 = 100），在 100 之上
                    说明从起点算起跑赢了基准，线往上走说明这段时间在变强。
                    纵轴用对数刻度：同样的高度代表同样的涨跌幅度（100→200 和
                    1000→2000 一样高），涨了几十倍的股票也不会把别的线压扁。
  <out>.widget.txt  账本文档里图表组件的完整代码（数据已内嵌，隔周取一个点）。
  标准输出          数字摘要：区间涨跌、两年内最高点和日期、距最高点回撤、相对基准。
图只用来看形状；写进账本的数字一律取自摘要或 ma_latest.csv，不从图上读。

用法：
  python3 -I theme_chart.py --market us --history history_w.csv --codes MU,WDC,SNDK --out storage
只依赖标准库和 matplotlib。
"""

import argparse
import csv
import json
import math
import re
import sys

BENCH = {"us": "SPY", "cn": "000300.SH"}
MIN_WEEKS = 26  # 共同起点之后不足半年，就说明这一点


def load(path, codes):
    with open(path, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        weeks = header[1:]
        found = {}
        for row in reader:
            if row and row[0] in codes:
                found[row[0]] = [float(v) if v else None for v in row[1:]]
    return weeks, found


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


def draw_png(path, weeks, codes, bench, idx):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from datetime import date

    xs = [date.fromisoformat(w) for w in weeks]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7.5), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 2]})
    colours = plt.get_cmap("tab10").colors
    ends = []
    for k, c in enumerate(codes):
        ax1.plot(xs, idx[c], color=colours[k], linewidth=2, label=c)
        ends.append((idx[c][-1], c, colours[k]))
    ax1.plot(xs, idx[bench], color="0.45", linewidth=1.6, linestyle="--", label=f"{bench} (benchmark)")
    ends.append((idx[bench][-1], bench, "0.35"))
    ax1.axhline(100, color="0.6", linewidth=0.8)
    log_axis(ax1, [v for c in codes + [bench] for v in idx[c] if v] + [100])
    ax1.set_ylabel(f"weekly close, {weeks[0]} = 100\n(log scale: equal height = equal % move)")
    ax1.set_title(f"{', '.join(codes)} vs {bench}  |  {weeks[0]} to {weeks[-1]}", fontsize=12)
    ax1.grid(True, color="0.9")
    ax1.legend(loc="upper left", fontsize=9, frameon=False)
    end_labels(ax1, ends)

    rel_ends, rel_all = [], [100]
    for k, c in enumerate(codes):
        rel = [a / b * 100 if a is not None and b else None for a, b in zip(idx[c], idx[bench])]
        ax2.plot(xs, rel, color=colours[k], linewidth=1.8, label=c)
        rel_ends.append((rel[-1], c, colours[k]))
        rel_all += [v for v in rel if v]
    ax2.axhline(100, color="0.45", linewidth=1, linestyle="--")
    log_axis(ax2, rel_all)
    end_labels(ax2, rel_ends)
    ax2.set_ylabel(f"relative to {bench}\n(above 100 = beat {bench} since start)")
    ax2.grid(True, color="0.9")
    ax2.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 4, 7, 10)))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.tight_layout()
    fig.subplots_adjust(right=0.9)
    fig.savefig(path, dpi=110)
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
    ap.add_argument("--history", required=True, help="history_w.csv 的本地路径")
    ap.add_argument("--codes", required=True, help="逗号分隔，最多 4 个，例如 MU,WDC,SNDK")
    ap.add_argument("--out", required=True, help="输出文件名前缀")
    ap.add_argument("--bench", help="默认美股 SPY，A 股 000300.SH")
    args = ap.parse_args()

    bench = args.bench or BENCH[args.market]
    codes = [c.strip() for c in args.codes.split(",") if c.strip() and c.strip() != bench][:4]
    weeks, found = load(args.history, set(codes) | {bench})
    missing = [c for c in codes + [bench] if c not in found]
    if missing:
        print(f"周线里找不到：{', '.join(missing)}（代码写法见 README）", file=sys.stderr)
        codes = [c for c in codes if c in found]
        if not codes or bench not in found:
            sys.exit(1)

    start = common_start({c: found[c] for c in codes + [bench]})
    if start is None:
        sys.exit("有代码在两年内没有任何周线数据")
    weeks = weeks[start:]
    idx = {c: rebase(found[c], start) for c in codes + [bench]}
    notes = []
    if start > 0:
        late = [c for c in codes if found[c][start - 1] is None]
        notes.append(f"起点是所有代码都有数据的第一周 {weeks[0]}（{', '.join(late)} 在此之前没有数据）")
    if len(weeks) < MIN_WEEKS:
        notes.append(f"只有 {len(weeks)} 周数据，形状参考价值有限")

    draw_png(args.out + ".png", weeks, codes, bench, idx)
    component = "theme-price-" + slug("-".join(codes))
    code = widget_code(weeks, codes, bench, idx, component)
    # 代码里没有双引号和反斜杠，原样放进 JSON 字符串也不用转义
    assert '"' not in code and "\\" not in code
    with open(args.out + ".widget.txt", "w", encoding="utf-8") as f:
        f.write(code)

    print(json.dumps({
        "数据": f"history_w.csv 周收盘价（复权），{weeks[0]} 至 {weeks[-1]}，起点记为 100",
        "基准": bench,
        "说明": notes,
        "各代码": [summary(c, weeks, idx[c], idx[bench] if c != bench else None) for c in codes]
                 + [summary(bench, weeks, idx[bench], None)],
        "文件": [args.out + ".png", args.out + ".widget.txt"],
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
