"""
TODO-035 可买性判定 + 严格换仓建议（2026-04-18）

设计原则（用户两次明确）：
  1. 删除 100 元硬过滤，按"可动用资金"推荐
  2. 可动用资金 = 现金 + 持仓中"已有卖出信号"的市值（方案 B）
  3. 换仓建议必须严格：
     - A 类：持仓股本就该卖（sell_heavy/sell_medium）+ 新股是 buy_*
     - B 类：新股多维度明显优于持有股（≥3 项）+ 仅换 30%（保留 70% 仓底）
     - C 类：不到非常明确的优势 → 不出建议
  4. 任何换仓建议都明确"务必保留至少 70% 仓底，不要清仓换仓"

A 股交易规则：最小买入单位 100 股
"""


# A 股最小买入单位
MIN_LOT_SIZE = 100


def calc_min_buy_amount(price):
    """
    计算买 1 手（100 股）需要的最小金额

    返回：float（元）或 None（价格无效）
    """
    if price is None or price <= 0:
        return None
    return MIN_LOT_SIZE * float(price)


def calc_available_cash(cash, holdings, holding_signals):
    """
    方案 B：可动用资金 = 现金 + 持仓中"已有卖出信号"的市值

    "卖出信号" 指 sell_heavy / sell_medium（明确的强烈卖出，不含 sell_light/watch）
    sell_light/sell_watch 是温和提示，不算"该卖"

    输入：
      cash: 现金（user_cash.json amount）
      holdings: [{code, shares, cost, ...}, ...]
      holding_signals: [{code, signal, price, ...}, ...]
    返回：dict {
      'cash': X, 'sellable_value': X, 'available': X,
      'sellable_holdings': [{code, name, signal, value}, ...]
    }
    """
    cash = float(cash or 0)
    sellable_holdings = []
    sellable_value = 0.0

    # 信号映射
    signal_map = {}
    for sig in (holding_signals or []):
        code = str(sig.get('code', '')).zfill(6)
        if code:
            signal_map[code] = sig

    for h in (holdings or []):
        code = str(h.get('code', '')).zfill(6)
        sig = signal_map.get(code, {})
        signal = sig.get('signal', '')
        # 只算明确的强烈卖出，不算温和提示
        if signal not in ('sell_heavy', 'sell_medium'):
            continue
        price = sig.get('price') or h.get('cost', 0)
        shares = h.get('shares', 0) or 0
        value = price * shares
        if value > 0:
            sellable_value += value
            sellable_holdings.append({
                'code': code,
                'name': h.get('name', code),
                'signal': signal,
                'value': value,
                'shares': shares,
                'price': price,
            })

    return {
        'cash': cash,
        'sellable_value': round(sellable_value, 2),
        'available': round(cash + sellable_value, 2),
        'sellable_holdings': sellable_holdings,
    }


def classify_affordability(stock, available_funds):
    """
    把一个推荐股按"可买性"分类

    输入：
      stock: {code, name, price, signal, ...}
      available_funds: dict from calc_available_cash
    返回：dict {
      'status': 'affordable' / 'swap_needed' / 'unaffordable',
      'min_buy_amount': X,
      'cash_only_can_buy': bool（仅用现金能买）,
      'with_swap_can_buy': bool（用现金+卖出信号资金能买）,
      'message': 文案,
    }
    """
    price = stock.get('price')
    min_buy = calc_min_buy_amount(price)

    if min_buy is None:
        return {
            'status': 'unknown',
            'min_buy_amount': None,
            'message': '价格未知，无法判断是否买得起'
        }

    cash = available_funds.get('cash', 0)
    available = available_funds.get('available', cash)

    if cash >= min_buy:
        return {
            'status': 'affordable',
            'min_buy_amount': round(min_buy, 0),
            'cash_only_can_buy': True,
            'with_swap_can_buy': True,
            'message': f'✅ 现金够买 1 手（¥{min_buy:,.0f}）'
        }
    elif available >= min_buy:
        gap = min_buy - cash
        return {
            'status': 'swap_needed',
            'min_buy_amount': round(min_buy, 0),
            'cash_only_can_buy': False,
            'with_swap_can_buy': True,
            'gap': round(gap, 0),
            'message': (f'🔄 现金 ¥{cash:,.0f} 差 ¥{gap:,.0f}，'
                        f'卖掉持仓中卖出信号股可凑够')
        }
    else:
        gap = min_buy - available
        return {
            'status': 'unaffordable',
            'min_buy_amount': round(min_buy, 0),
            'cash_only_can_buy': False,
            'with_swap_can_buy': False,
            'gap': round(gap, 0),
            'message': (f'❌ 卖光所有应卖也凑不够（¥{available:,.0f} < ¥{min_buy:,.0f}）'
                        f'，差 ¥{gap:,.0f}')
        }


def compute_swap_recommendation(new_stock, holdings, holding_signals, available_funds):
    """
    生成换仓建议（严格按用户原则：不为买而卖）

    返回：dict {
      'swap_type': 'A' / 'B' / 'C' / 'none',
      'target_holdings': [{code, name, sell_value, sell_pct, ...}, ...],
      'reserve_advice': str（保留仓底提示）,
      'message': 主消息,
    }

    分类逻辑：
      A 类：持仓里有 sell_heavy/sell_medium → 用这部分钱（已经在 available 里了）
            目标持仓 = sellable_holdings 全卖
      B 类：新股的 total_score / king 等多维度明显优于某只 hold 持仓
            → 建议小换：卖掉持有股 30%
            → 强制保留 70%
      C 类：以上都不满足 → 不建议换
    """
    if not new_stock or new_stock.get('signal', '').startswith('buy_') is False:
        # 非买入信号，不需要换仓
        return {'swap_type': 'none', 'message': '新股不是买入信号，无需换仓'}

    # ========== A 类：持仓有卖出信号 ==========
    sellable = available_funds.get('sellable_holdings', [])
    if sellable:
        return {
            'swap_type': 'A',
            'target_holdings': [
                {'code': s['code'], 'name': s['name'], 'signal': s['signal'],
                 'sell_value': s['value'], 'sell_pct': 100,
                 'reason': f'本就有 {s["signal"]} 信号，应卖出'}
                for s in sellable
            ],
            'reserve_advice': '✓ 这些持仓本就有卖出信号，可全部卖出。'
                              '务必保留账户至少 30% 现金作为机动弹药',
            'message': (f'A 类换仓：用持仓中{len(sellable)} 只卖出信号股的资金'
                        f'（合计 ¥{available_funds.get("sellable_value", 0):,.0f}）'
                        f'买入 {new_stock.get("name", "")}'),
        }

    # ========== B 类：新股多维度明显优于某只持有股 ==========
    # 严格判定：至少 3 项明显优于
    new_score = new_stock.get('total_score', 0) or 0
    new_roe = new_stock.get('roe', 0) or 0
    new_is_king = new_stock.get('is_10y_king', False)
    new_pe = new_stock.get('pe', 0) or 0
    new_target = new_stock.get('max_buy_price_rr10') or 0  # REQ-184 倒推合理价
    new_price = new_stock.get('price', 0) or 0
    new_safety = (new_target / new_price - 1) * 100 if new_price > 0 and new_target > 0 else 0

    # 取 holding_signals 里的"普通持有"持仓（非 sell_*，因为有卖信号已经走 A 类）
    sig_map = {str(s.get('code', '')).zfill(6): s for s in (holding_signals or [])}

    candidates_b = []
    for h in (holdings or []):
        code = str(h.get('code', '')).zfill(6)
        # 跳过 ETF（不和个股比较）
        if code.startswith(('5', '1')):
            continue
        sig = sig_map.get(code, {})
        signal = sig.get('signal', '')
        if signal in ('sell_heavy', 'sell_medium', 'true_decline'):
            continue  # 已在 A 类处理

        old_score = sig.get('total_score', 0) or 0
        old_roe = sig.get('roe', 0) or 0
        old_is_king = sig.get('is_10y_king', False)
        old_pe = sig.get('pe', 0) or 0
        # 持有股的安全边际
        old_target = sig.get('max_buy_price_rr10') or 0
        old_price = sig.get('price', 0) or 0
        old_safety = (old_target / old_price - 1) * 100 if old_price > 0 and old_target > 0 else 0

        # 多维度明显优于判定（每项各 1 票）
        votes = 0
        reasons = []
        if new_score - old_score >= 5:
            votes += 1
            reasons.append(f'总分 +{new_score - old_score:.0f}pp')
        if new_roe - old_roe >= 5:
            votes += 1
            reasons.append(f'ROE +{new_roe - old_roe:.1f}pp')
        if new_is_king and not old_is_king:
            votes += 1
            reasons.append('十年王者 vs 非王者')
        # PE 更接近 fair_low（数值更小、有 PE）
        if 0 < new_pe < old_pe * 0.7:
            votes += 1
            reasons.append(f'PE {new_pe:.1f} 远低于持有 {old_pe:.1f}')
        if new_safety - old_safety >= 10:
            votes += 1
            reasons.append(f'安全边际 +{new_safety - old_safety:.0f}pp')

        if votes >= 3:
            candidates_b.append({
                'code': code, 'name': h.get('name', code),
                'votes': votes, 'reasons': reasons,
                'sig': sig, 'shares': h.get('shares', 0),
                'price': old_price,
            })

    if candidates_b:
        # 按优势分排序，取前 1
        candidates_b.sort(key=lambda x: -x['votes'])
        best = candidates_b[0]
        sell_pct = 30  # 强制只换 30%
        sell_shares = int(best['shares'] * sell_pct / 100 / 100) * 100  # 整 100 股
        sell_value = sell_shares * best['price']
        return {
            'swap_type': 'B',
            'target_holdings': [{
                'code': best['code'], 'name': best['name'],
                'sell_value': sell_value, 'sell_pct': sell_pct,
                'votes': best['votes'], 'reasons': best['reasons'],
                'reason': '；'.join(best['reasons']),
            }],
            'reserve_advice': (f'⚠ 务必保留至少 70% 持有股（{best["name"]}）作为仓底。'
                                f'不要清仓换仓——巴菲特/芒格反复强调"长期持有"，'
                                f'即使新股看起来更好，旧股也可能继续上涨'),
            'message': (f'B 类小幅换仓建议：{new_stock.get("name", "")} '
                        f'多维度优于 {best["name"]}（{best["votes"]} 项优势）。'
                        f'建议卖掉 {best["name"]} 30%（约 ¥{sell_value:,.0f}），'
                        f'保留 70% 作为仓底'),
        }

    # ========== C 类：不建议换 ==========
    return {
        'swap_type': 'C',
        'message': (f'不建议换仓：持仓没有卖出信号，新股优势也不到"明显优于"门槛'
                    f'（需多维度 ≥ 3 项明显领先才换）。建议存现金等待买入时机'),
        'reserve_advice': '保持现有持仓，等待现金积累或更明确的换仓机会',
    }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    # 单元自测
    print("=== calc_min_buy_amount 测试 ===")
    print(f'  茅台 1500 元: {calc_min_buy_amount(1500)} (期望 150000)')
    print(f'  长电 26 元: {calc_min_buy_amount(26)} (期望 2600)')
    print(f'  价格 None: {calc_min_buy_amount(None)} (期望 None)')

    print("\n=== calc_available_cash 测试 ===")
    holdings = [
        {'code': '600519', 'name': '茅台', 'shares': 100, 'cost': 1500},
        {'code': '510330', 'name': '沪深300etf', 'shares': 700, 'cost': 4.9},
    ]
    holding_signals = [
        {'code': '600519', 'signal': 'sell_heavy', 'price': 1700},
        {'code': '510330', 'signal': 'hold', 'price': 5.0},
    ]
    af = calc_available_cash(50000, holdings, holding_signals)
    print(f'  现金 5万 + 茅台 sell_heavy 17万: 可动用 ¥{af["available"]:,.0f}')
    print(f'  可卖持仓: {[h["name"] for h in af["sellable_holdings"]]}')

    print("\n=== classify_affordability 测试 ===")
    stock_high = {'code': '600519', 'name': '茅台', 'price': 1500, 'signal': 'buy_light'}
    stock_low = {'code': '601398', 'name': '工行', 'price': 6, 'signal': 'buy_medium'}
    print(f'  现金 5 万买茅台 1 手 15 万: {classify_affordability(stock_high, af)["status"]}')
    print(f'  现金 5 万买工行 1 手 600: {classify_affordability(stock_low, af)["status"]}')

    print("\n=== compute_swap_recommendation 测试 ===")
    new_stock = {'code': '603288', 'name': '海天', 'signal': 'buy_light',
                  'total_score': 35, 'roe': 28, 'is_10y_king': True, 'pe': 25,
                  'price': 50, 'max_buy_price_rr10': 60}
    swap = compute_swap_recommendation(new_stock, holdings, holding_signals, af)
    print(f'  类型: {swap["swap_type"]}')
    print(f'  消息: {swap["message"][:80]}...')
    print(f'  建议: {swap.get("reserve_advice", "")[:80]}...')
