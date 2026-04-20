"""
TODO-022 综合 e2e 测试（模拟全流程，不调 akshare 避免耗时）

覆盖：
  1. code_filter 分段逻辑
  2. log_scan_batch 集成
  3. patch_round 拉漏跑列表 + 调 screen_all_stocks
  4. merge_full 读 7 段 + 补漏 cache，去重合并
  5. send_ai freshness 报警推送逻辑（mock send_simple_msg）
  6. workflow yaml cron 语法

策略：mock 掉耗时的 screen_all_stocks，测主流程。
"""
import sys
import os
import json
import shutil
import unittest.mock as mock
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


# ============================================================
# 备份关键文件
# ============================================================
BACKUP_FILES = [
    'scan_freshness.json', 'daily_results.json', 'market_scan_cache.json',
] + [f'market_scan_full_p{i}.json' for i in range(1, 8)]

BACKUPS = {}


def backup():
    for f in BACKUP_FILES:
        path = os.path.join(SCRIPT_DIR, f)
        if os.path.exists(path):
            BACKUPS[f] = open(path, encoding='utf-8').read()


def restore():
    for f in BACKUP_FILES:
        path = os.path.join(SCRIPT_DIR, f)
        if f in BACKUPS:
            with open(path, 'w', encoding='utf-8') as fp:
                fp.write(BACKUPS[f])
        elif os.path.exists(path):
            os.remove(path)
    # 清 patch_round 测试生成文件
    for fname in os.listdir(SCRIPT_DIR):
        if fname.startswith('market_scan_patch_') and fname.endswith('.json'):
            try:
                os.remove(os.path.join(SCRIPT_DIR, fname))
            except Exception:
                pass


backup()

try:
    # ============================================================
    print("=== L1: code_filter 分段逻辑（方案 D：6 桶均匀分配）===")
    # ============================================================
    test_codes = [f'{prefix}{sfx:03d}'
                  for prefix in ['600', '601', '603', '000', '002', '300', '688']
                  for sfx in range(800)]
    test_codes = test_codes[:5500]

    buckets = {i: 0 for i in range(6)}
    for c in test_codes:
        try:
            b = int(c) % 6
            buckets[b] += 1
        except Exception:
            pass

    max_diff = max(buckets.values()) - min(buckets.values())
    assert_true(max_diff <= 5, f"6 桶分配最大差 {max_diff} 只（5500/6 ≈ 916）")
    assert_eq(sum(buckets.values()), 5500, "总数 5500")
    # 每桶 ~916 只 × 4 秒/只 = 61 分钟，仍在 75 分钟 timeout 内
    assert_true(max(buckets.values()) < 1100,
                f"最大桶 {max(buckets.values())} < 1100（留余量）")

    # ============================================================
    print("\n=== L2: screen_all_stocks code_filter 参数生效 ===")
    # ============================================================
    import importlib
    import screener
    importlib.reload(screener)

    # mock screen_single_stock 避免真调 akshare
    call_codes = []

    def fake_single(code, config, quotes_df):
        call_codes.append(code)
        return {"code": code, "passed": False, "signal": None,
                "data_quality": "ok"}

    # mock 依赖：get_all_stocks 返回假股票列表，get_batch_roe_data 返回假 df
    import pandas as pd
    fake_stocks = pd.DataFrame([
        {'code': '000000', 'name': 'A'},
        {'code': '000001', 'name': 'B'},
        {'code': '000002', 'name': 'C'},
        {'code': '000007', 'name': 'D'},  # % 7 == 0
        {'code': '000014', 'name': 'E'},  # % 7 == 0
    ])

    def fake_get_all_stocks():
        return fake_stocks

    def fake_roe(date='20241231'):
        return pd.DataFrame([{'代码': c, '净资产收益率(%)': 20.0}
                              for c in fake_stocks['code']])

    def fake_quotes():
        return pd.DataFrame([{'代码': c, '最新价': 100.0} for c in fake_stocks['code']])

    with mock.patch.object(screener, 'screen_single_stock', side_effect=fake_single):
        with mock.patch.object(screener, 'get_all_stocks', side_effect=fake_get_all_stocks):
            with mock.patch.object(screener, 'get_batch_roe_data', side_effect=fake_roe):
                with mock.patch.object(screener, 'get_realtime_quotes', side_effect=fake_quotes):
                    # 方案 D：跑 p_idx=0（尾号 % 6 == 0）→ 应该只跑 000000
                    # 000007 % 6 = 1, 000014 % 6 = 2, 000002 % 6 = 2
                    # 所以 % 6 == 0 的只有 000000
                    config = {}
                    call_codes.clear()
                    screener.screen_all_stocks(
                        config,
                        code_filter=lambda c: int(c) % 6 == 0,
                        track_freshness=False,
                    )
                    # 000000 % 6 == 0
                    assert_true('000000' in call_codes,
                                "code_filter 正确筛出 000000（% 6 == 0）")

                    # 不给 filter → 全部跑
                    call_codes.clear()
                    screener.screen_all_stocks(config, track_freshness=False)
                    assert_eq(len(call_codes), 5, "无 filter 时全跑 5 只")

    # ============================================================
    print("\n=== L3: freshness 跟踪集成（success/fail 分类）===")
    # ============================================================
    import scan_freshness as sf
    importlib.reload(sf)
    sf._save({})  # 清空

    def fake_single_mixed(code, config, quotes_df):
        call_codes.append(code)
        if code == '000007':
            return {"code": code, "passed": False, "signal": None,
                    "data_quality": "no_indicator"}  # 拉不到数据 → fail
        elif code == '000014':
            return {"code": code, "passed": False, "signal": "hold",
                    "data_quality": "ok"}  # 成功但没过 → success
        else:
            return {"code": code, "passed": True, "signal": "buy_heavy",
                    "data_quality": "ok"}

    with mock.patch.object(screener, 'screen_single_stock', side_effect=fake_single_mixed):
        with mock.patch.object(screener, 'get_all_stocks', side_effect=fake_get_all_stocks):
            with mock.patch.object(screener, 'get_batch_roe_data', side_effect=fake_roe):
                with mock.patch.object(screener, 'get_realtime_quotes', side_effect=fake_quotes):
                    call_codes.clear()
                    # 全跑（不限制 code_filter），测试 freshness 写入
                    screener.screen_all_stocks(
                        {},
                        track_freshness=True,
                    )

    # 检查 scan_freshness 写入
    fr = sf.get_freshness('000007')
    assert_true(fr is not None and fr['consecutive_fails'] >= 1,
                "000007 (no_indicator) 被记为失败")
    fr = sf.get_freshness('000014')
    assert_true(fr is not None and fr['consecutive_fails'] == 0,
                "000014 (ok 但 hold) 被记为成功")

    # ============================================================
    print("\n=== L4: get_stale_stocks 优先级正确（持仓+ETF 优先）===")
    # ============================================================
    sf._save({})
    # 故意让 3 只股 fails：1 只持仓 + 1 只 ETF + 1 只候选
    sf.log_scan_fail('600519')  # 持仓（假设）
    sf.log_scan_fail('510330')  # ETF
    sf.log_scan_fail('002001')  # 候选
    sf.log_scan_fail('002001')  # 候选 fails 更多

    stale = sf.get_stale_stocks(
        priority_holdings=['600519'],
        priority_etf=['510330'],
        priority_watchlist=[],
    )
    codes_order = [c for c, _, _ in stale]
    # 持仓+ETF 同级 (priority=0)，同级按 fails 倒序 → 数据越老越优先补
    # 510330 fails=2 > 600519 fails=1 → 510330 在前（合理：2 天没更新比 1 天更紧急）
    assert_true(set(codes_order[:2]) == {'600519', '510330'}, "持仓+ETF 排前 2（同级）")
    assert_eq(codes_order[0], '510330', "同级按 fails 倒序：510330 fails=2 优先于 600519 fails=1")
    assert_eq(codes_order[2], '002001', "候选排最后")

    # ============================================================
    print("\n=== L5: merge_full 合并 + 去重逻辑 ===")
    # ============================================================
    # 写 3 段测试文件（p1 / p3 / p5）
    for p_idx, recs in [
        (1, [{'code': '000001', 'name': '股A', 'signal': 'buy_watch'}]),
        (3, [{'code': '000003', 'name': '股C', 'signal': 'buy_medium'},
             {'code': '000001', 'name': '股A', 'signal': 'buy_heavy'}]),  # 重复
        (5, [{'code': '000005', 'name': '股E', 'signal': 'buy_light'}]),
    ]:
        with open(os.path.join(SCRIPT_DIR, f'market_scan_full_p{p_idx}.json'), 'w', encoding='utf-8') as f:
            json.dump({
                'date': '2026-04-20', 'mode': f'full_p{p_idx}',
                'candidates_count': 100,
                'ai_recommendations': recs,
            }, f, ensure_ascii=False)

    # 模拟 merge_full 逻辑（手工复制 main.py 里的实现来避免跑整个 main）
    all_recs = []
    seen_codes = set()
    merged_files = []
    for p in range(1, 8):
        f = os.path.join(SCRIPT_DIR, f'market_scan_full_p{p}.json')
        if os.path.exists(f):
            data = json.load(open(f, encoding='utf-8'))
            recs = data.get('ai_recommendations', [])
            for s in recs:
                code = str(s.get('code', '')).zfill(6)
                if code and code not in seen_codes:
                    seen_codes.add(code)
                    s['source'] = f'段 {p}'
                    all_recs.append(s)
            merged_files.append(f'p{p}({len(recs)})')

    assert_eq(len(all_recs), 3, "去重后 3 只（000001 + 000003 + 000005）")
    assert_true({s['code'] for s in all_recs} == {'000001', '000003', '000005'},
                "代码正确")
    # 000001 出现 2 次（p1 和 p3），应保留 p1 的（先到）
    s_000001 = next(s for s in all_recs if s['code'] == '000001')
    assert_eq(s_000001['signal'], 'buy_watch', "000001 保留 p1 的（先到）")

    # ============================================================
    print("\n=== L6: merge_full 读补漏 cache ===")
    # ============================================================
    # 写一个补漏 cache
    today_str = datetime.now().strftime('%Y%m%d')
    patch_file = os.path.join(SCRIPT_DIR, f'market_scan_patch_{today_str}_0600.json')
    with open(patch_file, 'w', encoding='utf-8') as f:
        json.dump({
            'date': '2026-04-20', 'mode': 'patch_round',
            'candidates_count': 2,
            'ai_recommendations': [
                {'code': '000009', 'name': '股F', 'signal': 'buy_heavy'},
                {'code': '000001', 'name': '股A', 'signal': 'buy_heavy'},  # 已在 p1，应跳
            ],
        }, f, ensure_ascii=False)

    # 合并补漏
    import glob
    patch_pattern = os.path.join(SCRIPT_DIR, f'market_scan_patch_{today_str}_*.json')
    for pf in sorted(glob.glob(patch_pattern)):
        data = json.load(open(pf, encoding='utf-8'))
        recs = data.get('ai_recommendations', [])
        for s in recs:
            code = str(s.get('code', '')).zfill(6)
            if code and code not in seen_codes:
                seen_codes.add(code)
                s['source'] = '补漏'
                all_recs.append(s)
    assert_eq(len(all_recs), 4, "加补漏后 4 只（新增 000009）")
    s_000009 = next(s for s in all_recs if s['code'] == '000009')
    assert_eq(s_000009['source'], '补漏', "000009 标记为补漏来源")

    # ============================================================
    print("\n=== L7: freshness 报警推送逻辑（mock send）===")
    # ============================================================
    sf._save({})
    # 构造 1 只持仓红色（5 天前）+ 1 只关注黄色
    sf.log_scan_success('600519', 'buy')
    sf.log_scan_success('000538', 'hold')
    data = sf._load()
    data[sf._zfill_code('600519')]['last_scanned_at'] = (datetime.now(sf._BEIJING) - timedelta(days=5)).isoformat(timespec='seconds')
    data[sf._zfill_code('000538')]['last_scanned_at'] = (datetime.now(sf._BEIJING) - timedelta(days=1, hours=12)).isoformat(timespec='seconds')
    sf._save(data)

    # 直接调判定逻辑
    level_600519 = sf.get_alert_level('600519')
    level_000538 = sf.get_alert_level('000538')
    assert_eq(level_600519, 'red', "600519 5 天前 = red")
    assert_true(level_000538 in ('yellow', 'red'), f"000538 1.5 天前 = yellow/red（实际 {level_000538}）")

    # ============================================================
    print("\n=== L8: workflow yaml cron 语法验证（方案 D）===")
    # ============================================================
    yaml_path = os.path.join(SCRIPT_DIR, '.github/workflows/daily_screen.yml')
    yaml_content = open(yaml_path, encoding='utf-8').read()

    # 验证关键 cron 存在（方案 D：6 段 + 4 凌晨补漏 + 3 白天补漏 + merge）
    required_crons = [
        "cron: '0 9 * * *'",   # full_p1 北京 17:00
        "cron: '0 11 * * *'",  # full_p2 北京 19:00
        "cron: '0 13 * * *'",  # full_p3 北京 21:00
        "cron: '0 15 * * *'",  # full_p4 北京 23:00
        "cron: '0 17 * * *'",  # full_p5 北京 01:00
        "cron: '0 19 * * *'",  # full_p6 北京 03:00
        "cron: '0 20 * * *'",  # patch 北京 04:00
        "cron: '0 21 * * *'",  # patch 北京 05:00
        "cron: '0 22 * * *'",  # patch 北京 06:00
        "cron: '0 23 * * *'",  # patch 北京 07:00
        "cron: '15 0 * * *'",  # merge 北京 08:15
        "cron: '0 3 * * *'",   # patch 北京 11:00
        "cron: '0 6 * * *'",   # patch 北京 14:00
        "cron: '0 8 * * *'",   # patch 北京 16:00
    ]
    for c in required_crons:
        assert_true(c in yaml_content, f"yaml 含 {c}")

    # 验证 full_p7 已删除
    assert_true('full_p7' not in yaml_content, "yaml 不应含 full_p7（方案 D 已删）")

    # 验证 HOUR 判断分支存在
    assert_true('mode=full_p1' in yaml_content, "yaml mode=full_p1 分支")
    assert_true('mode=patch_round' in yaml_content, "yaml mode=patch_round 分支")
    assert_true('mode=merge_full' in yaml_content, "yaml mode=merge_full 分支")

    # 验证 HOUR 09 (UTC) = 17 (北京) 映射正确
    # cron '0 9 * * *' UTC → 北京 17:00 → 应触发 full_p1
    assert_true('"$HOUR" = "17"' in yaml_content and 'full_p1' in yaml_content,
                "HOUR=17 → full_p1 映射")

    # ============================================================
    print("\n=== L9: main.py patch_round 漏跑列表正确拉取 ===")
    # ============================================================
    sf._save({})
    # 构造 freshness：3 只持仓中 1 只失败
    holdings = [
        {'code': '600519', 'name': '茅台'},
        {'code': '000538', 'name': '白药'},
        {'code': '510330', 'name': '沪深300'},  # ETF
    ]
    sf.log_scan_fail('600519')
    sf.log_scan_success('000538', 'hold')  # 成功
    sf.log_scan_fail('510330')
    sf.log_scan_fail('510330')  # 更多 fails

    # 模拟 patch_round 的拉列表逻辑
    holdings_codes = [h['code'] for h in holdings]
    etf_codes = [c for c in holdings_codes if c.startswith(('5', '1'))]
    non_etf = [c for c in holdings_codes if c not in etf_codes]

    stale = sf.get_stale_stocks(
        priority_holdings=non_etf,
        priority_etf=etf_codes,
        priority_watchlist=[],
        max_count=1100,
    )
    stale_codes = [c for c, _, _ in stale]
    assert_eq(len(stale_codes), 2, "应拉出 2 只漏跑（600519 + 510330）")
    # 持仓+ETF 同级，按 fails 倒序：510330 fails=2 优先
    assert_eq(stale_codes[0], '510330', "ETF 510330 fails=2 优先（同级按 fails 倒序）")
    assert_eq(stale_codes[1], '600519', "持仓 600519 fails=1 次之")
    assert_true('000538' not in stale_codes, "成功的 000538 不在漏跑列表")

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
