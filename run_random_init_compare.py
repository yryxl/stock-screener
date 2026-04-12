"""
随机半路接管 · 4 模式对比回测脚本

模拟用户"半路开始用模型"的真实场景：
  - 起点不是纯现金，而是已有 80% 资金随机配置 5 只股票 + 20% 现金
  - 模型从下个月开始接管，可能立即卖掉它认为高估的，也可能保留好的
  - 每个组合跑 3 次不同随机种子，取均值消除单次抽样噪声

跑 4 个模式：baseline / path_a / path_b / path_c
× 5 个代表性起点
× 4 档本金
× 3 个随机种子
= 240 次 run_backtest

进度实时写入 backtest_random_init.progress 文件，可用 cat 查看。
最终结果存到 backtest_random_init.json。
"""

import json
import os
import sys
import time
sys.stdout.reconfigure(encoding="utf-8")

import backtest_autorun as ba
from backtest_autorun import run_backtest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROGRESS_FILE = os.path.join(SCRIPT_DIR, "backtest_random_init.progress")
RESULT_FILE = os.path.join(SCRIPT_DIR, "backtest_random_init.json")


def write_progress(text):
    """覆盖写入进度文件，便于外部 cat 实时查看"""
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(text + "\n")


def main():
    # 清空进度文件
    open(PROGRESS_FILE, "w", encoding="utf-8").close()

    capitals = [10000, 100000, 500000, 1000000]
    time_points = [
        (2008, 10),  # 次贷危机底
        (2015, 6),   # 5178 牛顶
        (2018, 12),  # 18 熊底
        (2019, 8),   # 白酒泡沫
        (2022, 10),  # 政策底
    ]
    seeds = [42, 123, 7777]
    modes = ["baseline", "path_a", "path_b", "path_c"]

    results = {m: {} for m in modes}
    total = len(modes) * len(time_points) * len(capitals) * len(seeds)
    done = 0
    start_time = time.time()

    write_progress(f"=== 开始 {total} 次回测 ===")

    for mode in modes:
        ba.STRATEGY_MODE = mode
        for sy, sm in time_points:
            ym = f"{sy}-{sm:02d}"
            if ym not in results[mode]:
                results[mode][ym] = {"years": None, "pnls_by_cap": [[] for _ in capitals]}
            for cap_idx, cap in enumerate(capitals):
                for seed in seeds:
                    r = run_backtest(
                        sy, sm,
                        initial_capital=cap,
                        verbose=False,
                        initial_random_n_stocks=5,
                        initial_random_seed=seed,
                        initial_random_cash_pct=0.20,
                    )
                    pnl = r["final_pnl"]
                    results[mode][ym]["pnls_by_cap"][cap_idx].append(pnl)
                    if results[mode][ym]["years"] is None:
                        years = (2025 - sy) + (12 - sm) / 12
                        results[mode][ym]["years"] = round(years, 1)
                    done += 1
                    elapsed = time.time() - start_time
                    eta = elapsed / done * (total - done) if done > 0 else 0
                    write_progress(
                        f"  [{done}/{total}] {mode:<10} {ym} ¥{cap:>8,} seed={seed:<5} "
                        f"→ {pnl:+7.1f}%  (elapsed {elapsed:.0f}s, ETA {eta:.0f}s)"
                    )

    # 保存结果
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    write_progress(f"\n=== 完成 ===")
    write_progress(f"数据已保存: {RESULT_FILE}")
    write_progress(f"总耗时: {(time.time() - start_time):.0f}s")


if __name__ == "__main__":
    main()
