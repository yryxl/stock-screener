"""
holdings_attribution 全面测试
"""
import sys
import os

sys.stdout.reconfigure(encoding='utf-8')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from holdings_attribution import (
    get_attribution, set_attribution,
    filter_model_only, summarize_attribution,
    auto_classify_by_buy_date, migrate_holdings,
    MODEL_LIVE_DATE, ATTRIBUTION_LABELS
)

PASSED = 0
FAILED = 0
DETAILS = []


def assert_eq(a, b, msg):
    global PASSED, FAILED
    if a == b:
        PASSED += 1
        print(f"  ✅ {msg}")
    else:
        FAILED += 1
        DETAILS.append(msg)
        print(f"  ❌ {msg}（{a!r} vs {b!r}）")


def assert_true(cond, msg):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {msg}")
    else:
        FAILED += 1
        DETAILS.append(msg)
        print(f"  ❌ {msg}")


# ============================================================
print("=== L1: get_attribution 默认 + 3 类 ===")
assert_eq(get_attribution({}), 'pre_model', "缺字段默认 pre_model")
assert_eq(get_attribution({'attribution': 'model'}), 'model', "model")
assert_eq(get_attribution({'attribution': 'pre_model'}), 'pre_model', "pre_model")
assert_eq(get_attribution({'attribution': 'manual'}), 'manual', "manual")
assert_eq(get_attribution({'attribution': 'invalid_xxx'}), 'pre_model', "无效值默认 pre_model")

print("\n=== L2: set_attribution + 备注 ===")
h = {'code': '600519'}
set_attribution(h, 'model', '模型推荐 buy_heavy')
assert_eq(h['attribution'], 'model', "set 成功")
assert_eq(h['attribution_note'], '模型推荐 buy_heavy', "备注保存")
try:
    set_attribution(h, 'invalid', '')
    assert_true(False, "无效归因应抛错")
except ValueError:
    assert_true(True, "无效归因抛错")

print("\n=== L3: filter_model_only ===")
test = [
    {'code': 'A', 'attribution': 'model'},
    {'code': 'B', 'attribution': 'pre_model'},
    {'code': 'C', 'attribution': 'manual'},
    {'code': 'D'},  # 缺字段 → pre_model
    {'code': 'E', 'attribution': 'model'},
]
only = filter_model_only(test)
assert_eq(len(only), 2, "只 2 只 model")
assert_eq([h['code'] for h in only], ['A', 'E'], "代码正确")

print("\n=== L4: summarize_attribution ===")
s = summarize_attribution(test)
assert_eq(s['model'], 2, "model 计数")
assert_eq(s['pre_model'], 2, "pre_model 计数（含缺字段）")
assert_eq(s['manual'], 1, "manual 计数")
assert_eq(s['total'], 5, "total")
assert_eq(s['model_pct'], 40.0, "model_pct")
assert_eq(s['attributed_count'], 2, "已计入数")
assert_eq(s['unattributed_count'], 3, "未计入数（pre_model + manual）")

print("\n=== L5: auto_classify_by_buy_date ===")
# buy_date < MODEL_LIVE_DATE → pre_model
assert_eq(auto_classify_by_buy_date({'buy_date': '2024-01-01'}), 'pre_model',
          "上线前买入 → pre_model")
# buy_date ≥ MODEL_LIVE_DATE，无 daily_signals → manual
assert_eq(auto_classify_by_buy_date({'code': 'X', 'buy_date': '2026-05-01'}), 'manual',
          "上线后买，无信号数据 → manual")
# 上线后买且有 buy 信号 → model
sigs = [{'code': '600519', 'signal': 'buy_heavy'}]
assert_eq(auto_classify_by_buy_date({'code': '600519', 'buy_date': '2026-05-01'}, sigs),
          'model', "上线后 + buy 信号 → model")
# 上线后买但 hold 信号 → manual
sigs2 = [{'code': '600519', 'signal': 'hold'}]
assert_eq(auto_classify_by_buy_date({'code': '600519', 'buy_date': '2026-05-01'}, sigs2),
          'manual', "上线后 + hold 信号 → manual")
# 缺 buy_date → pre_model
assert_eq(auto_classify_by_buy_date({'code': 'Y'}), 'pre_model', "缺 buy_date → pre_model")
# 上线日当天买入 → 不算 pre_model（>= 即 model 或 manual）
assert_eq(auto_classify_by_buy_date({'code': 'Z', 'buy_date': MODEL_LIVE_DATE}), 'manual',
          "上线日当天 + 无信号 → manual（不算 pre_model）")

print("\n=== L6: migrate_holdings dry_run + 实迁 ===")
test_holdings = [
    {'code': '000538', 'name': '云南白药'},
    {'code': '600519', 'attribution': 'model'},
    {'code': '510330', 'buy_date': '2024-10-01'},
]
# dry_run
import copy
test_dry = copy.deepcopy(test_holdings)
r = migrate_holdings(test_dry, dry_run=True)
assert_eq(r['migrated'], 2, "dry_run: 2 只待迁移")
assert_eq(r['already_set'], 1, "dry_run: 1 只已设")
assert_true('attribution' not in test_dry[0], "dry_run 不修改原数据")

# 实迁
r = migrate_holdings(test_holdings)
assert_eq(test_holdings[0].get('attribution'), 'pre_model', "云南白药迁为 pre_model")
assert_eq(test_holdings[1].get('attribution'), 'model', "茅台保持 model")
assert_eq(test_holdings[2].get('attribution'), 'pre_model', "沪深300 上线前 → pre_model")

print("\n=== L7: 极端：空持仓 ===")
assert_eq(filter_model_only([]), [], "空持仓 filter")
s = summarize_attribution([])
assert_eq(s['total'], 0, "空持仓 total=0")
assert_eq(s['model_pct'], 0, "空持仓 model_pct=0")

print("\n=== L8: 与 model_health_monitor 集成 ===")
# 实际场景：所有持仓都 pre_model → 模型成绩函数应返回明确的"无样本"
import json
import tempfile

# 模拟一个临时 holdings.json
tmp_holdings = [
    {'code': '510330', 'name': '沪深300etf', 'shares': 700, 'cost': 4.92, 'attribution': 'pre_model'},
    {'code': '000538', 'name': '云南白药', 'shares': 100, 'cost': 56.40, 'attribution': 'pre_model'},
]
tmp_path = os.path.join(SCRIPT_DIR, 'holdings_test_only.json')
with open(tmp_path, 'w', encoding='utf-8') as f:
    json.dump(tmp_holdings, f, ensure_ascii=False)
try:
    from model_health_monitor import calc_holding_win_rate, calc_max_drawdown_current
    r = calc_holding_win_rate(holdings_file=tmp_path)
    assert_true(r is not None, "win_rate 不为 None（含 attribution_summary）")
    assert_eq(r['rate'], None, "rate=None（无 model 持仓）")
    assert_true('attribution_summary' in r, "返回含 attribution_summary")
    assert_eq(r['attribution_summary']['model'], 0, "model 持仓数 0")

    r = calc_max_drawdown_current(holdings_file=tmp_path)
    assert_eq(r['worst_stock'], None, "drawdown 无样本")
finally:
    os.remove(tmp_path)


print("\n" + "=" * 60)
print(f"测试结果：通过 {PASSED} / 失败 {FAILED}")
print("=" * 60)
if FAILED:
    for d in DETAILS:
        print(f"  ❌ {d}")
    sys.exit(1)
print("✅ 全部通过")
