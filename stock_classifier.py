"""
TODO-046 股票防守型 vs 进攻型分类（2026-04-18）

设计依据（巴菲特/芒格理念 + A 股本土化）：
  防守型 = 长期持有吃息，抗波动（巴菲特"宁可错过不犯错"的主战场）
    典型：可口可乐 / 美国运通（巴菲特持仓）
    A 股：工行/长电/大秦/中证红利 ETF
  进攻型 = 追求成长，估值波动大（巴菲特后来才接受，如苹果）
    典型：苹果 / 比亚迪（巴菲特后期持仓）
    A 股：茅台/恒瑞/创业板 ETF
  中性型 = 介于两者之间（大部分白马股）

判定标准（按优先级）：
  1. 行业归属（电力/银行/铁路 → 防守；半导体/新能源/医药 → 进攻）
  2. 股息率（≥ 4% 偏防守 / ≤ 1% 偏进攻）
  3. ROE + PE 组合（高 ROE+ 高 PE → 进攻）
  4. ETF 类型（宽基/红利策略 → 防守，行业 → 进攻）
"""


# ============================================================
# 防守型行业关键词
# ============================================================
DEFENSIVE_INDUSTRIES = [
    # 公用事业（吃水/电/燃气）
    '电力', '公用事业', '燃气', '水务',
    # 基建（高速/铁路/港口/机场）
    '铁路', '高速', '港口', '机场', '铁路公路',
    # 银行/保险（高股息）
    '银行', '保险',
    # 通信运营（中移动/中电信）
    '通信服务', '通信运营',
    # 煤炭（高股息周期防御）
    '煤炭', '煤炭开采',
    # 石油石化（高股息）
    '石油', '石化',
    # 巴菲特核心持仓类（消费品+品牌+护城河）
    '白酒', '调味品', '调味发酵', '乳制品', '饮料', '食品',
    '中药', '同仁堂',  # 老字号中药
    '免税', '旅游零售',
]

# ============================================================
# 进攻型行业关键词（巴菲特后期才接受，散户应警惕高估值）
# ============================================================
OFFENSIVE_INDUSTRIES = [
    # 半导体/科技
    '半导体', '芯片', '集成电路', '电子', '消费电子', 'AI', '人工智能',
    # 新能源（含电池/锂电）
    '新能源', '光伏', '锂电', '锂电池', '电池', '动力电池', '风电', '储能',
    # 创新医药
    '生物制品', '医疗器械', 'CXO', '创新药',
    # 汽车（含新能源车）
    '汽车', '汽车整车',
    # 军工
    '军工', '国防', '航天', '航空航天',
    # 互联网/软件
    '软件', '互联网', '云计算', '游戏', '传媒娱乐',
    # 创业板/科创板（一般是成长股集中地）
]


def classify_stock(stock):
    """
    给一只股票分类：防守 / 进攻 / 中性 / 未知

    输入：stock dict，至少包含以下字段（字段名兼容 ai_recommendations / watchlist_signals 等）：
      - code, name
      - industry / category（行业）
      - pe / pe_ttm
      - roe（5 年均值或最新）
      - dividend_yield（股息率%）
      - is_10y_king（可选）
    返回：(category, label, reason)
      category: 'defensive' / 'offensive' / 'neutral' / 'unknown'
      label: '🛡 防守' / '⚔ 进攻' / '⚪ 中性' / '❓ 未知'
      reason: 判定理由
    """
    code = str(stock.get("code", "")).zfill(6)
    industry = (stock.get("industry") or stock.get("category") or "").strip()
    pe = stock.get("pe") or stock.get("pe_ttm") or 0
    roe = stock.get("roe") or 0
    div = stock.get("dividend_yield") or 0

    # ========== ETF 单独处理 ==========
    if code.startswith(('5', '1')):
        try:
            import json, os
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'etf_index_map.json'),
                       encoding='utf-8') as f:
                etf_map = json.load(f).get('map', {})
            etf_info = etf_map.get(code)
            if etf_info:
                kind = etf_info.get('kind', '')
                idx_name = etf_info.get('name', '')
                if kind == 'broad':
                    return 'defensive', '🛡 防守', f'宽基 ETF（{idx_name}）'
                if kind == 'strategy_dividend':
                    return 'defensive', '🛡 防守', f'红利策略 ETF（{idx_name}）'
                if kind == 'sector':
                    return 'offensive', '⚔ 进攻', f'行业 ETF（{idx_name}）'
        except Exception:
            pass
        # 其他 ETF（货币/国债/黄金）
        if any(k in stock.get('name', '') for k in ['货币', '国债', '黄金', '日利', '添益']):
            return 'defensive', '🛡 防守', '现金等价物 / 避险资产'
        return 'neutral', '⚪ 中性', f'未识别 ETF'

    # ========== 个股判定 ==========
    reasons = []
    defensive_score = 0
    offensive_score = 0

    # 1. 行业归属（最重要）
    for kw in DEFENSIVE_INDUSTRIES:
        if kw in industry:
            defensive_score += 3
            reasons.append(f'行业偏防守（{kw}）')
            break
    for kw in OFFENSIVE_INDUSTRIES:
        if kw in industry:
            offensive_score += 3
            reasons.append(f'行业偏成长（{kw}）')
            break

    # 2. 股息率
    if div >= 4:
        defensive_score += 2
        reasons.append(f'高股息 {div:.1f}%')
    elif div <= 1 and div > 0:
        offensive_score += 1
        reasons.append(f'低股息 {div:.1f}%')

    # 3. ROE + PE 组合
    if roe and roe > 25 and pe and pe > 30:
        offensive_score += 2
        reasons.append(f'高 ROE {roe:.0f}% + 高 PE {pe:.0f}')
    elif roe and roe < 15 and div >= 3:
        defensive_score += 1
        reasons.append(f'低 ROE 但股息防御')

    # 4. PE 偏高（成长股特征）
    if pe and pe > 50:
        offensive_score += 2
        reasons.append(f'PE 极高 {pe:.0f}')

    # ========== 综合判定 ==========
    if defensive_score >= 3 and defensive_score > offensive_score:
        return 'defensive', '🛡 防守', '；'.join(reasons[:2])
    if offensive_score >= 3 and offensive_score > defensive_score:
        return 'offensive', '⚔ 进攻', '；'.join(reasons[:2])
    if defensive_score == 0 and offensive_score == 0:
        return 'unknown', '❓ 未知', f'数据不足（行业 {industry or "?"}）'
    return 'neutral', '⚪ 中性', '；'.join(reasons[:2]) if reasons else '介于防守/进攻之间'


def get_classify_summary(stocks):
    """统计一组股票的防守/进攻分布"""
    counts = {'defensive': 0, 'offensive': 0, 'neutral': 0, 'unknown': 0}
    for s in stocks or []:
        cat, _, _ = classify_stock(s)
        counts[cat] = counts.get(cat, 0) + 1
    total = sum(counts.values())
    return {
        'counts': counts,
        'total': total,
        'defensive_pct': round(counts['defensive'] / total * 100, 1) if total else 0,
        'offensive_pct': round(counts['offensive'] / total * 100, 1) if total else 0,
    }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    # 自测
    cases = [
        {'code': '601398', 'name': '工商银行', 'industry': '银行Ⅱ', 'pe': 7, 'roe': 11, 'dividend_yield': 6.4},
        {'code': '600900', 'name': '长江电力', 'industry': '电力', 'pe': 19, 'roe': 13, 'dividend_yield': 4.4},
        {'code': '600519', 'name': '贵州茅台', 'industry': '白酒Ⅱ', 'pe': 25, 'roe': 30, 'dividend_yield': 2.5},
        {'code': '300750', 'name': '宁德时代', 'industry': '电池', 'pe': 35, 'roe': 22, 'dividend_yield': 1.0},
        {'code': '688981', 'name': '中芯国际', 'industry': '半导体', 'pe': 80, 'roe': 5, 'dividend_yield': 0},
        {'code': '510330', 'name': '沪深300etf', 'industry': '', 'pe': 0, 'roe': 0, 'dividend_yield': 2.3},
        {'code': '512890', 'name': '红利低波etf', 'industry': '', 'pe': 8, 'roe': 0, 'dividend_yield': 4.9},
        {'code': '512170', 'name': '中证医疗ETF', 'industry': '', 'pe': 30, 'roe': 0, 'dividend_yield': 0},
        {'code': '518880', 'name': '黄金ETF', 'industry': '', 'pe': 0, 'roe': 0, 'dividend_yield': 0},
        {'code': '000538', 'name': '云南白药', 'industry': '中药Ⅱ', 'pe': 19, 'roe': 12, 'dividend_yield': 4.7},
        {'code': '600276', 'name': '恒瑞医药', 'industry': '生物制品', 'pe': 50, 'roe': 18, 'dividend_yield': 0.5},
    ]
    for c in cases:
        cat, label, reason = classify_stock(c)
        print(f'{label}  {c["code"]} {c["name"]}: {reason}')
