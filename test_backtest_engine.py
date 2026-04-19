"""
B1：backtest_engine.py 核心评分函数单元测试

测试目标（mock 数据，不依赖 raw_S01.json）：
- _percentile_to_temperature：5 档温度计算（纯函数）
- _absolute_threshold_temperature：绝对阈值兜底（纯函数）
- check_10_year_king：4 个必要条件 + 数据不足边界
- get_buyback_score：分级加分（4 档）
- _roe_historical_avg：历史 ROE 均值
- _get_recent_roe / _get_recent_gm：最新值取出

验收标准：
- 全部纯函数边界覆盖
- check_10_year_king 4 个条件都能独立否决
- 测试 < 5 秒（不调真实数据接口）
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


import backtest_engine as bte


# ============================================================
# 1. _percentile_to_temperature
# ============================================================
print("=== 1. _percentile_to_temperature 5 档温度计算 ===")

# 1.1 样本不足返回 None
assert_eq(bte._percentile_to_temperature(50, []), None, "空历史返回 None")
assert_eq(bte._percentile_to_temperature(50, [40] * 35), None, "样本 35 < 36 返回 None")
assert_eq(bte._percentile_to_temperature(None, [40] * 50), None, "current None 返回 None")

# 1.2 5 档边界
hist = list(range(1, 101))  # 1~100
assert_eq(bte._percentile_to_temperature(86, hist), 2, "≥85% 返回 +2")
assert_eq(bte._percentile_to_temperature(75, hist), 1, "70-85% 返回 +1")
assert_eq(bte._percentile_to_temperature(50, hist), 0, "30-70% 返回 0")
assert_eq(bte._percentile_to_temperature(20, hist), -1, "15-30% 返回 -1")
assert_eq(bte._percentile_to_temperature(10, hist), -2, "<15% 返回 -2")


# ============================================================
# 2. _absolute_threshold_temperature
# ============================================================
print("\n=== 2. _absolute_threshold_temperature 绝对阈值兜底 ===")

# 2.1 PE 单独判定
assert_eq(bte._absolute_threshold_temperature(55, None), 2, "PE≥50 → +2")
assert_eq(bte._absolute_threshold_temperature(40, None), 1, "PE 35-50 → +1")
assert_eq(bte._absolute_threshold_temperature(25, None), 0, "PE 20-35 → 0")
assert_eq(bte._absolute_threshold_temperature(18, None), -1, "PE ≤20 → -1")
assert_eq(bte._absolute_threshold_temperature(12, None), -2, "PE ≤15 → -2")

# 2.2 PE+PB 综合
assert_eq(bte._absolute_threshold_temperature(55, 6), 2, "PE 极热+PB 极热 → +2")
assert_eq(bte._absolute_threshold_temperature(12, 1.5), -2, "PE 极冷+PB 极冷 → -2")
# PE 0 + PB +2 = 1（round 后 +1）
assert_eq(bte._absolute_threshold_temperature(25, 6), 1, "PE 中性 + PB 极热 → +1（平均）")

# 2.3 全 None
assert_eq(bte._absolute_threshold_temperature(None, None), None, "全 None 返回 None")


# ============================================================
# 3. check_10_year_king（4 必要条件）
# ============================================================
print("\n=== 3. check_10_year_king 十年王者 4 个必要条件 ===")

# helper：构造 N 年 ROE 序列（最新在前）
def mock_reports(roes):
    return [{'roe': r} for r in roes]


# 3.1 标准王者：10 年都 ≥ 18%
with mock.patch.object(bte, 'get_annual_reports_before',
                        return_value=mock_reports([22, 21, 20, 19, 18, 19, 20, 21, 22, 23])):
    is_king, avg, years_above = bte.check_10_year_king('S01', 2024, 12)
    assert_true(is_king, "10 年高 ROE → 是王者")
    assert_true(20 <= avg <= 22, f"均值 ~21 (实际 {avg:.1f})")
    assert_eq(years_above, 10, "10 年都 ≥15%")

# 3.2 数据不足（< 7 年）
with mock.patch.object(bte, 'get_annual_reports_before',
                        return_value=mock_reports([22, 21, 20, 19, 18, 19])):
    is_king, _, _ = bte.check_10_year_king('S02', 2024, 12)
    assert_true(not is_king, "<7 年数据 → 不是王者")

# 3.3 条件 1 否决：均值 < 15%
with mock.patch.object(bte, 'get_annual_reports_before',
                        return_value=mock_reports([12, 14, 13, 12, 14, 13, 14])):
    is_king, avg, _ = bte.check_10_year_king('S03', 2024, 12)
    assert_true(not is_king, "均值 13% < 15% → 不是王者")

# 3.4 条件 2 否决：偶尔冲高（10 年中只 5 年 ≥ 15%）
with mock.patch.object(bte, 'get_annual_reports_before',
                        return_value=mock_reports([25, 26, 22, 24, 23, 8, 10, 9, 12, 11])):
    is_king, avg, years_above = bte.check_10_year_king('S04', 2024, 12)
    # avg = (25+26+22+24+23+8+10+9+12+11)/10 = 17，过条件 1
    # 但 years_above = 5 < 7 → 否决
    assert_true(not is_king, "偶尔冲高（5 年 ≥15%）→ 不是王者")
    assert_eq(years_above, 5, "5 年 ≥15%")

# 3.5 条件 3 否决：王者已死（最近 2 年都 <10%）
with mock.patch.object(bte, 'get_annual_reports_before',
                        return_value=mock_reports([8, 9, 18, 20, 22, 21, 19, 18, 17, 16])):
    is_king, _, _ = bte.check_10_year_king('S05', 2024, 12)
    # 历史好但最近 2 年崩塌
    # 实际：avg=(8+9+18+20+22+21+19+18+17+16)/10=16.8 ≥15 ✅
    # years_above=8（除了 8、9 都 ≥15）✅ 通过条件 2
    # 条件 3：[0]=8<10 且 [1]=9<10 → 否决 ✅
    assert_true(not is_king, "最近 2 年都 <10% → 王者已死")

# 3.6 条件 4 否决：最新 1 年 ROE 为负
with mock.patch.object(bte, 'get_annual_reports_before',
                        return_value=mock_reports([-5, 18, 20, 22, 21, 19, 18, 17, 16, 15])):
    is_king, _, _ = bte.check_10_year_king('S06', 2024, 12)
    # avg = (-5+18+20+22+21+19+18+17+16+15)/10 = 16.1 ≥15 ✅
    # years_above = 9 ≥7 ✅
    # 条件 3：[0]=-5<10, [1]=18>10 → 不连续低于 10，不否决
    # 条件 4：最新 ROE 为负 → 否决
    assert_true(not is_king, "最新 1 年 ROE 负 → 否决")

# 3.7 临界：最近 2 年一年 9 一年 11（不连续 <10）
with mock.patch.object(bte, 'get_annual_reports_before',
                        return_value=mock_reports([9, 11, 18, 20, 22, 21, 19, 18, 17, 16])):
    is_king, _, _ = bte.check_10_year_king('S07', 2024, 12)
    # avg=(9+11+18+20+22+21+19+18+17+16)/10=17.1
    # years_above=8 ≥7
    # 条件 3：[0]=9<10 且 [1]=11>10 → 不连续低于 10 → 不否决
    # 条件 4：最新 ROE 9 ≥ 0 → 不否决
    assert_true(is_king, "最近一年低但前一年正常 → 仍是王者（条件 3 不连续）")


# ============================================================
# 4. get_buyback_score（4 档加分）
# ============================================================
print("\n=== 4. get_buyback_score 回购评分 ===")

# 4.1 无回购记录
with mock.patch.object(bte, '_load_buybacks', return_value={}):
    score, total = bte.get_buyback_score('S01', 2024, 12)
    assert_eq(score, 0, "无记录 → 0")
    assert_eq(total, 0, "总额 0")

# 4.2 ≥50 亿（高加分 +15）
mock_data = {'S01': [
    {'status': '完成', 'notice_date': '2023-06-01', 'amount': 60e8},
]}
with mock.patch.object(bte, '_load_buybacks', return_value=mock_data):
    score, total = bte.get_buyback_score('S01', 2024, 12)
    assert_eq(score, 15, "≥50 亿 → +15")
    assert_eq(total, 60.0, "总额 60 亿")

# 4.3 ≥10 亿（+8）
mock_data = {'S02': [
    {'status': '完成', 'notice_date': '2023-06-01', 'amount': 15e8},
]}
with mock.patch.object(bte, '_load_buybacks', return_value=mock_data):
    score, _ = bte.get_buyback_score('S02', 2024, 12)
    assert_eq(score, 8, "≥10 亿 → +8")

# 4.4 ≥1 亿（+3）
mock_data = {'S03': [
    {'status': '完成', 'notice_date': '2023-06-01', 'amount': 3e8},
]}
with mock.patch.object(bte, '_load_buybacks', return_value=mock_data):
    score, _ = bte.get_buyback_score('S03', 2024, 12)
    assert_eq(score, 3, "≥1 亿 → +3")

# 4.5 < 1 亿（0）
mock_data = {'S04': [
    {'status': '完成', 'notice_date': '2023-06-01', 'amount': 0.5e8},
]}
with mock.patch.object(bte, '_load_buybacks', return_value=mock_data):
    score, _ = bte.get_buyback_score('S04', 2024, 12)
    assert_eq(score, 0, "<1 亿 → 0")

# 4.6 多年累加（≥5 年内的所有完成回购）
mock_data = {'S05': [
    {'status': '完成', 'notice_date': '2020-03-01', 'amount': 5e8},   # 在 5 年窗口
    {'status': '完成', 'notice_date': '2022-06-01', 'amount': 8e8},
    {'status': '进行中', 'notice_date': '2023-12-01', 'amount': 100e8},  # 不算（未完成）
    {'status': '完成', 'notice_date': '2018-01-01', 'amount': 100e8},   # 出 5 年窗口
]}
with mock.patch.object(bte, '_load_buybacks', return_value=mock_data):
    score, total = bte.get_buyback_score('S05', 2024, 12, lookback_years=5)
    # 5 年内完成的：5 + 8 = 13 亿 → +8
    assert_eq(score, 8, "5 年累加 13 亿 → +8")
    assert_eq(total, 13.0, "总额 13 亿")

# 4.7 未来日期不算
mock_data = {'S06': [
    {'status': '完成', 'notice_date': '2025-06-01', 'amount': 100e8},  # 在 2024-12 之后
]}
with mock.patch.object(bte, '_load_buybacks', return_value=mock_data):
    score, total = bte.get_buyback_score('S06', 2024, 12)
    assert_eq(total, 0, "未来日期不计入")
    assert_eq(score, 0, "未来日期 → 0 分")


# ============================================================
# 5. _roe_historical_avg
# ============================================================
print("\n=== 5. _roe_historical_avg 历史 ROE 均值 ===")

with mock.patch.object(bte, 'get_annual_reports_before',
                        return_value=mock_reports([20, 18, 16, 22, 19, 21, 23])):
    avg = bte._roe_historical_avg('S01', 2024, 12, lookback_years=7)
    expected = sum([20, 18, 16, 22, 19, 21, 23]) / 7
    assert_true(abs(avg - expected) < 0.01, f"均值 {avg:.2f} 接近 {expected:.2f}")

# 数据不足
with mock.patch.object(bte, 'get_annual_reports_before',
                        return_value=mock_reports([20])):
    avg = bte._roe_historical_avg('S02', 2024, 12, lookback_years=7)
    assert_true(avg is None, "数据不足返回 None")

# 全 None
with mock.patch.object(bte, 'get_annual_reports_before',
                        return_value=[{'roe': None}, {'roe': None}]):
    avg = bte._roe_historical_avg('S03', 2024, 12)
    assert_true(avg is None, "全 None 返回 None")


# ============================================================
# 6. _get_recent_roe / _get_recent_gm
# ============================================================
print("\n=== 6. _get_recent_roe / _get_recent_gm ===")

with mock.patch.object(bte, 'get_annual_reports_before',
                        return_value=[{'roe': 22, 'gross_margin': 91},
                                      {'roe': 20, 'gross_margin': 90}]):
    roe = bte._get_recent_roe('S01', 2024, 12)
    gm = bte._get_recent_gm('S01', 2024, 12)
    assert_eq(roe, 22, "最新 ROE")
    assert_eq(gm, 91, "最新 毛利率")

# 空数据
with mock.patch.object(bte, 'get_annual_reports_before', return_value=[]):
    assert_true(bte._get_recent_roe('S02', 2024, 12) is None, "空数据 ROE None")
    assert_true(bte._get_recent_gm('S02', 2024, 12) is None, "空数据 GM None")


# ============================================================
# 7. B2：护城河松动转迁 check_moat_recovery
# ============================================================
print("\n=== 7. B2 护城河松动转迁判定（恢复条件 = 10年连续 ROE≥15%）===")

# 7.1 时间不够（松动后 9 年）
assert_eq(bte.check_moat_recovery(broken_year=2020, current_year=2028,
                                    post_break_roes=[20] * 10), False,
          "松动后 8 年（< 10）→ 不恢复")

# 7.2 时间够但 ROE 数据不足
assert_eq(bte.check_moat_recovery(broken_year=2020, current_year=2030,
                                    post_break_roes=[20, 22, 21, 19, 18]),
          False, "数据不足 5 年（< 10）→ 不恢复")

# 7.3 时间够 + 数据够 + 全 ≥15% → 恢复
assert_eq(bte.check_moat_recovery(broken_year=2020, current_year=2030,
                                    post_break_roes=[18, 20, 22, 21, 19, 17, 16, 18, 20, 22]),
          True, "10 年都 ≥15% → 恢复")

# 7.4 临界：恰好 10 年都 = 15%
assert_eq(bte.check_moat_recovery(broken_year=2020, current_year=2030,
                                    post_break_roes=[15] * 10), True,
          "10 年都 = 15% → 恢复（≥ 阈值）")

# 7.5 中间有 1 年 < 15% → 不恢复
assert_eq(bte.check_moat_recovery(broken_year=2020, current_year=2030,
                                    post_break_roes=[18, 20, 22, 14, 19, 17, 16, 18, 20, 22]),
          False, "10 年里有 1 年 = 14% → 不恢复")

# 7.6 第 1 年就 <15% → 不恢复
assert_eq(bte.check_moat_recovery(broken_year=2020, current_year=2030,
                                    post_break_roes=[10, 20, 22, 21, 19, 17, 16, 18, 20, 22]),
          False, "第 1 年 = 10% → 不恢复")

# 7.7 第 10 年 <15% → 不恢复
assert_eq(bte.check_moat_recovery(broken_year=2020, current_year=2030,
                                    post_break_roes=[18, 20, 22, 21, 19, 17, 16, 18, 20, 12]),
          False, "第 10 年 = 12% → 不恢复")

# 7.8 数据多（11 年），只看前 10 年
assert_eq(bte.check_moat_recovery(broken_year=2020, current_year=2031,
                                    post_break_roes=[18, 20, 22, 21, 19, 17, 16, 18, 20, 22, 5]),
          True, "11 年数据，只看前 10 年（都 ≥15）→ 恢复")

# 7.9 自定义阈值（threshold=20）
assert_eq(bte.check_moat_recovery(broken_year=2020, current_year=2030,
                                    post_break_roes=[18, 20, 22, 21, 19, 17, 16, 18, 20, 22],
                                    threshold=20),
          False, "阈值 20%，但有年份 18% → 不恢复")

# 7.10 自定义恢复年数（recovery_years=5）
assert_eq(bte.check_moat_recovery(broken_year=2020, current_year=2025,
                                    post_break_roes=[18, 20, 22, 21, 19],
                                    recovery_years=5),
          True, "recovery_years=5，5 年都 ≥15% → 恢复")

# 7.11 None ROE 被过滤
assert_eq(bte.check_moat_recovery(broken_year=2020, current_year=2030,
                                    post_break_roes=[18, None, 22, 21, 19, None, 16, 18, 20, 22]),
          False, "10 年序列含 None，过滤后只 8 年 → 不恢复")

# 7.12 边界：刚好 10 年时间，10 年数据全 ≥15%
assert_eq(bte.check_moat_recovery(broken_year=2020, current_year=2030,
                                    post_break_roes=[20] * 10), True,
          "刚好 10 年 + 全 ≥15% → 恢复（边界 OK）")


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
