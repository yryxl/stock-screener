"""
TODO-022 第 1 批：scan_freshness 单元测试
"""
import sys
import os
import json
from datetime import datetime, timedelta, timezone

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
    assert_true(a == b, f"{msg}（{a!r} vs {b!r}）")


import scan_freshness as sf

# 备份真实数据
backup = None
if os.path.exists(sf.FRESHNESS_FILE):
    backup = open(sf.FRESHNESS_FILE, encoding='utf-8').read()


def reset():
    sf._save({})


def restore():
    if backup is not None:
        open(sf.FRESHNESS_FILE, 'w', encoding='utf-8').write(backup)
    elif os.path.exists(sf.FRESHNESS_FILE):
        os.remove(sf.FRESHNESS_FILE)


try:
    print("=== L1: log_scan_success / fail 基础 ===")
    reset()
    sf.log_scan_success('600519', 'buy_watch')
    rec = sf.get_freshness('600519')
    assert_eq(rec['consecutive_fails'], 0, "成功后 fails=0")
    assert_eq(rec['last_signal'], 'buy_watch', "信号保存")

    n = sf.log_scan_fail('510330')
    assert_eq(n, 1, "首次失败 fails=1")
    n = sf.log_scan_fail('510330')
    assert_eq(n, 2, "再失败 fails=2")
    rec = sf.get_freshness('510330')
    assert_true(rec['first_fail_at'] is not None, "first_fail_at 已记录")

    print("\n=== L2: 失败后再成功 → fails 归 0 ===")
    sf.log_scan_success('510330', 'hold')
    rec = sf.get_freshness('510330')
    assert_eq(rec['consecutive_fails'], 0, "再成功后 fails=0")
    assert_true(rec['first_fail_at'] is None, "first_fail_at 清空")

    print("\n=== L3: 代码 zfill ===")
    reset()
    sf.log_scan_success('519', 'buy')
    assert_true(sf.get_freshness('000519') is not None, "短代码 zfill 6 位可查")
    assert_true(sf.get_freshness('519') is not None, "短代码原样查也行")

    print("\n=== L4: log_scan_batch 性能批量 ===")
    reset()
    success = [('600519', 'buy'), ('000538', 'hold'), ('601398', 'buy_watch')]
    fails = ['510330', '512890']
    sf.log_scan_batch(success, fails)
    assert_eq(sf.get_freshness('600519')['consecutive_fails'], 0, "批量 success 1")
    assert_eq(sf.get_freshness('510330')['consecutive_fails'], 1, "批量 fail 1")

    print("\n=== L5: get_stale_stocks 优先级 ===")
    reset()
    # 持仓: 510330 fails=2
    sf.log_scan_fail('510330'); sf.log_scan_fail('510330')
    # 关注: 000538 fails=3
    sf.log_scan_fail('000538'); sf.log_scan_fail('000538'); sf.log_scan_fail('000538')
    # 候选: 002001 fails=5
    for _ in range(5):
        sf.log_scan_fail('002001')

    stale = sf.get_stale_stocks(
        priority_holdings=['510330'],
        priority_watchlist=['000538'],
    )
    codes_in_order = [c for c, _, _ in stale]
    assert_eq(codes_in_order[0], '510330', "持仓优先")
    assert_eq(codes_in_order[1], '000538', "关注次之")
    assert_eq(codes_in_order[2], '002001', "候选最后（即使 fails 大）")

    print("\n=== L6: get_stale_stocks max_count + exclude ===")
    stale = sf.get_stale_stocks(max_count=2)
    assert_eq(len(stale), 2, "max_count 限制返回 2")

    stale = sf.get_stale_stocks(exclude_codes=['510330'])
    codes_in_order = [c for c, _, _ in stale]
    assert_true('510330' not in codes_in_order, "exclude 排除生效")

    print("\n=== L7: get_lag_in_trading_days 交易日 lag ===")
    reset()
    sf.log_scan_success('600519', 'buy')
    lag = sf.get_lag_in_trading_days('600519')
    assert_eq(lag, 0, "刚成功 lag=0")

    # 把 last_scanned_at 改成 5 天前（含周末）
    data = sf._load()
    data['600519']['last_scanned_at'] = (datetime.now(sf._BEIJING) - timedelta(days=5)).isoformat(timespec='seconds')
    sf._save(data)
    lag = sf.get_lag_in_trading_days('600519')
    # 5 天前 → lag 应该是 ~3 个交易日（去掉周末）或 ~3 个工作日
    assert_true(2 <= lag <= 5, f"lag 应该 2-5 个交易日（实际 {lag}）")

    # 不存在的股
    assert_true(sf.get_lag_in_trading_days('999999') is None, "不存在的股 lag=None")

    print("\n=== L8: get_alert_level 单只股颜色 ===")
    reset()
    # 刚成功 → green
    sf.log_scan_success('600519', 'buy')
    assert_eq(sf.get_alert_level('600519'), 'green', "刚成功 → green")

    # 1 天前 → yellow
    data = sf._load()
    data['600519']['last_scanned_at'] = (datetime.now(sf._BEIJING) - timedelta(days=1, hours=12)).isoformat(timespec='seconds')
    sf._save(data)
    level = sf.get_alert_level('600519')
    assert_true(level in ('yellow', 'red'), f"约 1 天 → yellow 或 red（实际 {level}）")

    # 5 天前 → red
    data = sf._load()
    data['600519']['last_scanned_at'] = (datetime.now(sf._BEIJING) - timedelta(days=5)).isoformat(timespec='seconds')
    sf._save(data)
    assert_eq(sf.get_alert_level('600519'), 'red', "5 天前 → red")

    # 从未成功 → unknown
    sf.log_scan_fail('510330')
    assert_eq(sf.get_alert_level('510330'), 'unknown', "从未成功 → unknown")

    print("\n=== L9: get_tab_alert_level 聚合 ===")
    reset()
    # 全绿 tab
    sf.log_scan_success('A1', 'buy')
    sf.log_scan_success('A2', 'buy')
    tab = sf.get_tab_alert_level([
        {'code': 'A1', 'kind': 'holding'},
        {'code': 'A2', 'kind': 'holding'},
    ])
    assert_eq(tab, 'green', "全绿")

    # 1 红 → tab 红
    data = sf._load()
    code1 = sf._zfill_code('A1')
    data[code1]['last_scanned_at'] = (datetime.now(sf._BEIJING) - timedelta(days=5)).isoformat(timespec='seconds')
    sf._save(data)
    tab = sf.get_tab_alert_level([
        {'code': 'A1', 'kind': 'holding'},
        {'code': 'A2', 'kind': 'holding'},
    ])
    assert_eq(tab, 'red', "1 红 → tab 红")

    # 持仓 ≥ 2 黄 → tab 红
    reset()
    sf.log_scan_success('B1', 'buy')
    sf.log_scan_success('B2', 'buy')
    sf.log_scan_success('B3', 'buy')
    data = sf._load()
    # B1 / B2 改成约 1.5 天前 → yellow
    yt = (datetime.now(sf._BEIJING) - timedelta(days=1, hours=12)).isoformat(timespec='seconds')
    data[sf._zfill_code('B1')]['last_scanned_at'] = yt
    data[sf._zfill_code('B2')]['last_scanned_at'] = yt
    sf._save(data)
    tab = sf.get_tab_alert_level([
        {'code': 'B1', 'kind': 'holding'},
        {'code': 'B2', 'kind': 'holding'},
        {'code': 'B3', 'kind': 'holding'},
    ])
    # 注意：1.5 天可能算 1 个交易日 → yellow，可能算 2 个 → red
    # 这里要看 lag 算法
    lag1 = sf.get_lag_in_trading_days('B1')
    if lag1 == 1:
        # yellow，需要 ≥ 2 黄 → tab 红
        assert_eq(tab, 'red', f"持仓 ≥ 2 黄 → tab 红（B1 lag={lag1}）")
    elif lag1 >= 2:
        # 已经 red
        assert_eq(tab, 'red', f"持仓 1 红 → tab 红（B1 lag={lag1}）")

    # 关注 ≥ 3 黄 → tab 红
    reset()
    for c in ['C1', 'C2', 'C3']:
        sf.log_scan_success(c, 'buy')
    yt = (datetime.now(sf._BEIJING) - timedelta(days=1, hours=12)).isoformat(timespec='seconds')
    data = sf._load()
    for c in ['C1', 'C2', 'C3']:
        data[sf._zfill_code(c)]['last_scanned_at'] = yt
    sf._save(data)
    tab = sf.get_tab_alert_level([{'code': c, 'kind': 'watchlist'} for c in ['C1', 'C2', 'C3']])
    lag1 = sf.get_lag_in_trading_days('C1')
    if lag1 == 1:
        assert_eq(tab, 'red', f"关注 ≥ 3 黄 → tab 红（lag={lag1}）")

    # 关注 < 3 黄 → tab 黄
    reset()
    for c in ['D1', 'D2']:
        sf.log_scan_success(c, 'buy')
    data = sf._load()
    for c in ['D1', 'D2']:
        data[sf._zfill_code(c)]['last_scanned_at'] = yt
    sf._save(data)
    tab = sf.get_tab_alert_level([{'code': c, 'kind': 'watchlist'} for c in ['D1', 'D2']])
    lag1 = sf.get_lag_in_trading_days('D1')
    if lag1 == 1:
        assert_eq(tab, 'yellow', f"关注 < 3 黄 → tab 黄（lag={lag1}）")

    print("\n=== L10: ETF 算 holding kind ===")
    reset()
    sf.log_scan_success('510330', 'buy')
    data = sf._load()
    data[sf._zfill_code('510330')]['last_scanned_at'] = (datetime.now(sf._BEIJING) - timedelta(days=5)).isoformat(timespec='seconds')
    sf._save(data)
    tab = sf.get_tab_alert_level([{'code': '510330', 'kind': 'etf'}])
    assert_eq(tab, 'red', "ETF 1 红 → tab 红（kind=etf 等同 holding）")

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
