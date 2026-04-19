"""
持仓模型归因（2026-04-19 用户提出）

核心问题：用户在模型上线前买的股票（如云南白药）表现不好，
不应该算到模型成绩头上——这股从来不是模型推荐买的。

3 类归因：
  model      🤖 模型推荐买入（计入模型成绩）
  pre_model  📜 模型上线前持有（不计入）
  manual     ✋ 用户自主决定（不计入）

模型正式上线日：2026-04-15（用户 2026-04-19 拍板）
默认归因：pre_model（保守，宁可少算也不要污染成绩）

用法：
  from holdings_attribution import (
      get_attribution, set_attribution,
      filter_model_only, summarize_attribution
  )
  model_only = filter_model_only(holdings)
  s = summarize_attribution(holdings)  # {'model': 1, 'pre_model': 3, ...}
"""
from typing import List, Dict, Optional


# 模型正式上线日 — 2026-04-15（参数稳定性验证完成日，模型才算"成熟"）
MODEL_LIVE_DATE = '2026-04-15'

ATTRIBUTION_LABELS = {
    'model': '🤖 模型推荐',
    'pre_model': '📜 上线前持有',
    'manual': '✋ 自主决定',
}

ATTRIBUTION_COLORS = {
    'model': '#2e7d32',       # 绿
    'pre_model': '#9e9e9e',   # 灰
    'manual': '#ef6c00',      # 橙
}

# 计入模型成绩的归因
ATTRIBUTED_TO_MODEL = {'model'}


def get_attribution(holding: Dict) -> str:
    """取归因。缺失时按"保守原则"返回 pre_model。"""
    a = holding.get('attribution')
    if a in ('model', 'pre_model', 'manual'):
        return a
    return 'pre_model'


def set_attribution(holding: Dict, attribution: str, note: str = '') -> Dict:
    """改归因（返回修改后的 dict，原对象也修改）"""
    if attribution not in ('model', 'pre_model', 'manual'):
        raise ValueError(f'无效归因：{attribution}')
    holding['attribution'] = attribution
    if note:
        holding['attribution_note'] = note
    return holding


def filter_model_only(holdings: List[Dict]) -> List[Dict]:
    """返回只含"模型推荐"的持仓子集（用于算模型成绩）"""
    return [h for h in holdings if get_attribution(h) in ATTRIBUTED_TO_MODEL]


def summarize_attribution(holdings: List[Dict]) -> Dict:
    """返回 3 类归因的统计

    Returns: {
        'model': X 只,
        'pre_model': X 只,
        'manual': X 只,
        'total': N,
        'model_pct': X.X,
        'attributed_count': X,
        'unattributed_count': X,  # pre_model + manual
    }
    """
    counts = {'model': 0, 'pre_model': 0, 'manual': 0}
    for h in holdings:
        counts[get_attribution(h)] += 1
    total = sum(counts.values())
    return {
        **counts,
        'total': total,
        'model_pct': round(counts['model'] / total * 100, 1) if total else 0,
        'attributed_count': counts['model'],
        'unattributed_count': counts['pre_model'] + counts['manual'],
    }


def auto_classify_by_buy_date(holding: Dict, daily_signals: Optional[List] = None) -> str:
    """根据 buy_date 自动判定归因（迁移工具用）

    规则：
      buy_date < MODEL_LIVE_DATE → pre_model
      buy_date ≥ MODEL_LIVE_DATE 且 daily_signals 含该股 buy 信号 → model
      其它 → manual

    Args:
      holding: 持仓 dict
      daily_signals: 当日 daily_results.json 的信号列表（可选）

    Returns: 'model' / 'pre_model' / 'manual'
    """
    buy_date = holding.get('buy_date', '')
    if not buy_date:
        return 'pre_model'  # 没日期默认保守
    if buy_date < MODEL_LIVE_DATE:
        return 'pre_model'

    # 上线后买入：检查当时是否有 buy 信号
    if daily_signals:
        code = str(holding.get('code', '')).zfill(6)
        for s in daily_signals:
            if str(s.get('code', '')).zfill(6) == code:
                sig = s.get('signal', '')
                if sig and sig.startswith('buy'):
                    return 'model'
                break
    # 上线后买但没 buy 信号 / 没数据 → 算用户自主决定
    return 'manual'


def migrate_holdings(holdings: List[Dict], dry_run: bool = False) -> Dict:
    """一次性迁移：给所有持仓打上 attribution

    Args:
      holdings: 持仓列表（会被修改，除非 dry_run）
      dry_run: True = 不修改，只返回会做什么

    Returns: 迁移摘要 {migrated: X, already_set: X, by_class: {...}}
    """
    migrated = 0
    already = 0
    by_class = {'model': 0, 'pre_model': 0, 'manual': 0}

    for h in holdings:
        if 'attribution' in h:
            already += 1
            by_class[get_attribution(h)] += 1
        else:
            new_attr = auto_classify_by_buy_date(h)
            if not dry_run:
                h['attribution'] = new_attr
            by_class[new_attr] += 1
            migrated += 1

    return {
        'migrated': migrated,
        'already_set': already,
        'by_class': by_class,
        'total': len(holdings),
    }


# ============================================================
# 自检
# ============================================================
if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    test = [
        {'code': '000538', 'name': '云南白药'},  # 缺 attribution → pre_model
        {'code': '600519', 'name': '茅台', 'attribution': 'model'},
        {'code': '000333', 'name': '美的', 'attribution': 'manual',
         'attribution_note': '看好家电板块自己买的'},
        {'code': '510330', 'name': '沪深300', 'buy_date': '2024-10-01'},  # 上线前
        {'code': '601398', 'name': '工行', 'buy_date': '2026-05-01'},     # 上线后
    ]

    print('=== get_attribution 测试 ===')
    for t in test:
        print(f'  {t.get("code")} {t.get("name")} → {get_attribution(t)}')

    print('\n=== summarize_attribution 测试 ===')
    s = summarize_attribution(test)
    for k, v in s.items():
        print(f'  {k}: {v}')

    print('\n=== filter_model_only 测试 ===')
    only = filter_model_only(test)
    print(f'  模型推荐持仓：{len(only)} 只')
    for h in only:
        print(f'    - {h.get("code")} {h.get("name")}')

    print('\n=== auto_classify_by_buy_date 测试 ===')
    for t in test:
        cls = auto_classify_by_buy_date(t)
        print(f'  {t.get("code")} {t.get("name")} (buy_date={t.get("buy_date","无")}) → {cls}')

    print('\n=== migrate_holdings dry-run ===')
    test2 = [{'code': '000538', 'name': '云南白药'},
             {'code': '600519', 'name': '茅台', 'attribution': 'model'}]
    r = migrate_holdings(test2, dry_run=True)
    print(f'  迁移摘要：{r}')
    assert 'attribution' not in test2[0], 'dry_run 不该修改'
    print('  ✅ dry_run 正确不修改原数据')
