"""
H5 transaction_log.py 全方位测试
"""
import sys
import os
import json
import shutil

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


# 备份
LOG_FILE = os.path.join(SCRIPT_DIR, 'transaction_log.json')
backup = None
if os.path.exists(LOG_FILE):
    backup = open(LOG_FILE, encoding='utf-8').read()


def restore():
    if backup is not None:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write(backup)
    elif os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)


def reset():
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump([], f)


try:
    import importlib
    import transaction_log
    importlib.reload(transaction_log)
    tl = transaction_log

    print("=== Layer 1: 基础写入 ===")
    reset()

    # 1.1 建仓
    ok, msg = tl.log_transaction('600519', '茅台', 'buy', 1500, 100,
                                  date='2023-05-15', fee=100, note='首次建仓')
    assert_true(ok, "建仓成功")
    h = tl.get_history('600519')
    assert_eq(len(h), 1, "1 条记录")
    assert_eq(h[0]['cash_change'], -150100, "现金变动 = -(1500*100 + 100) = -150100")

    # 1.2 增持
    ok, _ = tl.log_transaction('600519', '茅台', 'buy_add', 1400, 50, date='2023-09-20', fee=50)
    assert_true(ok, "增持成功")

    # 1.3 减持
    ok, _ = tl.log_transaction('600519', '茅台', 'sell_partial', 1700, 30, date='2024-03-10', fee=80)
    assert_true(ok, "减持成功")
    h = tl.get_history('600519')
    sell_rec = [r for r in h if r['action'] == 'sell_partial'][0]
    assert_close(sell_rec['cash_change'], 50920, msg="减持现金 = 1700*30 - 80 = 50920")

    # 1.4 分红再投
    ok, _ = tl.log_transaction('600519', '茅台', 'dividend', 1600, 5, date='2024-06-15')
    assert_true(ok, "分红再投成功")

    print("\n=== Layer 2: get_summary 计算 ===")
    s = tl.get_summary('600519', current_price=1450)

    # 持有数量：100 + 50 - 30 + 5 = 125
    assert_eq(s['shares_held'], 125, "持有 125 股")

    # 平均成本累加：
    # 建仓后：100 股，cost=150100，avg=1501
    # 增持后：150 股，cost=150100+70050=220150，avg=1467.67
    # 减持后：仍按 1467.67 算（成本不动，shares 减到 120）
    # 分红再投后：(1467.67*120 + 1600*5)/125 = (176120.4+8000)/125 = 1472.96
    assert_close(s['avg_cost'], 1472.96, tol=0.05, msg="平均成本")

    # 累计投入 = 150100 + 70050 + 8000 = 228150
    assert_close(s['total_invested'], 228150, tol=1, msg="累计投入")

    # 累计收回 = 50920
    assert_close(s['total_received'], 50920, tol=1, msg="累计收回")

    # 已实现盈亏 = 50920 - 1467.67*30 = 50920 - 44030.1 = 6889.9
    assert_close(s['realized_pnl'], 6890, tol=1, msg="已实现盈亏")

    # 浮盈 = (1450 - 1472.96) * 125 = -2870
    assert_close(s['unrealized_pnl'], -2870, tol=1, msg="浮盈")

    assert_eq(s['transaction_count'], 4, "4 笔交易")
    assert_eq(s['first_buy_date'], '2023-05-15', "首次买入")

    print("\n=== Layer 3: 多股隔离 ===")
    tl.log_transaction('000333', '美的', 'buy', 70, 1000, date='2024-01-15')
    s2 = tl.get_summary('000333', current_price=80)
    assert_eq(s2['shares_held'], 1000, "美的持有 1000 股")
    assert_close(s2['unrealized_pnl'], 10000, tol=1, msg="美的浮盈")

    # 茅台不受影响
    s_mt = tl.get_summary('600519', current_price=1450)
    assert_eq(s_mt['shares_held'], 125, "茅台仍 125 股")

    codes = tl.get_all_codes()
    assert_eq(len(codes), 2, "2 个股有交易记录")
    assert_true('600519' in codes and '000333' in codes, "代码集合正确")

    print("\n=== Layer 4: 清仓后重新建仓 ===")
    reset()
    tl.log_transaction('TEST01', '测试', 'buy', 100, 100, date='2023-01-01')
    tl.log_transaction('TEST01', '测试', 'sell_all', 150, 100, date='2023-06-01')
    s = tl.get_summary('TEST01')
    assert_eq(s['shares_held'], 0, "清仓后 0 股")
    assert_close(s['realized_pnl'], 5000, tol=1, msg="清仓盈亏 = (150-100)*100")
    assert_eq(s['avg_cost'], 0, "清仓后 avg_cost 重置")

    # 重新建仓，avg_cost 从新算
    tl.log_transaction('TEST01', '测试', 'buy', 200, 50, date='2023-09-01')
    s = tl.get_summary('TEST01')
    assert_eq(s['shares_held'], 50, "重新建仓 50 股")
    assert_close(s['avg_cost'], 200, msg="重新建仓 avg=200")

    print("\n=== Layer 5: 边界条件 ===")
    # 5.1 不存在 code
    s = tl.get_summary('NOTEXIST')
    assert_true(s is None, "不存在的 code 返回 None")

    # 5.2 异常 action
    ok, msg = tl.log_transaction('600519', '茅台', 'unknown', 100, 100)
    assert_true(not ok, "未知 action 拒绝")

    # 5.3 价格/数量 0
    ok, _ = tl.log_transaction('600519', '茅台', 'buy', 0, 100)
    assert_true(not ok, "价格 0 拒绝")
    ok, _ = tl.log_transaction('600519', '茅台', 'buy', 100, 0)
    assert_true(not ok, "数量 0 拒绝")

    # 5.4 代码 zfill
    ok, _ = tl.log_transaction('519', '测试', 'buy', 100, 100, date='2024-01-01')
    assert_true(ok, "短代码加入")
    h = tl.get_history('000519')  # 用补 0 后的代码查
    assert_true(len(h) >= 1, "短代码自动 zfill 6 位可查到")

    print("\n=== Layer 6: 删除记录 ===")
    reset()
    tl.log_transaction('600519', '茅台', 'buy', 1500, 100, date='2023-01-01')
    tl.log_transaction('600519', '茅台', 'buy_add', 1400, 50, date='2023-06-01')
    assert_eq(len(tl.get_history('600519')), 2, "2 条记录")

    ok, _ = tl.delete_transaction(0)  # 删第 1 条
    assert_true(ok, "删除成功")
    assert_eq(len(tl.get_history('600519')), 1, "剩 1 条")

    ok, _ = tl.delete_transaction(99)
    assert_true(not ok, "越界删除拒绝")

finally:
    restore()

print("\n" + "=" * 60)
print(f"测试结果：通过 {PASSED} / 失败 {FAILED}")
print("=" * 60)
if FAILED:
    for d in DETAILS:
        print(f"  ❌ {d}")
    sys.exit(1)
print("✅ 全部通过")
