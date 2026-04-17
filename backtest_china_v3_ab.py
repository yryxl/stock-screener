"""
中国国情版 v3 规则 A/B 对比回测

A 组：CHINA_V3_ENABLED=True  （开启新规则）
B 组：CHINA_V3_ENABLED=False （关闭，相当于原模型）

对比：
  - 终值收益率
  - 最大回撤
  - 踩雷次数（ROE 转负/退市）
  - 交易次数

固定条件：
  - 起始时间 4 个（覆盖牛市起点/熊市起点/震荡市）
  - 本金 10万 / 100万
  - 回测到 2025-12 结束
"""

import sys
sys.stdout.reconfigure(encoding='utf-8')

import backtest_engine as be
from backtest_autorun import run_backtest


# ------- 6 个起始时间（覆盖各种市场周期，更稳健的统计样本）-------
TIME_POINTS = [
    (2014, 1),   # 熊市底（测下一轮牛市参与）
    (2015, 6),   # 牛市顶（测下跌保护）
    (2018, 1),   # 熊市起点（测底部吸收）
    (2019, 6),   # 反弹期
    (2020, 3),   # 疫情底反弹
    (2022, 4),   # 科技泡沫后
]

# ------- 本金档位 -------
CAPITALS = [100000, 1000000]


def run_single(enable_v3, sy, sm, cap):
    """单次回测"""
    be.CHINA_V3_ENABLED = enable_v3
    r = run_backtest(sy, sm, initial_capital=cap, verbose=False)
    return {
        "final_pnl": r["final_pnl"],
        "trade_count": r["trade_count"],
        "total_fees": r["total_fees"],
        "total_dividends": r["total_dividends"],
    }


def main():
    print("=" * 95)
    print("  中国国情版 v3 A/B 对比回测")
    print(f"  起始时间: {', '.join(f'{y}-{m:02d}' for y, m in TIME_POINTS)}")
    print(f"  本金档位: {CAPITALS}")
    print(f"  规则差异: 跑步机硬否决 / 冲浪者降级 / 过路费放宽 ROE 门槛")
    print("=" * 95)

    summary = []
    for sy, sm in TIME_POINTS:
        for cap in CAPITALS:
            print(f"\n━━━ 起始 {sy}-{sm:02d} · 本金 ¥{cap:,} ━━━")
            a = run_single(True, sy, sm, cap)
            b = run_single(False, sy, sm, cap)
            delta = a["final_pnl"] - b["final_pnl"]
            arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "=")
            print(f"  A(v3开) 收益 {a['final_pnl']:+7.2f}% | 交易 {a['trade_count']:3} 笔 | 分红 ¥{a['total_dividends']:>8,.0f}")
            print(f"  B(v3关) 收益 {b['final_pnl']:+7.2f}% | 交易 {b['trade_count']:3} 笔 | 分红 ¥{b['total_dividends']:>8,.0f}")
            print(f"  差值    {delta:+7.2f}pp {arrow}")
            summary.append({
                "sy": sy, "sm": sm, "cap": cap,
                "a_pnl": a["final_pnl"], "b_pnl": b["final_pnl"], "delta": delta,
                "a_trades": a["trade_count"], "b_trades": b["trade_count"],
            })

    # 总表
    print(f"\n\n{'='*95}")
    print("  总览")
    print(f"{'='*95}")
    print(f"  {'起始':>8} | {'本金':>10} | {'A(v3开)':>9} | {'B(v3关)':>9} | {'差值':>7} | {'交易变化':>9}")
    print("  " + "-" * 90)
    for s in summary:
        trade_diff = s['a_trades'] - s['b_trades']
        print(f"  {s['sy']}-{s['sm']:02d} | ¥{s['cap']:>9,} | "
              f"{s['a_pnl']:>+8.2f}% | {s['b_pnl']:>+8.2f}% | "
              f"{s['delta']:>+6.2f}pp | {trade_diff:>+8}")

    # 平均
    avg_a = sum(s['a_pnl'] for s in summary) / len(summary)
    avg_b = sum(s['b_pnl'] for s in summary) / len(summary)
    avg_delta = avg_a - avg_b
    wins = sum(1 for s in summary if s['delta'] > 0)
    losses = sum(1 for s in summary if s['delta'] < 0)
    ties = sum(1 for s in summary if s['delta'] == 0)
    print("  " + "-" * 90)
    print(f"  {'均值':>8} | {'':>10} | {avg_a:>+8.2f}% | {avg_b:>+8.2f}% | {avg_delta:>+6.2f}pp")
    print(f"\n  胜平负（A 对比 B）：{wins}胜 {ties}平 {losses}负（共 {len(summary)} 场）")


if __name__ == "__main__":
    main()
