"""
REQ-150 资产配置健康度检查（TODO-033，2026-04-17）

目标：把持仓自动分到 6 类资产，对比目标比例（docs/ALLOCATION_STRATEGY.md），
      让用户在前端一眼看到"目标 vs 实际"偏差，不用拿计算器算。

6 类目标比例（来自 docs/ALLOCATION_STRATEGY.md）：
  40% 巴菲特式价值股（本模型筛选的个股 + 行业 ETF）
  20% 指数增强（沪深300 / 中证500 / 上证50 等宽基 ETF）
  20% 跨境资产（纳指 / 恒生 / 欧股 / 标普 ETF）
  10% 高股息防御（红利 ETF + 银行/公用事业个股）
  5% 黄金（黄金 ETF / 实物黄金）
  5% 现金（货币基金 / 国债 ETF）

警示阈值：
  偏差 > 10pp → 红色警告
  偏差 5-10pp → 黄色提示
  偏差 ≤ 5pp → 绿色健康
"""

import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ============================================================
# 6 类资产目标比例（与 docs/ALLOCATION_STRATEGY.md 对齐）
# ============================================================
ALLOCATION_TARGETS = {
    "buffett_value": {"label": "🎯 巴菲特式价值股", "target_pct": 40},
    "index_enhance": {"label": "📊 指数增强", "target_pct": 20},
    "cross_border":  {"label": "🌍 跨境资产", "target_pct": 20},
    "high_dividend": {"label": "💰 高股息防御", "target_pct": 10},
    "gold":          {"label": "🥇 黄金", "target_pct": 5},
    "cash":          {"label": "💵 现金", "target_pct": 5},
}


# ============================================================
# 跨境 ETF 名单（A 股市场上跟踪海外指数的 ETF）
# ============================================================
CROSS_BORDER_ETFS = {
    '513100': '纳指 ETF',
    '513500': '标普 500 ETF',
    '159632': '纳指 100 ETF',
    '513030': '德国 30 ETF',
    '513080': '法国 CAC40 ETF',
    '159920': '恒生 ETF',
    '513660': '恒生互联网 ETF',
    '159740': '恒生科技 ETF',
    '513900': '港股通精选 100 ETF',
    '159850': '香港中小 ETF',
    '518880': '黄金 ETF',  # 注：会被黄金分类拦截
}

# ============================================================
# 黄金 ETF 名单
# ============================================================
GOLD_ETFS = {
    '518880': '黄金 ETF (华安)',
    '518800': '黄金 ETF (国泰)',
    '159934': '黄金 ETF (易方达)',
    '159937': '黄金 ETF (博时)',
    '518660': '黄金 ETF (工银)',
    '518800_NOTE': '其他黄金 ETF',
}

# ============================================================
# 高股息行业个股关键词（行业字段含这些词 → 归高股息防御）
# ============================================================
HIGH_DIVIDEND_INDUSTRIES = [
    '银行', '保险', '电力', '公用事业', '高速公路', '铁路',
    '燃气', '水务', '港口', '机场',
]


# ============================================================
# 货币基金 / 国债 ETF（视为现金等价物）
# ============================================================
CASH_EQUIVALENT_ETFS = {
    '511880': '银华日利',  # 货币 ETF
    '511990': '华宝添益',  # 货币 ETF
    '511660': '建信添益',  # 货币 ETF
    '511010': '国债 ETF',
    '511260': '十年国债 ETF',
    '511020': '5 年地方债 ETF',
}


def classify_holding(code, name="", category=""):
    """
    把一只持仓分到 6 类资产之一

    返回：(asset_class, reason)
      asset_class: 'buffett_value' / 'index_enhance' / 'cross_border' /
                   'high_dividend' / 'gold' / 'cash'
      reason: 判定依据
    """
    if not code:
        return 'buffett_value', '无代码默认归价值股'
    code = str(code).zfill(6)
    name = (name or '').lower()

    # ========== 优先级 1：跨境资产 ==========
    # 必须在黄金/指数判定之前，因为某些代码可能交叉
    cross_border_keywords = ['纳指', '纳斯达克', 'nasdaq', 'qqq', '标普', 's&p', 'sp500',
                              '恒生', '港股', '香港', '德国', '法国', '欧股', '欧洲',
                              '日经', '日本', '海外', '全球', '美股']
    for kw in cross_border_keywords:
        if kw in name:
            return 'cross_border', f'名称含"{kw}"'
    if code in CROSS_BORDER_ETFS and code not in GOLD_ETFS:
        return 'cross_border', f'已知跨境 ETF（{CROSS_BORDER_ETFS[code]}）'

    # ========== 优先级 2：黄金 ==========
    if '黄金' in name or '白银' in name or 'gold' in name:
        return 'gold', f'名称含"黄金/白银"'
    if code in GOLD_ETFS:
        return 'gold', f'已知黄金 ETF'

    # ========== 优先级 3：现金等价物 ==========
    cash_keywords = ['货币', '日利', '添益', '国债', '短债']
    for kw in cash_keywords:
        if kw in name:
            return 'cash', f'名称含"{kw}"（现金等价物）'
    if code in CASH_EQUIVALENT_ETFS:
        return 'cash', f'已知货币基金/国债 ETF'

    # ========== 优先级 4：通过 ETF 映射表判定 ==========
    try:
        with open(os.path.join(SCRIPT_DIR, 'etf_index_map.json'), encoding='utf-8') as f:
            etf_map = json.load(f).get('map', {})
    except Exception:
        etf_map = {}

    if code in etf_map:
        kind = etf_map[code].get('kind', '')
        idx_name = etf_map[code].get('name', '')
        if kind == 'broad':
            return 'index_enhance', f'宽基 ETF（{idx_name}）'
        if kind == 'strategy_dividend':
            return 'high_dividend', f'红利策略 ETF（{idx_name}）'
        if kind == 'sector':
            # 行业 ETF（医药/消费/军工）归"巴菲特价值股"
            return 'buffett_value', f'行业 ETF（{idx_name}），归价值股池'

    # ========== 优先级 5：高股息行业个股 ==========
    cat = (category or '').lower()
    for kw in HIGH_DIVIDEND_INDUSTRIES:
        if kw in cat or kw in name:
            return 'high_dividend', f'高股息行业（{kw}）'

    # ========== 优先级 6：默认归巴菲特价值股 ==========
    # ETF 代码（5/1 开头）但未识别 → 归价值股池（保守处理）
    if code.startswith(('5', '1')):
        return 'buffett_value', f'未识别的 ETF/基金（{code}），暂归价值股池'

    # 个股（6/0/3/8 开头）→ 价值股
    return 'buffett_value', f'个股，归价值股池'


def calc_allocation_breakdown(holdings, cash_amount=0, current_prices=None):
    """
    计算实际配置 vs 目标配置

    输入：
      holdings: [{code, name, shares, cost, category?}, ...]
      cash_amount: 可投资现金（来自 user_cash.json）
      current_prices: {code: price}（来自 daily_results.json holding_signals）
                      不传则用 cost 估算

    返回：dict {
      'breakdown': [
        {asset_class, label, target_pct, actual_pct, deviation_pp,
         status, market_value, holdings: [...]},
        ...（6 类）
      ],
      'total_assets': 总资产,
      'max_deviation': 最大偏差（绝对值，用于综合状态）,
      'overall_status': 'green'/'yellow'/'red',
    }
    """
    if not holdings and cash_amount <= 0:
        return None

    current_prices = current_prices or {}

    # 初始化 6 类
    classes = {k: {'asset_class': k, 'label': v['label'],
                   'target_pct': v['target_pct'],
                   'market_value': 0.0, 'holdings': []}
               for k, v in ALLOCATION_TARGETS.items()}

    # 现金直接归类
    classes['cash']['market_value'] += cash_amount
    if cash_amount > 0:
        classes['cash']['holdings'].append({
            'code': '-', 'name': '可投资现金', 'value': cash_amount,
            'reason': 'user_cash.json'
        })

    # 持仓分类
    for h in holdings:
        code = str(h.get('code', '')).zfill(6)
        name = h.get('name', code)
        cat = h.get('category', '')
        shares = h.get('shares', 0) or 0
        cost = h.get('cost', 0) or 0
        # 优先用当前价
        price = current_prices.get(code, cost)
        market_value = shares * price

        cls, reason = classify_holding(code, name, cat)
        classes[cls]['market_value'] += market_value
        classes[cls]['holdings'].append({
            'code': code, 'name': name, 'value': market_value,
            'reason': reason,
        })

    total_assets = sum(c['market_value'] for c in classes.values())
    if total_assets <= 0:
        return None

    # 算实际占比 + 偏差
    breakdown = []
    max_deviation = 0
    for k in ['buffett_value', 'index_enhance', 'cross_border',
              'high_dividend', 'gold', 'cash']:
        c = classes[k]
        actual_pct = c['market_value'] / total_assets * 100
        deviation = actual_pct - c['target_pct']
        abs_dev = abs(deviation)
        if abs_dev > 10:
            status = 'red'
        elif abs_dev > 5:
            status = 'yellow'
        else:
            status = 'green'
        max_deviation = max(max_deviation, abs_dev)
        breakdown.append({
            'asset_class': k,
            'label': c['label'],
            'target_pct': c['target_pct'],
            'actual_pct': round(actual_pct, 1),
            'deviation_pp': round(deviation, 1),
            'status': status,
            'market_value': round(c['market_value'], 0),
            'holdings_count': len(c['holdings']),
            'holdings': c['holdings'],
        })

    # 综合状态：任一类红 → 红；否则任一黄 → 黄；都绿 → 绿
    if any(b['status'] == 'red' for b in breakdown):
        overall = 'red'
    elif any(b['status'] == 'yellow' for b in breakdown):
        overall = 'yellow'
    else:
        overall = 'green'

    return {
        'breakdown': breakdown,
        'total_assets': round(total_assets, 0),
        'max_deviation': round(max_deviation, 1),
        'overall_status': overall,
    }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    # 简单 self-test
    print("=== 分类测试 ===")
    cases = [
        ('510330', '沪深300etf', None, 'index_enhance'),
        ('510500', '中证500etf南方', None, 'index_enhance'),
        ('512890', '红利低波etf华泰', None, 'high_dividend'),
        ('513100', '纳指ETF', None, 'cross_border'),
        ('159920', '恒生ETF', None, 'cross_border'),
        ('518880', '黄金ETF', None, 'gold'),
        ('511880', '银华日利', None, 'cash'),
        ('601398', '工商银行', '银行', 'high_dividend'),
        ('600900', '长江电力', '电力', 'high_dividend'),
        ('600519', '贵州茅台', '白酒', 'buffett_value'),
        ('000538', '云南白药', '医药', 'buffett_value'),
        ('512170', '中证医疗', None, 'buffett_value'),  # 行业 ETF
    ]
    for code, name, cat, expected in cases:
        actual, reason = classify_holding(code, name, cat or '')
        icon = '✅' if actual == expected else '❌'
        print(f'  {icon} {code} {name}: {actual} ({reason})')

    # 实际持仓测试
    print("\n=== 实际持仓配置测试 ===")
    with open('holdings.json', encoding='utf-8') as f:
        holdings = json.load(f)
    try:
        with open('user_cash.json', encoding='utf-8') as f:
            cash = json.load(f).get('amount', 0)
    except Exception:
        cash = 0

    result = calc_allocation_breakdown(holdings, cash)
    if result:
        print(f"总资产: ¥{result['total_assets']:,.0f}")
        print(f"综合状态: {result['overall_status']}（最大偏差 {result['max_deviation']}pp）")
        print()
        for b in result['breakdown']:
            color = {'green': '🟢', 'yellow': '🟡', 'red': '🔴'}[b['status']]
            print(f"  {color} {b['label']}: 实际 {b['actual_pct']:5.1f}% / 目标 {b['target_pct']}% "
                  f"(偏差 {b['deviation_pp']:+5.1f}pp, ¥{b['market_value']:,.0f})")
