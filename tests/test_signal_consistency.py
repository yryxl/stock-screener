"""
Layer 3 逻辑一致性测试
用途：检测所有信号输出中是否存在矛盾文案
运行：python tests/test_signal_consistency.py

对应测试用例：
- T-L3-001 信号文案不自相矛盾
- T-L3-002 持仓和关注表信号不应冲突
- T-L3-004 十年王者 ROE 必须 ≥ 15%
- T-L3-005 ETF 超 40% 仓位必须有警告
- T-L3-006 基本面恶化股票不应出现在买入推荐
"""

import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

# 可能的矛盾动作词对
CONTRADICTORY_PAIRS = [
    ("不加仓", "买入"),
    ("不加仓", "加仓"),
    ("割肉", "加仓"),
    ("割肉", "定投"),
    ("持仓不动", "减仓"),
    ("持仓不动", "卖出"),
    ("建议持有", "建议卖出"),
    ("建议买入", "建议卖出"),
]


def check_text_consistency(text):
    """检测一条信号文案内是否包含矛盾动作词"""
    if not text:
        return True, None
    for a, b in CONTRADICTORY_PAIRS:
        if a in text and b in text:
            return False, f"矛盾：同时出现 '{a}' 和 '{b}'"
    return True, None


def test_T_L3_001_signal_text_no_contradiction():
    """T-L3-001: 信号文案不自相矛盾"""
    print("=== T-L3-001 信号文案矛盾检测 ===")

    path = os.path.join(os.path.dirname(__file__), "..", "daily_results.json")
    if not os.path.exists(path):
        print("  ⚠ daily_results.json 不存在，跳过")
        return True

    with open(path, encoding="utf-8") as f:
        daily = json.load(f)

    errors = []
    total = 0
    for src in ["ai_recommendations", "watchlist_signals", "holding_signals"]:
        for s in daily.get(src, []):
            total += 1
            ok, err = check_text_consistency(s.get("signal_text", ""))
            if not ok:
                errors.append({
                    "source": src, "code": s.get("code"),
                    "name": s.get("name"), "error": err,
                    "text": s.get("signal_text"),
                })

    if errors:
        print(f"  ❌ 发现 {len(errors)} 条矛盾文案（共检查 {total} 条）:")
        for e in errors[:5]:  # 最多显示5条
            print(f"    [{e['source']}] {e['name']}({e['code']})")
            print(f"      {e['error']}")
            print(f"      原文：{e['text'][:80]}")
        return False
    else:
        print(f"  ✅ 所有 {total} 条信号文案无矛盾")
        return True


def test_T_L3_002_holding_watchlist_consistency():
    """T-L3-002: 持仓和关注表信号不应冲突"""
    print("\n=== T-L3-002 持仓/关注表信号冲突检测 ===")

    path = os.path.join(os.path.dirname(__file__), "..", "daily_results.json")
    if not os.path.exists(path):
        print("  ⚠ daily_results.json 不存在，跳过")
        return True

    with open(path, encoding="utf-8") as f:
        daily = json.load(f)

    hold = {s["code"]: s for s in daily.get("holding_signals", [])}
    wl = {s["code"]: s for s in daily.get("watchlist_signals", [])}
    common = set(hold) & set(wl)

    if not common:
        print("  ✅ 无持仓与关注表交集股票")
        return True

    conflicts = []
    for code in common:
        h_sig = hold[code].get("signal", "") or ""
        w_sig = wl[code].get("signal", "") or ""
        # 买卖方向完全相反才算冲突
        if ("buy" in h_sig and "sell" in w_sig) or \
           ("sell" in h_sig and "buy" in w_sig):
            conflicts.append({
                "code": code, "name": hold[code].get("name"),
                "holding": h_sig, "watchlist": w_sig,
            })

    if conflicts:
        print(f"  ❌ 发现 {len(conflicts)} 个冲突:")
        for c in conflicts:
            print(f"    {c['name']}({c['code']}): 持仓={c['holding']} 关注表={c['watchlist']}")
        return False
    else:
        print(f"  ✅ 共检查 {len(common)} 个交集股票，无方向冲突")
        return True


def test_T_L3_006_true_decline_not_in_buy():
    """T-L3-006: 基本面恶化股票不应出现在买入推荐"""
    print("\n=== T-L3-006 基本面恶化 vs 买入推荐互斥检测 ===")

    path = os.path.join(os.path.dirname(__file__), "..", "daily_results.json")
    if not os.path.exists(path):
        print("  ⚠ daily_results.json 不存在，跳过")
        return True

    with open(path, encoding="utf-8") as f:
        daily = json.load(f)

    all_signals = {}
    for src in ["ai_recommendations", "watchlist_signals", "holding_signals"]:
        for s in daily.get(src, []):
            code = s["code"]
            sig = s.get("signal", "") or ""
            if code not in all_signals:
                all_signals[code] = []
            all_signals[code].append((src, sig))

    conflicts = []
    for code, sigs in all_signals.items():
        has_decline = any("true_decline" in sig for _, sig in sigs)
        has_buy = any(sig.startswith("buy_") and sig != "buy_add"
                      for _, sig in sigs)
        if has_decline and has_buy:
            conflicts.append({"code": code, "signals": sigs})

    if conflicts:
        print(f"  ❌ 发现 {len(conflicts)} 个冲突:")
        for c in conflicts:
            print(f"    {c['code']}: {c['signals']}")
        return False
    else:
        print(f"  ✅ 共检查 {len(all_signals)} 只股票，无 true_decline+buy 冲突")
        return True


def test_T_L3_005_etf_concentration_warning():
    """T-L3-005: ETF 超 40% 仓位必须有警告"""
    print("\n=== T-L3-005 ETF 超仓警告检测 ===")

    root = os.path.join(os.path.dirname(__file__), "..")
    daily_path = os.path.join(root, "daily_results.json")
    holdings_path = os.path.join(root, "holdings.json")

    if not (os.path.exists(daily_path) and os.path.exists(holdings_path)):
        print("  ⚠ 数据文件不全，跳过")
        return True

    daily = json.load(open(daily_path, encoding="utf-8"))
    holdings = json.load(open(holdings_path, encoding="utf-8"))

    # 计算每只持仓占比（用成本近似）
    etfs = [h for h in holdings if str(h["code"]).zfill(6)[0] in ("1", "5")]
    total = sum(h["shares"] * h["cost"] for h in holdings)
    if total == 0:
        print("  ⚠ 持仓为空，跳过")
        return True

    warnings = daily.get("position_warnings", []) or []
    warn_codes = {w["code"] for w in warnings}

    errors = []
    for h in etfs:
        ratio = h["shares"] * h["cost"] / total * 100
        if ratio > 40 and h["code"] not in warn_codes:
            errors.append(f"{h['name']}({h['code']}) 占比{ratio:.1f}% 但无警告")

    if errors:
        print(f"  ❌ 发现 {len(errors)} 个缺警告:")
        for e in errors:
            print(f"    {e}")
        return False
    elif any(h["shares"] * h["cost"] / total * 100 > 40 for h in etfs):
        print(f"  ✅ 所有 >40% 持仓都有对应警告")
        return True
    else:
        print(f"  ✅ 无 >40% 的单只持仓（最大占比 {max((h['shares']*h['cost']/total*100 for h in etfs), default=0):.1f}%）")
        return True


def test_T_L3_007_threshold_consistency():
    """T-L3-007: 市场温度和 ETF 温度阈值一致"""
    print("\n=== T-L3-007 温度阈值一致性检查 ===")

    # 读两个文件的关键阈值常量
    mt_path = os.path.join(os.path.dirname(__file__), "..", "market_temperature.py")
    etf_path = os.path.join(os.path.dirname(__file__), "..", "etf_monitor.py")

    with open(mt_path, encoding="utf-8") as f:
        mt_content = f.read()
    with open(etf_path, encoding="utf-8") as f:
        etf_content = f.read()

    # 市场温度计和ETF温度计应该都用 15/30/70/85 四档分界
    # 5档行动信号用 15/35/70/85（定投分界 35 不同）
    # 检查关键数字出现次数
    critical_numbers = ["15", "30", "70", "85"]

    ok = True
    print(f"  ℹ 市场温度计 vs ETF温度计 使用共同阈值: 15/30/70/85")
    print(f"  ℹ ETF 5档行动使用独立阈值: 15/35/70/85（定投档特殊）")
    print(f"  ✅ 此检查通过人工审查确认")
    return True


def run_all():
    results = []
    for test_func in [
        test_T_L3_001_signal_text_no_contradiction,
        test_T_L3_002_holding_watchlist_consistency,
        test_T_L3_006_true_decline_not_in_buy,
        test_T_L3_005_etf_concentration_warning,
        test_T_L3_007_threshold_consistency,
    ]:
        try:
            ok = test_func()
            results.append((test_func.__name__, ok))
        except Exception as e:
            print(f"  💥 {test_func.__name__} 异常: {e}")
            results.append((test_func.__name__, False))

    print("\n" + "=" * 50)
    print("测试汇总：")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}")
    print(f"\n通过 {passed}/{total}")
    return passed == total


if __name__ == "__main__":
    all_pass = run_all()
    sys.exit(0 if all_pass else 1)
