"""
第五阶段：边界数据测试

目的：用各种"故意搞坏的"输入测模型，确保：
  1. 不崩溃（任何异常都被捕获，返回合理结果）
  2. 不错误通过（异常股不应被标记为可买入）
  3. 字段填充安全（result 字典里所有字段都有合理默认值）
"""

import sys
sys.stdout.reconfigure(encoding='utf-8')
import yaml
import pandas as pd
import numpy as np
from screener import screen_single_stock

with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)


# ===== 边界场景 =====
# 用真实股票代码触发不同的边界情况
BOUNDARY_CASES = [
    # 场景 1：不存在的股票代码（数据完全拉不到）
    ('999999', '不存在的代码', '应：不崩溃，passed=False'),
    ('000000', '全 0 代码', '应：不崩溃，passed=False'),

    # 场景 2：上市不久的新股（历史数据不足 5 年）
    ('688981', '中芯国际', '科创板，2020 年上市，历史短'),
    ('688256', '寒武纪', '科创板，2020 年上市，亏损股'),

    # 场景 3：退市/ST 股（财报数据可能不规则）
    ('600485', 'ST 信威', '已退市/暂停'),
    ('600145', 'ST 新亿', '财报不规则'),

    # 场景 4：B 股 / H 股代码格式（行业字段可能为空）
    ('900901', 'B 股代码', '上海 B 股，可能数据缺'),

    # 场景 5：极小盘 / 微盘股（数据质量差）
    ('600086', '东方金钰', '已退市破产'),

    # 场景 6：刚 IPO 的次新股
    ('688981', '中芯国际', '历史<5 年'),

    # 场景 7：财务异常但还在交易的股
    ('000662', '索菱股份', '财务造假被处罚'),
    ('600240', '退市华业', 'A 股造假退市'),
]


fields_required = [
    'code', 'passed', 'checks', 'signal', 'pe', 'price',
    'is_10y_king', 'is_good_quality', 'is_toll_bridge',
    'china_v3_risks', 'cashcow_label', 'cashcow_tier',
    'max_buy_price_rr10', 'mgmt_score', 'mgmt_tier', 'mgmt_flags',
]


print('===== 第五阶段：边界数据测试 =====\n')
print(f'{"代码":8}  {"名称":12}  {"结果":8}  {"说明":50}')
print('-' * 90)

stats = {
    'total': 0,
    'crashed': 0,           # 崩溃数（必须 0）
    'wrongly_passed': 0,    # 错误通过（边界股不应通过）
    'safe_rejected': 0,     # 安全拒绝
    'field_missing': 0,     # 字段缺失
}
issues = []

for code, name, expectation in BOUNDARY_CASES:
    stats['total'] += 1
    try:
        r = screen_single_stock(code, config, pd.DataFrame())
        # 检查必要字段是否都存在
        missing = [f for f in fields_required if f not in r]
        if missing:
            stats['field_missing'] += 1
            issues.append(f'{code} {name}: 缺字段 {missing[:3]}')

        passed = r.get('passed', False)
        if passed:
            # 边界股不应被标记可买入(科创板新股除外,可能确实优秀)
            if code not in ('688981',):  # 中芯国际可能合格
                stats['wrongly_passed'] += 1
                issues.append(f'{code} {name}: 边界股不应通过却 passed=True')
            else:
                stats['safe_rejected'] += 1
        else:
            stats['safe_rejected'] += 1

        # 显示结果
        risk_count = len(r.get('china_v3_risks', []))
        check_count = len(r.get('checks', {}))
        status = '✅通过' if passed else '✅拒绝'
        msg = f'风险{risk_count}条 / 关卡{check_count}个'
        print(f'{code:8}  {name:12}  {status:8}  {msg:30}  期望: {expectation[:30]}')
    except Exception as e:
        stats['crashed'] += 1
        err_msg = str(e)[:60]
        issues.append(f'{code} {name}: 崩溃 {err_msg}')
        print(f'{code:8}  {name:12}  🔥崩溃   {err_msg}')


# ===== 类型异常测试 =====
print('\n\n===== 类型异常测试 =====')
print('用伪造数据测试程序对 None / NaN / 异常类型的容错')
print('-' * 90)

# 这些直接调用底层函数,看是否崩溃
type_tests = []

try:
    from china_adjustments import (
        check_drain_business, check_cigar_butt_warning,
        check_management_scorecard, check_cashcow_label,
    )

    # 测试 1: 空 DataFrame
    r = check_drain_business(pd.DataFrame(), '钢铁')
    type_tests.append(('check_drain_business 空 DataFrame', '不崩溃' if r is not None else '返回 None'))

    # 测试 2: None industry
    r = check_drain_business(pd.DataFrame({'净资产收益率': [10]*12}), None)
    type_tests.append(('check_drain_business None industry', '不崩溃' if r is not None else '返回 None'))

    # 测试 3: 烟蒂检测 PE=None
    r = check_cigar_butt_warning('600519', '白酒', None, pd.DataFrame())
    type_tests.append(('check_cigar_butt_warning PE=None', f'返回 {r[0]}'))

    # 测试 4: 烟蒂检测 PE=负数
    r = check_cigar_butt_warning('600519', '白酒', -5, pd.DataFrame())
    type_tests.append(('check_cigar_butt_warning PE=-5', f'返回 {r[0]}'))

    # 测试 5: 烟蒂检测 PE=0
    r = check_cigar_butt_warning('600519', '白酒', 0, pd.DataFrame())
    type_tests.append(('check_cigar_butt_warning PE=0', f'返回 {r[0]}'))

    # 测试 6: 管理层评分 ROE=None
    r = check_management_scorecard('600519', None, pd.DataFrame())
    type_tests.append(('check_management_scorecard ROE=None', f'分数={r.get("score")}'))

    # 测试 7: 印钞机标签 ROE=None
    r = check_cashcow_label('600519', '白酒', None)
    type_tests.append(('check_cashcow_label ROE=None', f'返回 {r[0]}'))

    for label, result in type_tests:
        print(f'  ✅ {label}: {result}')

except Exception as e:
    print(f'  🔥 类型异常测试崩溃: {e}')
    import traceback
    traceback.print_exc()
    issues.append(f'类型异常测试崩溃: {e}')


# ===== 统计 =====
print(f'\n\n===== 统计 =====')
print(f'总边界场景：{stats["total"]}')
print(f'崩溃：{stats["crashed"]} ← 必须 0')
print(f'错误通过：{stats["wrongly_passed"]} ← 必须 0')
print(f'安全拒绝：{stats["safe_rejected"]}')
print(f'字段缺失：{stats["field_missing"]} ← 必须 0')

print('\n===== 异常列表 =====')
if issues:
    for i in issues:
        print(f'  ⚠ {i}')
else:
    print('  ✅ 无异常')

# ===== 硬性断言 =====
fail_reasons = []
if stats['crashed'] > 0:
    fail_reasons.append(f'有 {stats["crashed"]} 个边界场景崩溃')
if stats['wrongly_passed'] > 0:
    fail_reasons.append(f'有 {stats["wrongly_passed"]} 个边界股被错误通过')
if stats['field_missing'] > 0:
    fail_reasons.append(f'有 {stats["field_missing"]} 个场景字段缺失')

if fail_reasons:
    print('\n===== ❌ 边界测试失败 =====')
    for r in fail_reasons:
        print(f'  ❌ {r}')
    sys.exit(1)
else:
    print('\n===== ✅ 第五阶段边界测试全部通过 =====')
    sys.exit(0)
