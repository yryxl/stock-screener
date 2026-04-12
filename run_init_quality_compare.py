"""
不同初始持仓质量 × path_c 模型的对比回测

测试模型的"纠错能力"：不管你之前持有什么质量的股票，模型接管后表现如何？

6 种初始持仓质量：
  1. pure_cash    - 纯现金（对照组）
  2. random       - 纯随机抽 5 只
  3. all_good     - 全好股（茅台/五粮液/恒瑞/格力/伊利）
  4. all_bad      - 全垃圾（中石油/华锐/保千里/华夏幸福/海航）
  5. all_average  - 全普通（平安/兴业/万科/中免/招商）
  6. mixed_mess   - 混搭乱组合（1 好 + 1 烂 + 1 普通 + 1 周期 + 1 蓝筹）

只跑 path_c 模式 × 5 起点 × 4 本金 = 120 次回测
random 组跑 3 种子取均值，其它 5 组各跑 1 次 = 总计 150 次
"""

import json
import os
import sys
import time
sys.stdout.reconfigure(encoding="utf-8")

import backtest_autorun as ba
from backtest_autorun import run_backtest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROGRESS_FILE = os.path.join(SCRIPT_DIR, "backtest_init_quality.progress")


# 6 种初始持仓质量
INIT_CONFIGS = {
    "pure_cash": {
        "label": "纯现金（对照）",
        "n_stocks": 0,
        "stock_ids": None,
    },
    "all_good": {
        "label": "全好股（茅台/五粮液/恒瑞/格力/伊利）",
        "n_stocks": 0,
        "stock_ids": ["S01", "S02", "S04", "S05", "S31"],  # 茅台/五粮液/恒瑞/格力/伊利
    },
    "all_bad": {
        "label": "全垃圾（中石油/华锐/保千里/华夏幸福/海航）",
        "n_stocks": 0,
        "stock_ids": ["S06", "S07", "S09", "S35", "S37"],  # 中石油/华锐/保千里/华夏幸福/海航
    },
    "all_average": {
        "label": "全普通（平安/兴业/万科/中免/招商）",
        "n_stocks": 0,
        "stock_ids": ["S11", "S12", "S13", "S14", "S15"],
    },
    "mixed_mess": {
        "label": "乱组合（茅台+中石油+平安+宁德+大秦铁路）",
        "n_stocks": 0,
        "stock_ids": ["S01", "S06", "S11", "S17", "S51"],  # 好+烂+普通+妖+蓝筹
    },
    "random": {
        "label": "纯随机 5 只（3 种子均值）",
        "n_stocks": 5,
        "stock_ids": None,
    },
}


def write_progress(text):
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(text + "\n")


def main():
    open(PROGRESS_FILE, "w", encoding="utf-8").close()

    ba.STRATEGY_MODE = "path_c"

    capitals = [10000, 100000, 500000, 1000000]
    time_points = [
        (2008, 10),
        (2012, 10),
        (2015, 6),
        (2019, 8),
        (2022, 10),
    ]
    random_seeds = [42, 123, 7777]

    # 结构: results[config_name][ym] = {years, pnls: [cap0, cap1, cap2, cap3]}
    results = {}
    total_runs = 0
    for name, cfg in INIT_CONFIGS.items():
        n_seeds = len(random_seeds) if name == "random" else 1
        total_runs += len(time_points) * len(capitals) * n_seeds

    done = 0
    start_time = time.time()
    write_progress(f"=== 6 种初始质量 × path_c × 5 起点 × 4 本金 = {total_runs} 次回测 ===")

    for name, cfg in INIT_CONFIGS.items():
        results[name] = {}
        seeds = random_seeds if name == "random" else [42]

        for sy, sm in time_points:
            ym = f"{sy}-{sm:02d}"
            pnls_by_cap = [[] for _ in capitals]

            for cap_idx, cap in enumerate(capitals):
                for seed in seeds:
                    kwargs = {
                        "start_year": sy,
                        "start_month": sm,
                        "initial_capital": cap,
                        "verbose": False,
                        "initial_random_cash_pct": 0.20,
                    }
                    if cfg["stock_ids"]:
                        kwargs["initial_stock_ids"] = cfg["stock_ids"]
                    elif cfg["n_stocks"] > 0:
                        kwargs["initial_random_n_stocks"] = cfg["n_stocks"]
                        kwargs["initial_random_seed"] = seed

                    r = run_backtest(**kwargs)
                    pnl = r["final_pnl"]
                    pnls_by_cap[cap_idx].append(pnl)
                    done += 1
                    elapsed = time.time() - start_time
                    eta = elapsed / done * (total_runs - done) if done > 0 else 0
                    write_progress(
                        f"  [{done}/{total_runs}] {name:<13} {ym} ¥{cap:>8,} "
                        f"→ {pnl:+7.1f}%  ({elapsed:.0f}s / ETA {eta:.0f}s)"
                    )

            # 取每组的均值
            results[name][ym] = {
                "years": round((2025 - sy) + (12 - sm) / 12, 1),
                "pnls": [sum(p) / len(p) for p in pnls_by_cap],
            }

    # 保存
    result_path = os.path.join(SCRIPT_DIR, "backtest_init_quality.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 打印对比表
    write_progress(f"\n{'=' * 100}")
    write_progress(f"  6 种初始质量 × path_c · ¥100 万档对比")
    write_progress(f"{'=' * 100}")

    header = f"{'起点':<10}{'年数':<6}"
    for name in INIT_CONFIGS:
        header += f"{name:<14}"
    write_progress(header)
    write_progress("-" * 100)

    for ym in [f"{sy}-{sm:02d}" for sy, sm in time_points]:
        line = f"{ym:<10}{results[list(INIT_CONFIGS.keys())[0]][ym]['years']:<6.1f}"
        for name in INIT_CONFIGS:
            pnl = results[name][ym]["pnls"][3]  # ¥100 万档
            line += f"{pnl:>+10.1f}%   "
        write_progress(line)

    write_progress("-" * 100)
    line = f"{'均值':<10}{'':<6}"
    for name in INIT_CONFIGS:
        avg = sum(results[name][ym]["pnls"][3] for ym in results[name]) / len(results[name])
        line += f"{avg:>+10.1f}%   "
    write_progress(line)

    write_progress(f"\n总耗时: {(time.time() - start_time):.0f}s")
    write_progress(f"数据已保存: {result_path}")


if __name__ == "__main__":
    main()
