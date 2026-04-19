"""
Walk-Forward 验证（2026-04-19 用户提出 - 防止过拟合）

把 15 年（2010-2024）切为 5 段不重叠的 3 年期，
每段独立跑回测，看策略是否在每个时间窗口都稳定。

如果某段大亏、其它段大赚 → 策略只在特定市场环境管用（过拟合）
如果各段表现接近 → 策略具有时间稳定性（不过拟合）

输出：walk_forward_results.json + 控制台对比表
"""
import sys
import os
import time
import json
import statistics

sys.stdout.reconfigure(encoding='utf-8')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


# 5 段 × 3 年 不重叠
# hs300_annual_pct：沪深 300 同期年化收益率（粗略估算，做基准对比用）
# 数据来源：沪深 300 收盘点位粗算，2010~2024 完整数据可用 akshare 校准
WINDOWS = [
    {'name': '2010-2012', 'start': (2010, 1), 'end': (2012, 12), 'hs300_annual_pct': -10.0},
    {'name': '2013-2015', 'start': (2013, 1), 'end': (2015, 12), 'hs300_annual_pct': 14.0},
    {'name': '2016-2018', 'start': (2016, 1), 'end': (2018, 12), 'hs300_annual_pct': -7.0},
    {'name': '2019-2021', 'start': (2019, 1), 'end': (2021, 12), 'hs300_annual_pct': 18.0},
    {'name': '2022-2024', 'start': (2022, 1), 'end': (2024, 12), 'hs300_annual_pct': -11.0},
]


def run_window(window):
    """跑一个时间窗口，返回关键指标"""
    from backtest_autorun import run_backtest
    sy, sm = window['start']
    ey, em = window['end']
    t0 = time.time()
    r = run_backtest(
        sy, sm, end_year=ey, end_month=em,
        initial_capital=100000, verbose=False
    )
    elapsed = time.time() - t0

    years = ((ey - sy) * 12 + (em - sm + 1)) / 12
    annual_return = ((r['final_total'] / r['initial_capital']) ** (1 / years) - 1) * 100 \
        if years > 0 and r['final_total'] > 0 else None

    swap_events = r.get('swap_events', [])
    right = sum(1 for e in swap_events if '换对' in e.get('verdict', ''))
    wrong = sum(1 for e in swap_events if '换错' in e.get('verdict', ''))
    flat = sum(1 for e in swap_events if '持平' in e.get('verdict', ''))

    hs300 = window.get('hs300_annual_pct', 0)
    alpha = (annual_return - hs300) if annual_return is not None else None

    return {
        'window': window['name'],
        'years': round(years, 1),
        'final_total': round(r['final_total'], 0),
        'final_pnl': round(r['final_pnl'], 0),
        'pnl_pct': round((r['final_total'] / r['initial_capital'] - 1) * 100, 2),
        'annual_return_pct': round(annual_return, 2) if annual_return is not None else None,
        'hs300_annual_pct': hs300,
        'alpha_pp': round(alpha, 2) if alpha is not None else None,
        'trade_count': r['trade_count'],
        'swap_right': right,
        'swap_wrong': wrong,
        'swap_flat': flat,
        'total_dividends': round(r['total_dividends'], 0),
        'total_fees': round(r['total_fees'], 0),
        'elapsed_sec': round(elapsed, 1),
    }


def main():
    print("\nWalk-Forward 验证（5 段 × 3 年 不重叠）")
    print("每段 ~1.5 秒，共 ~10 秒\n")

    results = []
    for w in WINDOWS:
        print(f"  跑 {w['name']} ...", end=' ', flush=True)
        r = run_window(w)
        results.append(r)
        print(f"{r['elapsed_sec']}s | 终值 ¥{r['final_total']:,.0f} | "
              f"年化 {r['annual_return_pct']}% | "
              f"换对 {r['swap_right']} 错 {r['swap_wrong']}")

    # 保存
    out_path = os.path.join(SCRIPT_DIR, 'walk_forward_results.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({
            'windows': WINDOWS,
            'results': results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n💾 结果保存到 {out_path}")

    # 稳定性分析
    print("\n" + "=" * 100)
    print(f"{'窗口':<14} {'策略年化':<12} {'沪深300':<12} {'Alpha':<12} {'终值':<14} {'换对/错':<10}")
    print("-" * 100)

    annuals = []
    alphas = []
    for r in results:
        ann = r['annual_return_pct']
        annuals.append(ann if ann is not None else 0)
        alpha = r.get('alpha_pp')
        alphas.append(alpha if alpha is not None else 0)
        alpha_tag = '✅' if alpha and alpha > 0 else ('❌' if alpha and alpha < -3 else '〰')
        print(f"{r['window']:<14} {ann:>8.2f}%    {r['hs300_annual_pct']:>8.1f}%    "
              f"{alpha:>+7.2f}pp {alpha_tag}    "
              f"¥{r['final_total']:>10,.0f}    {r['swap_right']}/{r['swap_wrong']}")

    print("-" * 100)
    if annuals:
        ann_mean = statistics.mean(annuals)
        ann_std = statistics.stdev(annuals) if len(annuals) > 1 else 0
        ann_min = min(annuals)
        ann_max = max(annuals)
        cv = (ann_std / abs(ann_mean) * 100) if ann_mean != 0 else None

        alpha_mean = statistics.mean(alphas)
        alpha_std = statistics.stdev(alphas) if len(alphas) > 1 else 0
        beat_count = sum(1 for a in alphas if a > 0)

        print(f"\n📊 策略 vs 沪深 300 跨窗口对比：")
        print(f"  策略年化均值：{ann_mean:+.2f}% (标准差 {ann_std:.2f}%)")
        print(f"  Alpha 均值：{alpha_mean:+.2f}pp (标准差 {alpha_std:.2f}pp)")
        print(f"  跑赢沪深 300 段数：{beat_count}/{len(alphas)}")
        print(f"  最差 Alpha：{min(alphas):+.2f}pp（{[r['window'] for r in results if r.get('alpha_pp')==min(alphas)][0]}）")
        print(f"  最好 Alpha：{max(alphas):+.2f}pp（{[r['window'] for r in results if r.get('alpha_pp')==max(alphas)][0]}）")

        # 评级（基于 alpha 而非 raw return，因为 raw 受市场影响）
        if beat_count == len(alphas) and alpha_mean > 0:
            tag = '🟢 全段跑赢沪深 300（策略稳健不过拟合）'
        elif beat_count >= len(alphas) * 0.6 and alpha_mean > 0:
            tag = '🟢 多数段跑赢沪深 300（策略稳定）'
        elif beat_count >= len(alphas) * 0.4:
            tag = '🟡 部分段跑赢（策略时段依赖性中等）'
        else:
            tag = '🔴 多数段跑输（策略可能过拟合）'
        print(f"  评级：{tag}")

        positive_windows = sum(1 for a in annuals if a > 0)
        print(f"\n📈 策略正收益段数：{positive_windows}/{len(annuals)}")
        print(f"  💡 注意：纯策略收益受市场影响大；Alpha 才是真正的'策略价值'指标")

    return results


if __name__ == '__main__':
    main()
