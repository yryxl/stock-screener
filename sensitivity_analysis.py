"""
参数敏感性分析（2026-04-19 用户提出 - 防止过拟合）

5 个核心阈值 × 3 档（基线 / -20% / +20%）= 15 次回测
看每个阈值变动对最终结果的影响：
- 如果稍微变动就大幅恶化 → 过拟合风险高
- 如果变动 ±20% 结果基本稳定 → 阈值合理

回测范围：2010-01 到 2024-12（15 年），10 万本金
输出：sensitivity_results.json + 控制台对比表
"""
import sys
import os
import time
import json
import importlib

sys.stdout.reconfigure(encoding='utf-8')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


# ============================================================
# 5 个核心阈值（可 monkeypatch）
# ============================================================
THRESHOLDS = {
    'GOOD_COMPANY_ROE_THRESHOLD': {
        'base': 15.0, 'low': 12.0, 'high': 18.0,
        'desc': '好公司 ROE 门槛（5y 历史均值）'
    },
    'SUPER_GOOD_ROE_THRESHOLD': {
        'base': 25.0, 'low': 20.0, 'high': 30.0,
        'desc': '超级好公司 ROE 门槛'
    },
    'SUPER_GOOD_GM_THRESHOLD': {
        'base': 40.0, 'low': 32.0, 'high': 48.0,
        'desc': '超级好公司毛利率门槛'
    },
    'INDIVIDUAL_PE_HARD_VETO_MULTIPLIER': {
        'base': 1.2, 'low': 0.96, 'high': 1.44,
        'desc': '个股 PE 硬否决倍数（× 行业 fair_high）'
    },
    'BUDGET_HEAVY': {
        'base': 0.40, 'low': 0.32, 'high': 0.48,
        'desc': '重仓买入预算占比'
    },
}


def run_one(threshold_name, value, start_year, start_month):
    """跑一次回测，返回关键指标 dict"""
    import backtest_autorun

    # 备份 + 设值
    original = getattr(backtest_autorun, threshold_name)
    setattr(backtest_autorun, threshold_name, value)

    try:
        t0 = time.time()
        r = backtest_autorun.run_backtest(
            start_year, start_month,
            initial_capital=100000, verbose=False
        )
        elapsed = time.time() - t0

        years = (2025 - start_year) - (start_month - 12) / 12
        annual_return = ((r['final_total'] / r['initial_capital']) ** (1 / years) - 1) * 100 \
            if years > 0 and r['final_total'] > 0 else None

        swap_events = r.get('swap_events', [])
        right = sum(1 for e in swap_events if '换对' in e.get('verdict', ''))
        wrong = sum(1 for e in swap_events if '换错' in e.get('verdict', ''))

        return {
            'threshold': threshold_name,
            'value': value,
            'final_total': round(r['final_total'], 0),
            'final_pnl': round(r['final_pnl'], 0),
            'annual_return_pct': round(annual_return, 2) if annual_return is not None else None,
            'trade_count': r['trade_count'],
            'swap_right': right,
            'swap_wrong': wrong,
            'total_dividends': round(r['total_dividends'], 0),
            'total_fees': round(r['total_fees'], 0),
            'elapsed_sec': round(elapsed, 1),
        }
    finally:
        setattr(backtest_autorun, threshold_name, original)


def main():
    # 选 2010-2024 共 15 年
    start_year = 2010
    start_month = 1
    print(f"\n参数敏感性分析（{start_year}-{start_month:02d} 起 ~ 2025-12，约 15 年）")
    print(f"5 个阈值 × 3 档 = 15 次回测，预计 ~5 分钟\n")

    results = {}
    for name, cfg in THRESHOLDS.items():
        print(f"\n=== {name} ({cfg['desc']}) ===")
        results[name] = {}
        for level, key in [('base', 'base'), ('low (-20%)', 'low'), ('high (+20%)', 'high')]:
            value = cfg[key]
            print(f"  跑 {level} = {value} ...", end=' ', flush=True)
            r = run_one(name, value, start_year, start_month)
            results[name][key] = r
            print(f"{r['elapsed_sec']}s | 终值 ¥{r['final_total']:,.0f} | "
                  f"年化 {r['annual_return_pct']}%")

    # 保存结果
    out_path = os.path.join(SCRIPT_DIR, 'sensitivity_results.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({
            'config': {
                'start': f'{start_year}-{start_month:02d}',
                'end': '2025-12',
                'initial_capital': 100000,
            },
            'thresholds': THRESHOLDS,
            'results': results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n💾 结果保存到 {out_path}")

    # 输出对比表
    print("\n" + "=" * 100)
    print(f"{'阈值':<40} {'基线':<20} {'-20%':<20} {'+20%':<20} {'敏感度':<10}")
    print("-" * 100)

    summary = []
    for name, cfg in THRESHOLDS.items():
        base = results[name]['base']['final_total']
        low = results[name]['low']['final_total']
        high = results[name]['high']['final_total']

        # 敏感度 = max 变动幅度
        max_diff = max(abs(low - base), abs(high - base))
        max_diff_pct = max_diff / base * 100 if base > 0 else 0

        if max_diff_pct < 5:
            tag = '🟢 稳定'
        elif max_diff_pct < 15:
            tag = '🟡 中等'
        else:
            tag = '🔴 敏感'

        ann_base = results[name]['base']['annual_return_pct']
        ann_low = results[name]['low']['annual_return_pct']
        ann_high = results[name]['high']['annual_return_pct']

        summary.append({
            'threshold': name,
            'desc': cfg['desc'],
            'base_value': cfg['base'],
            'low_value': cfg['low'],
            'high_value': cfg['high'],
            'base_final': base,
            'low_final': low,
            'high_final': high,
            'max_diff_pct': round(max_diff_pct, 1),
            'tag': tag,
            'ann_base': ann_base,
            'ann_low': ann_low,
            'ann_high': ann_high,
        })
        print(f"{name:<40} ¥{base:>10,.0f} ({ann_base}%)  "
              f"¥{low:>10,.0f} ({ann_low}%)  "
              f"¥{high:>10,.0f} ({ann_high}%)  "
              f"{tag} {max_diff_pct:.1f}%")

    return summary


if __name__ == '__main__':
    summary = main()
