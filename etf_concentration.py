"""
TODO-041 ETF 集中度真实性识别（2026-04-18）

设计依据：
  用户原话："虽然买的是宽基，但其实只有其中几支股票是盈利的，绝大部分是亏钱的，
            那其实也没实现宽基的目的"
  真实数据印证：
    - 纳指 100 七巨头占权重接近 50%（不是真宽基）
    - 沪深 300 前 10 大仅 22%（真宽基）
    - 上证 50 本身就 50 只，前 10 大 60% 属设计本意

判定标准：
  ✅ 真宽基：前 10 大 < 35%（沪深 300 / 中证 500 / 中证 1000）
  ⚠ 偏集中：35-50%（标普 500）
  🚨 名义宽基实际主题：> 50%（纳指 100 七巨头）
  例外：成份股 ≤100 只的"按设计就集中"（上证 50 / 恒生）/ 策略 ETF / 行业 ETF
"""

import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_concentration_data():
    """加载 ETF 集中度数据"""
    try:
        with open(os.path.join(SCRIPT_DIR, 'etf_concentration_data.json'), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def get_index_concentration(index_code):
    """
    取某指数的集中度数据

    输入：index_code（如 '000300', 'NASDAQ100', 'H30269'）
    返回：dict {
      'name', 'top10_weight_pct', 'verdict', 'label', 'warning'?,
      'severity': 'green'/'yellow'/'red'/'neutral'
    } 或 None
    """
    data = _load_concentration_data()
    if not data:
        return None

    info = data.get('indices', {}).get(index_code)
    if not info:
        return None

    verdict = info.get('verdict', '')
    severity_map = {
        'true_broad': 'green',
        'concentrated': 'yellow',
        'fake_broad': 'red',
        'by_design_concentrated': 'neutral',
        'strategy_etf': 'neutral',
        'sector_etf': 'neutral',
    }
    severity = severity_map.get(verdict, 'neutral')

    return {
        'index_code': index_code,
        'name': info.get('name'),
        'total_stocks': info.get('total_stocks'),
        'top10_weight_pct': info.get('top10_weight_pct'),
        'top1_stock': info.get('top1_stock'),
        'top1_weight_pct': info.get('top1_weight_pct'),
        'verdict': verdict,
        'label': info.get('label', ''),
        'warning': info.get('warning'),
        'severity': severity,
        'as_of': info.get('as_of'),
    }


def check_etf_concentration(etf_code, etf_index_map=None):
    """
    给定一只 ETF 代码，返回其集中度评估

    输入：
      etf_code: 如 '510330'（沪深 300 ETF）
      etf_index_map: 可选，etf_index_map.json 已加载的数据
    返回：dict 同 get_index_concentration，或 None
    """
    if etf_index_map is None:
        try:
            with open(os.path.join(SCRIPT_DIR, 'etf_index_map.json'), encoding='utf-8') as f:
                etf_index_map = json.load(f).get('map', {})
        except Exception:
            return None

    etf_info = etf_index_map.get(str(etf_code).zfill(6))
    if not etf_info:
        # 不在映射表里，可能是跨境 ETF，直接按代码查
        # 跨境 ETF 用特殊 key（NASDAQ100 / S&P500 等）
        if str(etf_code).zfill(6) == '513100' or str(etf_code).zfill(6) == '159632':
            return get_index_concentration('NASDAQ100')
        if str(etf_code).zfill(6) == '513500':
            return get_index_concentration('S&P500')
        if str(etf_code).zfill(6) in ('159920', '513660', '513900', '159740'):
            return get_index_concentration('HSI')
        return None

    index_code = etf_info.get('index')
    return get_index_concentration(index_code)


def check_holdings_etf_concentration(holdings):
    """
    扫描持仓中所有宽基/策略 ETF，返回集中度评估清单

    返回：[{etf_code, etf_name, ...集中度数据}, ...]
    """
    try:
        with open(os.path.join(SCRIPT_DIR, 'etf_index_map.json'), encoding='utf-8') as f:
            etf_index_map = json.load(f).get('map', {})
    except Exception:
        etf_index_map = {}

    results = []
    for h in (holdings or []):
        code = str(h.get('code', '')).zfill(6)
        # 只检查 ETF（5/1 开头）
        if not code.startswith(('5', '1')):
            continue
        info = check_etf_concentration(code, etf_index_map)
        if info:
            results.append({
                'etf_code': code,
                'etf_name': h.get('name', code),
                **info,
            })
    return results


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    print("=== 单 ETF 集中度查询 ===")
    cases = [
        '510330',  # 沪深 300
        '510500',  # 中证 500
        '510050',  # 上证 50
        '512890',  # 红利低波
        '513100',  # 纳指 ETF
        '159920',  # 恒生 ETF
    ]
    for code in cases:
        info = check_etf_concentration(code)
        if info:
            emoji = {'green': '🟢', 'yellow': '🟡', 'red': '🔴', 'neutral': '⚪'}[info['severity']]
            print(f"{emoji} {code} → {info['name']}: 前10大 {info['top10_weight_pct']}%")
            print(f"    {info['label']}")
            if info.get('warning'):
                print(f"    ⚠ {info['warning']}")
        else:
            print(f"❌ {code}: 数据缺失")

    print()
    print("=== 实际持仓集中度扫描 ===")
    with open('holdings.json', encoding='utf-8') as f:
        holdings = json.load(f)
    results = check_holdings_etf_concentration(holdings)
    for r in results:
        emoji = {'green': '🟢', 'yellow': '🟡', 'red': '🔴', 'neutral': '⚪'}[r['severity']]
        print(f"{emoji} {r['etf_code']} {r['etf_name']}: 跟踪 {r['name']} | 前10大 {r['top10_weight_pct']}%")
        print(f"    {r['label']}")
