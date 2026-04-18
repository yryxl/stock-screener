"""
REQ-188 / TODO-015 目标价 + 追踪止损（2026-04-17）

设计原则（适配价值投资者）：
  - 追踪止损不是"建议卖"，是"提醒复查基本面"
  - 大回撤本身不可怕（茅台 2021-2022 跌 50% 后反弹），可怕的是"基本面同时恶化"
  - 巴菲特/芒格从不用追踪止损 → 我们做得更克制

两个规则：
  1. 目标价跟踪：当前价 vs 用户买入时记录的"合理价"对比
  2. 大回撤复查：从历史最高回撤 >20% 时，提醒"复查基本面是否恶化"

数据字段（holdings.json 新增）：
  - target_price: 买入时记录的合理价（用户手填，可选）
  - peak_price: 持有期间最高价（系统每日自动更新，初始 = cost）
"""

import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def update_peak_prices(holdings, current_prices):
    """
    更新每只持仓的历史最高价（peak_price）

    输入：
      holdings: [{code, ...}, ...]
      current_prices: {code: current_price}
    返回：(modified_holdings, changed)
      modified_holdings: 更新后的列表
      changed: True 表示有改动需要保存
    """
    changed = False
    for h in holdings:
        code = str(h.get('code', '')).zfill(6)
        cur = current_prices.get(code)
        if not cur or cur <= 0:
            continue
        old_peak = h.get('peak_price')
        cost = h.get('cost', 0) or 0
        # 初始化：peak_price = max(当前价, 成本)
        if old_peak is None:
            h['peak_price'] = max(cur, cost)
            changed = True
        elif cur > old_peak:
            h['peak_price'] = cur
            changed = True
    return holdings, changed


def calc_position_metrics(holding, current_price):
    """
    计算单只持仓的"目标价 + 追踪止损"指标

    输入：
      holding: {code, name, shares, cost, target_price?, peak_price?}
      current_price: 当前价
    返回：dict {
      'cost', 'current_price', 'target_price', 'peak_price',
      'pnl_pct': 当前浮盈%,
      'vs_target_pct': 当前价相对目标价 %（<0=有安全边际，>0=已透支）,
      'drawdown_from_peak_pct': 从历史最高的回撤%,
      'drawdown_alert': bool（>20% 触发复查提醒）,
      'target_status': 'below' / 'near' / 'above' / 'unknown',
      'target_message': 文案,
      'drawdown_message': 文案,
    }
    """
    cost = holding.get('cost', 0) or 0
    target = holding.get('target_price')
    peak = holding.get('peak_price') or max(current_price or cost, cost)

    if not current_price or current_price <= 0:
        return None

    result = {
        'cost': cost,
        'current_price': current_price,
        'target_price': target,
        'peak_price': peak,
        'pnl_pct': round((current_price / cost - 1) * 100, 1) if cost > 0 else None,
    }

    # 目标价对比
    if target and target > 0:
        vs_target = (current_price / target - 1) * 100
        result['vs_target_pct'] = round(vs_target, 1)
        if vs_target < -5:
            result['target_status'] = 'below'
            result['target_message'] = (
                f'低于合理价 {abs(vs_target):.1f}%，有安全边际可加仓'
            )
        elif vs_target < 5:
            result['target_status'] = 'near'
            result['target_message'] = '接近合理价（±5%），观望'
        elif vs_target < 20:
            result['target_status'] = 'above'
            result['target_message'] = (
                f'高于合理价 {vs_target:.1f}%，已透支安全边际'
            )
        else:
            result['target_status'] = 'far_above'
            result['target_message'] = (
                f'⚠ 高于合理价 {vs_target:.1f}%，严重透支，建议复查'
            )
    else:
        result['vs_target_pct'] = None
        result['target_status'] = 'unknown'
        result['target_message'] = '未设置目标价（请编辑持仓填写）'

    # 历史最高回撤
    if peak and peak > 0:
        drawdown = (peak - current_price) / peak * 100
        result['drawdown_from_peak_pct'] = round(drawdown, 1)
        # >20% 触发"复查基本面"提醒（不是"建议卖"）
        if drawdown > 30:
            result['drawdown_alert'] = True
            result['drawdown_message'] = (
                f'🚨 从历史最高 ¥{peak:.2f} 回撤 {drawdown:.1f}%。'
                f'必须复查基本面：是真错（卖出）还是假错（坚守）。'
                f'参考：基本面健康 + 大回撤 = 加仓机会；基本面恶化 + 大回撤 = 真要卖'
            )
        elif drawdown > 20:
            result['drawdown_alert'] = True
            result['drawdown_message'] = (
                f'⚠ 从历史最高 ¥{peak:.2f} 回撤 {drawdown:.1f}%。'
                f'建议复查基本面是否恶化。注意：好公司必经几次 20% 回撤'
                f'（茅台 2021-2022 跌 50% 后反弹）'
            )
        else:
            result['drawdown_alert'] = False
            result['drawdown_message'] = (
                f'回撤 {drawdown:.1f}%（<20% 正常波动）'
            )
    else:
        result['drawdown_from_peak_pct'] = None
        result['drawdown_alert'] = False
        result['drawdown_message'] = '历史最高价未记录'

    return result


def get_portfolio_drawdown_alerts(holdings, current_prices):
    """
    扫描组合，找出所有触发"大回撤复查"的持仓

    返回：[{code, name, drawdown_from_peak_pct, message}, ...]
    """
    alerts = []
    for h in holdings:
        code = str(h.get('code', '')).zfill(6)
        cur = current_prices.get(code)
        if not cur:
            continue
        m = calc_position_metrics(h, cur)
        if m and m.get('drawdown_alert'):
            alerts.append({
                'code': code,
                'name': h.get('name', code),
                'drawdown_from_peak_pct': m['drawdown_from_peak_pct'],
                'peak_price': m['peak_price'],
                'current_price': cur,
                'message': m['drawdown_message'],
            })
    return alerts


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    # 单元自测
    print("=== 测试 1: 完整字段持仓 ===")
    h = {'code': '600519', 'name': '茅台', 'shares': 100,
         'cost': 1500, 'target_price': 1700, 'peak_price': 2000}
    m = calc_position_metrics(h, 1400)  # 当前价 1400
    print(f'  当前 ¥{m["current_price"]} / 目标 ¥{m["target_price"]} / 历史最高 ¥{m["peak_price"]}')
    print(f'  浮盈 {m["pnl_pct"]}%, 相对目标 {m["vs_target_pct"]}%, 从最高回撤 {m["drawdown_from_peak_pct"]}%')
    print(f'  目标状态: {m["target_status"]} → {m["target_message"]}')
    print(f'  回撤提醒: {m["drawdown_alert"]} → {m["drawdown_message"]}')

    print()
    print("=== 测试 2: 缺 target_price 字段 ===")
    h2 = {'code': '510330', 'name': '沪深300etf', 'shares': 700, 'cost': 4.915}
    m2 = calc_position_metrics(h2, 4.5)
    print(f'  目标状态: {m2["target_status"]} → {m2["target_message"]}')
    print(f'  回撤: {m2["drawdown_from_peak_pct"]}% → {m2["drawdown_message"]}')

    print()
    print("=== 测试 3: 大回撤触发 ===")
    h3 = {'code': '000725', 'name': '京东方', 'shares': 1000,
          'cost': 5.0, 'peak_price': 7.0}
    m3 = calc_position_metrics(h3, 4.0)  # 从 7.0 跌到 4.0 = 回撤 43%
    print(f'  从最高 ¥{m3["peak_price"]} 跌到 ¥{m3["current_price"]} = 回撤 {m3["drawdown_from_peak_pct"]}%')
    print(f'  触发提醒: {m3["drawdown_alert"]}')
    print(f'  文案: {m3["drawdown_message"][:80]}...')

    print()
    print("=== 测试 4: 自动更新 peak_price ===")
    holdings = [
        {'code': '600519', 'name': '茅台', 'cost': 1500, 'peak_price': 2000},
        {'code': '510330', 'name': '沪深300etf', 'cost': 4.915},  # 无 peak_price
    ]
    prices = {'600519': 2100, '510330': 4.5}
    new_h, changed = update_peak_prices(holdings, prices)
    print(f'  changed = {changed} （应为 True）')
    print(f'  茅台 peak_price: {new_h[0]["peak_price"]} （应更新为 2100）')
    print(f'  沪深300etf peak_price: {new_h[1]["peak_price"]} （应初始化为 max(4.5, 4.915)=4.915）')
