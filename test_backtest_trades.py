"""
B3 + B4 + B5：回测交易/温度/复购单元测试

策略：用 mock 替换 streamlit 的 session_state 和 UI 调用，
直接测试 backtest_page.py 的 virtual_buy / virtual_sell + 复购场景。

覆盖：
- B3 virtual_buy/sell 主流程（建仓/加仓/部分卖/全卖/平均成本）
- B4 卖出后复购（验证当前无冷却期，可隔月再买）
- B5 PE/温度跳变触发：should_skip_pe_sells / should_apply_hot_market_reduction
"""
import sys
import os
import unittest.mock as mock

sys.stdout.reconfigure(encoding='utf-8')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

PASSED = 0
FAILED = 0
DETAILS = []


def assert_true(cond, msg):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {msg}")
    else:
        FAILED += 1
        DETAILS.append(msg)
        print(f"  ❌ {msg}")


def assert_eq(a, b, msg):
    assert_true(a == b, f"{msg}（实际 {a!r} vs 期望 {b!r}）")


def assert_close(a, b, tol=0.01, msg=""):
    assert_true(abs(a - b) < tol, f"{msg}（实际 {a} vs 期望 ~{b}）")


# ============================================================
# Mock streamlit before importing backtest_page
# ============================================================
class MockSessionState(dict):
    def __getattr__(self, k):
        return self.get(k)
    def __setattr__(self, k, v):
        self[k] = v


_mock_st = mock.MagicMock()
_mock_st.session_state = MockSessionState()
_mock_st.error = lambda *a, **kw: None
_mock_st.warning = lambda *a, **kw: None
_mock_st.info = lambda *a, **kw: None
_mock_st.success = lambda *a, **kw: None
_mock_st.markdown = lambda *a, **kw: None
sys.modules['streamlit'] = _mock_st

# 导入 backtest_page
import backtest_page as bp
import backtest_engine as bte


def init_state(cash=1000000):
    """重置 mock session_state"""
    _mock_st.session_state.clear()
    _mock_st.session_state['bt_cash'] = float(cash)
    _mock_st.session_state['bt_holdings'] = []
    _mock_st.session_state['bt_trade_log'] = []
    _mock_st.session_state['bt_year'] = 2024
    _mock_st.session_state['bt_month'] = 6
    _mock_st.session_state['bt_moat_broken'] = {}


# ============================================================
# B5：温度跳变判定函数（纯函数测试，无需 mock）
# ============================================================
print("=== B5: should_skip_pe_sells_for_cold_market（5 档 × 4 mode = 20 组合）===")

modes = ['path_a', 'path_b', 'path_c', 'default']
temps = [-2, -1, 0, 1, 2]
expected_cold_skip = {
    ('path_a', -2): False, ('path_a', -1): False, ('path_a', 0): False,
    ('path_a', 1): False, ('path_a', 2): False,
    ('path_b', -2): True, ('path_b', -1): False, ('path_b', 0): False,
    ('path_b', 1): False, ('path_b', 2): False,
    ('path_c', -2): True, ('path_c', -1): False, ('path_c', 0): False,
    ('path_c', 1): False, ('path_c', 2): False,
    ('default', -2): False, ('default', -1): False, ('default', 0): False,
    ('default', 1): False, ('default', 2): False,
}
for m in modes:
    for t in temps:
        actual = bte.should_skip_pe_sells_for_cold_market(m, t)
        expected = expected_cold_skip[(m, t)]
        assert_eq(actual, expected, f"{m} + temp={t} 跳过 PE 卖出")

print("\n=== B5: should_apply_hot_market_reduction（极热减仓）===")
expected_hot_reduce = {
    ('path_a', 2): False,   # path_a 取消极热减仓
    ('path_b', 2): True,    # path_b 保留极热减仓
    ('path_c', 2): False,   # path_c 同 path_a
    ('default', 2): True,   # 默认行为：极热时减仓
    # 非极热（temp != 2）一律不触发
    ('path_a', 1): False, ('path_b', 1): False,
    ('path_c', 1): False, ('default', 1): False,
    ('path_a', 0): False, ('path_b', 0): False,
    ('path_c', 0): False, ('default', 0): False,
}
for (m, t), expected in expected_hot_reduce.items():
    actual = bte.should_apply_hot_market_reduction(m, t)
    assert_eq(actual, expected, f"{m} + temp={t} 极热减仓")


# ============================================================
# B3：virtual_buy 主流程
# ============================================================
print("\n=== B3.1 virtual_buy 首次建仓 ===")
init_state(cash=1000000)
# mock check_moat 永远返回护城河完好，避免依赖 raw 数据
with mock.patch.object(bp, 'check_moat', return_value=(True, [])):
    with mock.patch.object(bp, '_get_current_roe_for_sid', return_value=22):
        with mock.patch.object(bp, '_build_trade_context', return_value={}):
            ok = bp.virtual_buy('S01', 'A01', 100.0, 1000)
            assert_true(ok, "首次建仓成功")
            holdings = _mock_st.session_state['bt_holdings']
            assert_eq(len(holdings), 1, "1 个持仓")
            assert_eq(holdings[0]['shares'], 1000, "1000 股")
            assert_close(holdings[0]['cost'], 100.0, msg="成本 100")
            assert_close(_mock_st.session_state['bt_cash'], 900000, msg="现金 -100k")

print("\n=== B3.2 virtual_buy 加仓（更新加权成本）===")
# 接上：再买 500 股 @ 120
with mock.patch.object(bp, 'check_moat', return_value=(True, [])):
    with mock.patch.object(bp, '_get_current_roe_for_sid', return_value=20):
        with mock.patch.object(bp, '_build_trade_context', return_value={}):
            ok = bp.virtual_buy('S01', 'A01', 120.0, 500)
            assert_true(ok, "加仓成功")
            holdings = _mock_st.session_state['bt_holdings']
            assert_eq(len(holdings), 1, "仍 1 个持仓（合并）")
            assert_eq(holdings[0]['shares'], 1500, "1500 股")
            # 加权成本 = (1000*100 + 500*120) / 1500 = 160000/1500 = 106.67
            assert_close(holdings[0]['cost'], 106.67, tol=0.05, msg="加权成本")
            # roe_baseline = (22*1000 + 20*500)/1500 = 21.33
            assert_close(holdings[0]['roe_baseline'], 21.33, tol=0.05, msg="ROE 基准加权")

print("\n=== B3.3 virtual_buy 资金不足 ===")
init_state(cash=10000)
with mock.patch.object(bp, 'check_moat', return_value=(True, [])):
    with mock.patch.object(bp, '_get_current_roe_for_sid', return_value=22):
        with mock.patch.object(bp, '_build_trade_context', return_value={}):
            ok = bp.virtual_buy('S01', 'A01', 100.0, 1000)  # 需 100k > 10k
            assert_true(not ok, "资金不足拒绝")
            assert_eq(len(_mock_st.session_state['bt_holdings']), 0, "持仓为空")

print("\n=== B3.4 virtual_buy 100 股整数倍约束 ===")
init_state(cash=1000000)
with mock.patch.object(bp, 'check_moat', return_value=(True, [])):
    ok = bp.virtual_buy('S01', 'A01', 100.0, 50)  # < 100 股
    assert_true(not ok, "<100 股拒绝")
    ok = bp.virtual_buy('S01', 'A01', 100.0, 150)  # 非 100 整数倍
    assert_true(not ok, "非 100 整数倍拒绝")

print("\n=== B3.5 virtual_buy 护城河松动标签禁止 ===")
init_state(cash=1000000)
_mock_st.session_state['bt_moat_broken'] = {
    'S01': {'broken_at': '2023-06', 'problems': ['ROE 连续下滑']}
}
ok = bp.virtual_buy('S01', 'A01', 100.0, 1000)
assert_true(not ok, "有松动标签的禁止买")

print("\n=== B3.6 virtual_buy 当前护城河松动禁止 ===")
init_state(cash=1000000)
with mock.patch.object(bp, 'check_moat', return_value=(False, ['ROE 跌破 15%'])):
    ok = bp.virtual_buy('S01', 'A01', 100.0, 1000)
    assert_true(not ok, "当前护城河松动禁止买")


# ============================================================
# B3：virtual_sell 主流程
# ============================================================
print("\n=== B3.7 virtual_sell 部分卖出 ===")
init_state(cash=900000)
_mock_st.session_state['bt_holdings'] = [{
    'sid': 'S01', 'anon': 'A01', 'shares': 1000, 'cost': 100.0,
    'buy_date': '2024-01', 'add_dates': [],
    'roe_at_buy': 22, 'roe_baseline': 22,
}]
with mock.patch.object(bp, '_build_trade_context', return_value={}):
    ok = bp.virtual_sell('S01', 300, 150.0)
    assert_true(ok, "部分卖出成功")
    h = _mock_st.session_state['bt_holdings'][0]
    assert_eq(h['shares'], 700, "剩余 700 股")
    assert_close(_mock_st.session_state['bt_cash'], 945000, msg="现金 +45000")

print("\n=== B3.8 virtual_sell 卖太多拒绝 ===")
ok = bp.virtual_sell('S01', 999, 150.0)
assert_true(not ok, "卖太多拒绝")
assert_eq(_mock_st.session_state['bt_holdings'][0]['shares'], 700, "持仓未变")

print("\n=== B3.9 virtual_sell 全卖（清空持仓项）===")
init_state(cash=900000)
_mock_st.session_state['bt_holdings'] = [{
    'sid': 'S01', 'anon': 'A01', 'shares': 1000, 'cost': 100.0,
    'buy_date': '2024-01', 'add_dates': [],
    'roe_at_buy': 22, 'roe_baseline': 22,
}]
with mock.patch.object(bp, '_build_trade_context', return_value={}):
    ok = bp.virtual_sell('S01', 1000, 200.0)
    assert_true(ok, "全卖成功")
    # 实现：shares <= 0 时从 holdings.remove(h)
    assert_eq(len(_mock_st.session_state['bt_holdings']), 0, "持仓清空")
    assert_close(_mock_st.session_state['bt_cash'], 900000 + 200000,
                 msg="现金 +200k")


# ============================================================
# B4：止损/止盈后复购测试（验证当前无冷却期，可隔月再买）
# ============================================================
print("\n=== B4.1 卖出后立即复购（同月）允许 ===")
init_state(cash=1000000)
with mock.patch.object(bp, 'check_moat', return_value=(True, [])):
    with mock.patch.object(bp, '_get_current_roe_for_sid', return_value=22):
        with mock.patch.object(bp, '_build_trade_context', return_value={}):
            bp.virtual_buy('S01', 'A01', 100.0, 1000)
            assert_eq(len(_mock_st.session_state['bt_holdings']), 1, "建仓成功")
            bp.virtual_sell('S01', 1000, 90.0)  # 止损
            # 卖完会从 holdings 移除该项
            assert_eq(len(_mock_st.session_state['bt_holdings']), 0, "止损卖出后持仓清空")

            # 隔了"几秒"又买入（同月）→ 新建仓
            ok = bp.virtual_buy('S01', 'A01', 95.0, 1000)
            assert_true(ok, "止损后立即复购允许（无冷却期）")
            assert_eq(len(_mock_st.session_state['bt_holdings']), 1, "新建一个持仓项")
            assert_close(_mock_st.session_state['bt_holdings'][0]['cost'], 95.0,
                         tol=0.05, msg="复购后成本 95（新建仓）")

print("\n=== B4.2 卖出后跨月复购（B4 关键场景：当前实现允许）===")
init_state(cash=1000000)
with mock.patch.object(bp, 'check_moat', return_value=(True, [])):
    with mock.patch.object(bp, '_get_current_roe_for_sid', return_value=22):
        with mock.patch.object(bp, '_build_trade_context', return_value={}):
            bp.virtual_buy('S01', 'A01', 100.0, 1000)
            bp.virtual_sell('S01', 1000, 110.0)  # 小赚卖出
            # 跨月
            _mock_st.session_state['bt_month'] = 7
            ok = bp.virtual_buy('S01', 'A01', 105.0, 1000)
            assert_true(ok, "跨月复购允许")

print("\n=== B4.3 卖出后护城河松动登记 → 禁止复购 ===")
init_state(cash=1000000)
with mock.patch.object(bp, 'check_moat', return_value=(True, [])):
    with mock.patch.object(bp, '_get_current_roe_for_sid', return_value=22):
        with mock.patch.object(bp, '_build_trade_context', return_value={}):
            bp.virtual_buy('S01', 'A01', 100.0, 1000)
            bp.virtual_sell('S01', 1000, 80.0)
# 模拟卖出后松动登记
_mock_st.session_state['bt_moat_broken']['S01'] = {
    'broken_at': '2024-06', 'problems': ['基本面恶化']
}
ok = bp.virtual_buy('S01', 'A01', 80.0, 1000)
assert_true(not ok, "松动登记后禁止复购（与 B4 期望吻合 — 这是巴菲特原则的实现）")


# ============================================================
# 总结
# ============================================================
print("\n" + "=" * 60)
print(f"测试结果：通过 {PASSED} / 失败 {FAILED}")
print("=" * 60)
if FAILED:
    for d in DETAILS:
        print(f"  ❌ {d}")
    sys.exit(1)
print("✅ 全部通过")
