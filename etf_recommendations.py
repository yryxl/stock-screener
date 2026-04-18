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
# 推荐池：每个资产类别的代表性 ETF
# ============================================================
RECOMMENDATION_POOL = {
    "buffett_value": {
        "label": "🎯 巴菲特式价值股",
        "etfs": [],
        "advice": "建议买个股而不是 ETF。看 Tab1 模型推荐里 ✅ 现金够买的股。"
                  "或买行业 ETF（512170 中证医疗 / 159928 中证主要消费 等）"
    },
    "index_enhance": {
        "label": "📊 指数增强",
        "etfs": [
            {"code": "510330", "name": "沪深300 ETF", "index_key": "000300",
             "preferred": True, "reason": "费率低、流动性好、跟踪误差小"},
            {"code": "510500", "name": "中证500 ETF", "index_key": "000905",
             "preferred": True, "reason": "中盘成长，与沪深300 互补"},
            {"code": "512100", "name": "中证1000 ETF", "index_key": "000852",
             "preferred": False, "reason": "小盘股代表，波动较大"},
        ]
    },
    "cross_border": {
        "label": "🌍 跨境资产",
        "etfs": [
            {"code": "159920", "name": "恒生 ETF", "index_key": "HSI",
             "preferred": True, "reason": "港股 CAPE 11.5 ✅ 估值合理（价值洼地）"},
            {"code": "513660", "name": "恒生互联网 ETF", "index_key": "HSI",
             "preferred": False, "reason": "腾讯/阿里等龙头，但波动大"},
            {"code": "513500", "name": "标普500 ETF", "index_key": "S&P500",
             "preferred": False, "reason": "⚠ 标普 CAPE 38.5（85% 分位），偏贵"},
            {"code": "513100", "name": "纳指 ETF", "index_key": "NASDAQ100",
             "preferred": False, "reason": "🚨 纳指 CAPE 45 历史次高，七巨头占 50%"},
        ]
    },
    "high_dividend": {
        "label": "💰 高股息防御",
        "etfs": [
            {"code": "512890", "name": "红利低波 ETF", "index_key": "H30269",
             "preferred": True, "reason": "中证红利低波动，分散+股息双优"},
            {"code": "510880", "name": "上证红利 ETF", "index_key": "000015",
             "preferred": True, "reason": "上证 50 红利股精选"},
            {"code": "515080", "name": "中证红利 ETF", "index_key": "000922",
             "preferred": False, "reason": "中证红利指数，覆盖更广"},
        ]
    },
    "gold": {
        "label": "🥇 黄金",
        "etfs": [
            {"code": "518880", "name": "黄金 ETF (华安)", "index_key": None,
             "preferred": True, "reason": "规模最大、流动性最好的实物黄金 ETF"},
            {"code": "159934", "name": "黄金 ETF (易方达)", "index_key": None,
             "preferred": False, "reason": "易方达管理，适合定投"},
        ]
    },
    "cash": {
        "label": "💵 现金",
        "etfs": [
            {"code": "511880", "name": "银华日利 货币 ETF", "index_key": None,
             "preferred": True, "reason": "T+0 货币基金，流动性极好"},
            {"code": "511010", "name": "国债 ETF", "index_key": None,
             "preferred": False, "reason": "5 年国债，对冲股市风险"},
        ],
        "advice": "现金过多时（>10%）应该考虑投出去。但市场极冷时应保留 20-30% 现金"
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
    给单只 ETF 综合评级（基于 CAPE + 集中度）

    返回：(rating, reason)
      rating: 'green' / 'yellow' / 'red' / 'unknown'
    """
    code = etf.get('code', '')
    index_key = etf.get('index_key')

    # 1. 跨境 ETF 看 CAPE
    if index_key in ('NASDAQ100', 'S&P500', 'HSI', 'DAX'):
        try:
            from cape_monitor import get_market_cape_status
            cape = get_market_cape_status(index_key)
            if cape:
                return cape['status'], (
                    f"CAPE {cape['current_cape']:.1f}, 预期回报 {cape['forecast_return']}%"
                )
        except Exception:
            pass

    # 2. A 股 ETF 看集中度（红就警告）
    try:
        from etf_concentration import check_etf_concentration
        conc = check_etf_concentration(code)
        if conc:
            sev = conc.get('severity', 'neutral')
            if sev == 'red':
                return 'red', f"前 10 大权重 {conc['top10_weight_pct']}%（伪宽基）"
            elif sev == 'green':
                return 'green', f"集中度健康（前 10 大 {conc['top10_weight_pct']}%）"
            elif sev == 'yellow':
                return 'yellow', f"集中度偏高（前 10 大 {conc['top10_weight_pct']}%）"
            else:
                return 'unknown', '策略/设计本意，按其原则评判'
    except Exception:
        pass

    return 'unknown', '无评级数据'


def get_recommendations_from_allocation(allocation_breakdown):
    """
    根据资产配置健康度结果，自动给出推荐补缺清单

    输入：calc_allocation_breakdown() 的返回值
    返回：[{asset_class, label, deviation_pp, etfs, ...}, ...]
        仅返回偏差 < -5pp（缺位明显）或 > +20pp（严重超目标，仅 cash）的类别
    """
    if not allocation_breakdown:
        return []

    recommendations = []
    for b in allocation_breakdown.get('breakdown', []):
        deviation = b.get('deviation_pp', 0)
        cls = b.get('asset_class')

        # cash 超目标 > 20pp → 推荐分散到其他资产
        if cls == 'cash' and deviation > 20:
            # 不推 cash 自己，而是提示
            recommendations.append({
                'asset_class': cls,
                'label': b['label'],
                'deviation_pp': deviation,
                'etfs': [],
                'advice': f'现金 +{deviation:.1f}pp 严重超目标。建议分散到缺位的资产类别（见下面推荐）'
            })
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
