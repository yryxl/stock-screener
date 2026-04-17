"""
REQ-165 / TODO-005 跨境 ETF CAPE 透支警告（2026-04-17）

核心思想：
  Shiller CAPE 是 10 年滚动通胀调整 PE，比单年 PE 更能反映"贵不贵"。
  历史数据：当 CAPE > 30 时，未来 10 年股票实际回报通常 < 4%；
            当 CAPE > 40（如 1999 / 2021）后必有大跌。

警告级别：
  🔴 红：CAPE > 历史 90% 分位 → 透支严重，未来 10 年预期低回报
  🟡 黄：p70 < CAPE ≤ p90 → 偏贵，谨慎加仓
  🟢 绿：CAPE ≤ p70 → 合理估值

数据维护：
  cape_data.json 手动维护，建议每月更新一次（来源 multpl.com）
"""

import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_cape_data():
    """加载 CAPE 数据文件"""
    try:
        with open(os.path.join(SCRIPT_DIR, 'cape_data.json'), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def get_market_cape_status(market_key):
    """
    REQ-165：获取某市场的 CAPE 估值状态

    输入：market_key 如 'S&P500' / 'NASDAQ100' / 'HSI' / 'DAX'
    返回：dict {
      'market': 市场全名,
      'current_cape': 当前 CAPE 值,
      'status': 'green' / 'yellow' / 'red',
      'percentile_position': '历史 X% 分位',
      'forecast_return': '未来 10 年实际预期回报 X%',
      'message': 警告文案,
      'as_of': 数据日期,
    } 或 None（数据缺失）
    """
    data = _load_cape_data()
    if not data:
        return None

    market = data.get('markets', {}).get(market_key)
    if not market:
        return None

    cape = market.get('current_cape')
    hist = market.get('history', {})
    p70 = hist.get('p70')
    p90 = hist.get('p90')
    p_high = hist.get('all_time_high')

    if cape is None or p70 is None or p90 is None:
        return None

    # 估值状态
    if cape > p90:
        status = 'red'
        pct_label = f'> 历史 90% 分位（{p90}）'
        emoji = '🚨'
    elif cape > p70:
        status = 'yellow'
        pct_label = f'位于历史 70-90% 分位（{p70}-{p90}）'
        emoji = '⚠'
    else:
        status = 'green'
        pct_label = f'≤ 历史 70% 分位（{p70}）'
        emoji = '✅'

    # 距离历史最高
    if p_high and cape > 0:
        from_high_pct = cape / p_high * 100
        from_high_label = f'相当于历史最高（{p_high}）的 {from_high_pct:.0f}%'
    else:
        from_high_label = ''

    # Shiller 预测回报
    forecast = market.get('shiller_10y_forecast_real_return_pct')
    forecast_label = f'未来 10 年实际预期回报约 {forecast}%' if forecast is not None else ''

    # 主消息
    if status == 'red':
        message = (
            f"{emoji} CAPE 透支警告：{market.get('name')} 当前 CAPE = {cape}，"
            f"{pct_label}{'，' + from_high_label if from_high_label else ''}。"
            f"{forecast_label}。"
            f"芒格：'公道价买伟大企业 > 便宜价买平庸企业'，更适用于'极贵价买伟大企业 → 大概率跑输'"
        )
    elif status == 'yellow':
        message = (
            f"{emoji} {market.get('name')} CAPE = {cape}，{pct_label}。"
            f"{forecast_label}。建议谨慎加仓，等待回调。"
        )
    else:
        message = (
            f"{emoji} {market.get('name')} CAPE = {cape}，{pct_label}。估值合理。"
            f"{forecast_label}"
        )

    return {
        'market': market.get('name'),
        'market_key': market_key,
        'current_cape': cape,
        'status': status,
        'percentile_position': pct_label,
        'from_high_label': from_high_label,
        'forecast_return': forecast,
        'message': message,
        'as_of': market.get('as_of'),
    }


def check_cross_border_cape_alerts(holdings):
    """
    REQ-165：扫描持仓中的跨境 ETF，对每只发出 CAPE 警告

    输入：holdings: [{code, name, ...}, ...]
    返回：list of dict，每个 ETF 的 CAPE 状态
    """
    data = _load_cape_data()
    if not data:
        return []

    etf_to_market = data.get('etf_to_market_map', {})
    alerts = []
    for h in holdings:
        code = str(h.get('code', '')).zfill(6)
        market_key = etf_to_market.get(code)
        if not market_key:
            continue
        status = get_market_cape_status(market_key)
        if status:
            alerts.append({
                'etf_code': code,
                'etf_name': h.get('name', code),
                **status,
            })
    return alerts


def get_all_market_cape_summary():
    """
    返回所有市场的 CAPE 汇总（用于资产配置参考）

    返回：[{market, current_cape, status, message, ...}, ...]
    """
    data = _load_cape_data()
    if not data:
        return []

    return [get_market_cape_status(k) for k in data.get('markets', {}).keys()
            if get_market_cape_status(k) is not None]


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    print("=== 各市场 CAPE 现状 ===")
    for s in get_all_market_cape_summary():
        emoji = {'red': '🔴', 'yellow': '🟡', 'green': '🟢'}[s['status']]
        print(f"{emoji} {s['market']:20} CAPE={s['current_cape']:.1f} | "
              f"{s['percentile_position']} | 预期回报 {s['forecast_return']}%")

    print()
    print("=== 模拟持仓 CAPE 警告 ===")
    mock_holdings = [
        {'code': '513100', 'name': '纳指 ETF'},
        {'code': '159920', 'name': '恒生 ETF'},
        {'code': '510330', 'name': '沪深 300 ETF'},  # 不属跨境，不该出警告
    ]
    alerts = check_cross_border_cape_alerts(mock_holdings)
    for a in alerts:
        print(f"\n[{a['etf_code']} {a['etf_name']}]")
        print(f"  {a['message']}")
