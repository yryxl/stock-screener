"""
TODO-040 ETF 推荐功能（2026-04-18）

设计依据：
  用户原话："我现在有了 ETF 反馈，但没有 ETF 推荐"
  当前 etf_monitor.py 仅评估已持有的 ETF，不推荐"应该买哪些"
  应该：根据资产配置目标偏差 → 推荐对应 ETF + CAPE/分位/集中度综合判断

推荐池设计（每个资产类别 2-3 只代表性 ETF）：
  - 巴菲特价值股：不推荐 ETF，指向模型个股推荐
  - 指数增强（缺时）：沪深300 / 中证500 / 中证1000
  - 跨境资产（缺时）：恒生（CAPE 11.5 ✅）/ 纳指 100（CAPE 45 🚨）/ 标普500（CAPE 38.5 🚨）
  - 高股息防御（缺时）：红利低波 / 上证红利 / 中证红利
  - 黄金（缺时）：518880 黄金 ETF
  - 现金（超时）：买货币 ETF 511880 等

综合评级：
  ✅ 现在就买：CAPE 绿灯 + 集中度健康
  ⚠ 谨慎少买：CAPE 黄灯 或 集中度偏高
  🚨 暂时不买：CAPE 红灯（即使缺位）或 名义伪宽基
"""


# ============================================================
# 推荐池（v2 重构 2026-04-18）：用户明确"防守为主、吃复利、求稳"
# 设计原则：
#   1. 大公司靠谱（华夏/华泰/易方达/嘉实/南方/华安/博时等头部）
#   2. 手续费低（管理费率 ≤ 0.5%/年优先）
#   3. 表现平稳（规模 ≥ 50 亿，长期跟踪误差小）
#   4. 风险偏大的不放（行业 ETF / 创业板 / 科创板 / 高 CAPE 跨境）
#   5. 货币/国债 ETF 重点扩展（用户用于放机动资金）
# ============================================================
RECOMMENDATION_POOL = {
    "buffett_value": {
        "label": "🎯 巴菲特式价值股",
        "etfs": [],
        "advice": "用户原则：同样风险更愿意投个股。看 Tab1 模型推荐里 ✅ 现金够买的股。"
    },
    "index_enhance": {
        "label": "📊 宽基稳健 ETF",
        "etfs": [
            # 沪深 300（同指数 3 只，分散黑天鹅）
            {"code": "510330", "name": "沪深300 ETF（华夏）", "index_key": "000300",
             "preferred": True, "reason": "规模最大 1500 亿+，费率 0.5%，流动性最佳"},
            {"code": "510300", "name": "沪深300 ETF（华泰柏瑞）", "index_key": "000300",
             "preferred": True, "reason": "国内首只沪深300 ETF，老牌靠谱，费率 0.5%"},
            {"code": "159919", "name": "沪深300 ETF（嘉实）", "index_key": "000300",
             "preferred": False, "reason": "深市流动性好，跟同指数差异 <0.1%"},
            # 中证 500（同指数 2 只，但中盘波动大于沪深300，标 ⚠）
            {"code": "510500", "name": "中证500 ETF（南方）", "index_key": "000905",
             "preferred": False, "reason": "⚠ 中盘 ETF 波动大于沪深300，比例不要超过 30%"},
            {"code": "159922", "name": "中证500 ETF（嘉实）", "index_key": "000905",
             "preferred": False, "reason": "⚠ 中盘 ETF（同 510500）"},
        ],
        "advice": "防守首选沪深 300，3 只同指数 ETF 任选。中证 500 偏成长，比例控制在 30% 以内"
    },
    "cross_border": {
        "label": "🌍 跨境资产（仅推估值合理的）",
        "etfs": [
            # 恒生（CAPE 11.5 估值低，符合防守原则）
            {"code": "159920", "name": "恒生 ETF（华夏）", "index_key": "HSI",
             "preferred": True, "reason": "港股 CAPE 11.5 ✅ 估值合理，预期回报 7%/年"},
            {"code": "510900", "name": "H 股 ETF（易方达）", "index_key": "HSI",
             "preferred": True, "reason": "跟 H 股指数，避开科技股集中度风险"},
            {"code": "513900", "name": "港股通 100（华夏）", "index_key": "HSI",
             "preferred": False, "reason": "港股通精选，分散性好"},
            # 不推：纳指/标普（CAPE 红灯，违反"求稳"原则）
            {"code": "513500", "name": "标普500 ETF", "index_key": "S&P500",
             "preferred": False, "reason": "🚨 CAPE 38.5（85% 分位）暂不推荐"},
            {"code": "513100", "name": "纳指 ETF", "index_key": "NASDAQ100",
             "preferred": False, "reason": "🚨 CAPE 45 历史次高，七巨头占 50%，不符合稳健原则"},
        ],
        "advice": "防守跨境只推港股（估值低）。美股 CAPE 历史高位，违反求稳原则"
    },
    "high_dividend": {
        "label": "💰 高股息防御（防守核心，重点持有）",
        "etfs": [
            # 红利低波（防守王者）
            {"code": "512890", "name": "红利低波 ETF（华泰柏瑞）", "index_key": "H30269",
             "preferred": True, "reason": "中证红利低波动，分散+股息双优，规模 100 亿+"},
            # 上证红利（老牌防守）
            {"code": "510880", "name": "上证红利 ETF（华泰）", "index_key": "000015",
             "preferred": True, "reason": "国内最早红利 ETF，规模最大，费率 0.15%"},
            # 中证红利（覆盖更广）
            {"code": "515080", "name": "中证红利 ETF（招商）", "index_key": "000922",
             "preferred": True, "reason": "中证红利指数，100 只成分股分散性好"},
            # 红利 ETF 易方达
            {"code": "510888", "name": "红利 ETF（易方达）", "index_key": "000015",
             "preferred": False, "reason": "同上证红利指数，易方达管理"},
        ],
        "advice": "防守核心仓位（占 ETF 总仓位 30-40%）。3 只同时持有分散基金公司风险"
    },
    "gold": {
        "label": "🥇 黄金（避险刚需）",
        "etfs": [
            {"code": "518880", "name": "黄金 ETF（华安）", "index_key": None,
             "preferred": True, "reason": "规模最大 200 亿+，跟踪实物黄金，费率 0.5%"},
            {"code": "159934", "name": "黄金 ETF（易方达）", "index_key": None,
             "preferred": True, "reason": "易方达管理靠谱，规模 80 亿+"},
            {"code": "518800", "name": "黄金 ETF（国泰）", "index_key": None,
             "preferred": False, "reason": "国泰管理，跟金价误差小"},
            {"code": "159937", "name": "黄金 ETF（博时）", "index_key": None,
             "preferred": False, "reason": "博时管理，备选"},
        ],
        "advice": "黄金是真正的避险（地缘/通胀对冲）。建议 5-10% 仓位，3 家分散"
    },
    "cash": {
        "label": "💵 现金类 ETF（重点扩展，用于放机动资金）",
        "etfs": [
            # 货币 ETF（T+0，随时支取）
            {"code": "511880", "name": "银华日利（货币 ETF）", "index_key": None,
             "preferred": True, "reason": "🌟 T+0 货币 ETF 规模最大 800 亿+，年化 2-2.5%，T+0 随时支取"},
            {"code": "511990", "name": "华宝添益（货币 ETF）", "index_key": None,
             "preferred": True, "reason": "🌟 T+0 货币 ETF 第二大，年化 2-2.5%，老牌"},
            {"code": "511660", "name": "建信添益（货币 ETF）", "index_key": None,
             "preferred": True, "reason": "🌟 建信管理，T+0 货币 ETF"},
            {"code": "159001", "name": "易方达保证金（货币 ETF）", "index_key": None,
             "preferred": False, "reason": "易方达管理，作为分散选择"},
            # 国债 ETF（短债更稳）
            {"code": "511010", "name": "国债 ETF（5 年期）", "index_key": None,
             "preferred": True, "reason": "5 年国债 ETF，年化 2.5-3%，T+0 支取"},
            {"code": "511260", "name": "10 年国债 ETF（国泰）", "index_key": None,
             "preferred": False, "reason": "10 年期国债，利率敏感度高（涨跌大）"},
            {"code": "511020", "name": "平安 5-10 年国开债 ETF", "index_key": None,
             "preferred": False, "reason": "国开债比国债收益略高，安全度同等"},
        ],
        "advice": "🌟 货币 ETF 是放现金的最佳选择：T+0 随时取、年化 2-2.5%（高于活期 0.3%）、"
                   "0 手续费、规模大不会清盘。建议机动资金的 70-80% 放这里。"
                   "国债 ETF 用于剩余 20-30%（年化更高但波动稍大）"
    },
}


def get_recommendations_for_class(asset_class, deviation_pp=None):
    """
    根据资产类别返回推荐 ETF + 综合评级

    输入：
      asset_class: 'cross_border' / 'index_enhance' / 'high_dividend' / 'gold' / 'cash' / 'buffett_value'
      deviation_pp: 偏差（>0 超目标，<0 缺位）。仅"缺位"才推荐买
    返回：dict {
      'asset_class', 'label', 'etfs': [{...with rating}, ...], 'advice'
    }
    """
    pool = RECOMMENDATION_POOL.get(asset_class)
    if not pool:
        return None

    enriched_etfs = []
    for etf in pool.get('etfs', []):
        # 综合评级：根据 CAPE + 集中度
        rating, rating_reason = _calc_etf_rating(etf)
        enriched_etfs.append({
            **etf,
            'rating': rating,
            'rating_reason': rating_reason,
        })

    # 排序：preferred=True 优先 + rating 越好越靠前
    rating_order = {'green': 0, 'yellow': 1, 'red': 2, 'unknown': 3}
    enriched_etfs.sort(key=lambda e: (
        not e.get('preferred', False),
        rating_order.get(e['rating'], 3),
    ))

    return {
        'asset_class': asset_class,
        'label': pool['label'],
        'etfs': enriched_etfs,
        'advice': pool.get('advice'),
        'deviation_pp': deviation_pp,
    }


def _calc_etf_rating(etf):
    """
    给单只 ETF 综合评级（基于"估值时机 + 集中度"）

    用户原则（2026-04-18）："宽基是好的，但在错误的价格买入就是错误的"
    所以 A 股 ETF 必须综合 PE 分位 + 集中度，不能只看集中度

    评级逻辑：
      估值（决定买入时机）：
        🔴 PE 分位 ≥85% / CAPE >历史 90% 分位 → 极热泡沫，不推
        🟡 PE 分位 70-85% / CAPE 70-90% → 偏热谨慎
        🟢 PE 分位 ≤70% / CAPE ≤70% → 估值合理或便宜
      集中度（决定结构风险）：
        🔴 fake_broad（如纳指七巨头）→ 即使估值低也警告
        🟢 真宽基/策略/设计本意 → 不影响评级
      综合：取最严重的（任一项🔴 → 总🔴）

    返回：(rating, reason)
    """
    code = etf.get('code', '')
    index_key = etf.get('index_key')

    valuation_rating = 'unknown'
    valuation_reason = ''
    concentration_rating = 'unknown'
    concentration_reason = ''

    # 1. 估值（跨境用 CAPE，A 股用 PE 分位）
    if index_key in ('NASDAQ100', 'S&P500', 'HSI', 'DAX'):
        try:
            from cape_monitor import get_market_cape_status
            cape = get_market_cape_status(index_key)
            if cape:
                valuation_rating = cape['status']
                valuation_reason = f"CAPE {cape['current_cape']:.1f}, 预期回报 {cape['forecast_return']}%"
        except Exception:
            pass
    else:
        # A 股 ETF 用 PE 分位（数据来自 etf_monitor 的历史采集）
        try:
            from etf_monitor import load_etf_index_map, load_index_history, compute_etf_temperature
            etf_map = load_etf_index_map()
            etf_info = etf_map.get(str(code).zfill(6))
            if etf_info:
                idx_code = etf_info.get('index')
                store = load_index_history(idx_code)
                if store:
                    temp = compute_etf_temperature(store)
                    pct = temp.get('percentile')
                    if pct is not None:
                        if pct >= 85:
                            valuation_rating = 'red'
                            valuation_reason = f"⚠ PE 分位 {pct:.0f}%（极热泡沫，错误价格）"
                        elif pct >= 70:
                            valuation_rating = 'yellow'
                            valuation_reason = f"PE 分位 {pct:.0f}%（偏热，谨慎少买）"
                        elif pct <= 30:
                            valuation_rating = 'green'
                            valuation_reason = f"✅ PE 分位 {pct:.0f}%（估值便宜，好时机）"
                        else:
                            valuation_rating = 'green'
                            valuation_reason = f"PE 分位 {pct:.0f}%（合理区间）"
        except Exception:
            pass

    # 2. 集中度（结构风险）
    try:
        from etf_concentration import check_etf_concentration
        conc = check_etf_concentration(code)
        if conc:
            sev = conc.get('severity', 'neutral')
            if sev == 'red':
                concentration_rating = 'red'
                concentration_reason = f"前 10 大 {conc['top10_weight_pct']}%（伪宽基）"
            elif sev == 'green':
                concentration_rating = 'green'
                concentration_reason = f"前 10 大 {conc['top10_weight_pct']}%（结构健康）"
            elif sev == 'yellow':
                concentration_rating = 'yellow'
                concentration_reason = f"前 10 大 {conc['top10_weight_pct']}%（集中度偏高）"
            else:
                concentration_rating = 'green'  # 策略/设计本意按健康
                concentration_reason = '策略 ETF，按设计'
    except Exception:
        pass

    # 3. 综合评级（取最严重）
    sev_order = {'red': 3, 'yellow': 2, 'green': 1, 'unknown': 0}
    final_sev = max(sev_order.get(valuation_rating, 0),
                     sev_order.get(concentration_rating, 0))
    final_rating = next((k for k, v in sev_order.items() if v == final_sev), 'unknown')

    # 组合理由
    parts = []
    if valuation_reason:
        parts.append(f"估值: {valuation_reason}")
    if concentration_reason:
        parts.append(f"结构: {concentration_reason}")
    final_reason = ' | '.join(parts) if parts else '无评级数据'

    return final_rating, final_reason


def get_recommendations_from_allocation(allocation_breakdown):
    """
    根据资产配置健康度结果，自动给出推荐补缺清单

    输入：calc_allocation_breakdown() 的返回值
    返回：[{asset_class, label, deviation_pp, etfs, ...}, ...]
    """
    if not allocation_breakdown:
        return []

    recommendations = []
    cash_amount_value = 0  # 现金市值（决定是否推货币 ETF）
    for b in allocation_breakdown.get('breakdown', []):
        deviation = b.get('deviation_pp', 0)
        cls = b.get('asset_class')

        # cash 类别：无论是否超目标，只要有现金就推货币 ETF（活期利率太低）
        if cls == 'cash':
            actual_pct = b.get('actual_pct', 0)
            cash_value = b.get('market_value', 0)
            cash_amount_value = cash_value
            # 即使现金占比合理（5%），也应该放货币 ETF（不是真"放活期"）
            rec = get_recommendations_for_class(cls, deviation_pp=deviation)
            if rec:
                if deviation > 20:
                    # 超目标：警示 + 仍推货币 ETF
                    rec['advice'] = (
                        f'⚠ 现金 {actual_pct}% 严重超目标 5%（+{deviation:.0f}pp）。'
                        f'建议：(1) 70-80% 现金放下方货币 ETF（年化 2-2.5%）；'
                        f'(2) 剩余分散到缺位的资产类别（见上方推荐）'
                    )
                else:
                    rec['advice'] = (
                        f'💡 不要让现金躺在活期账户（年化 0.3%）。'
                        f'放下方货币 ETF：T+0 随时取、年化 2-2.5%、0 手续费、规模大不会清盘'
                    )
                recommendations.append(rec)
            continue

        # 其他类别缺位 < -5pp → 推荐买
        if deviation < -5:
            rec = get_recommendations_for_class(cls, deviation_pp=deviation)
            if rec:
                recommendations.append(rec)

    return recommendations


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    # 单元自测
    print("=== 测试：跨境资产推荐 ===")
    rec = get_recommendations_for_class('cross_border', deviation_pp=-20)
    if rec:
        print(f"{rec['label']} (缺 {-rec['deviation_pp']}pp):")
        for etf in rec['etfs']:
            emoji = {'green': '🟢', 'yellow': '🟡', 'red': '🔴', 'unknown': '⚪'}[etf['rating']]
            star = '⭐' if etf.get('preferred') else '  '
            print(f"  {emoji}{star} {etf['code']} {etf['name']}: {etf['rating_reason']}")
            print(f"        {etf['reason']}")

    print()
    print("=== 测试：实际持仓配置推荐 ===")
    import json
    from allocation_check import calc_allocation_breakdown
    with open('holdings.json', encoding='utf-8') as f:
        holdings = json.load(f)
    try:
        with open('user_cash.json', encoding='utf-8') as f:
            cash = float(json.load(f).get('amount', 0))
    except Exception:
        cash = 0

    alloc = calc_allocation_breakdown(holdings, cash)
    recs = get_recommendations_from_allocation(alloc)
    for r in recs:
        if r['etfs']:
            print(f"\n📊 {r['label']} (偏差 {r['deviation_pp']:+.1f}pp)：")
            for etf in r['etfs'][:3]:
                emoji = {'green': '🟢', 'yellow': '🟡', 'red': '🔴', 'unknown': '⚪'}[etf['rating']]
                star = '⭐' if etf.get('preferred') else '  '
                print(f"  {emoji}{star} {etf['code']} {etf['name']}: {etf['rating_reason']}")
        else:
            print(f"\n💡 {r['label']} (偏差 {r['deviation_pp']:+.1f}pp): {r.get('advice','')}")
