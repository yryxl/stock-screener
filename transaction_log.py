"""
H5：持股交易明细记录（2026-04-19 用户提出 / 2026-04-20 强化）

⚠️ 这是用户操作的"永久日志"——所有建仓/加仓/减仓/清仓/分红再投的历史
   ★ 严禁被清空、覆盖、重置
   ★ 接手者/未来 AI 不允许做"清空 transaction_log.json 重新开始"的操作
   ★ 即使误录也只能用 delete_transaction(idx) 删单条
   ★ 这份日志是 AI 分析"用户操作模式"的唯一数据源

用户原话（2026-04-20）："这些交易明细都是要记录下来的，
我的加仓，减仓，清仓等等，不然你后面如何分析我的操作"

数据：transaction_log.json（按时间倒序追加）
   [
     {
       "code": "600519",
       "name": "贵州茅台",
       "date": "2023-05-15",          # 实际交易日
       "action": "buy",                # buy/buy_add/sell_partial/sell_all/dividend
       "price": 1500.0,                # 单价
       "shares": 100,                  # 数量（正数）
       "cash_change": -150100.0,       # 现金变动（含手续费，负=花钱，正=拿回）
       "fee": 100.0,                   # 手续费
       "note": "首次建仓",
       "added_at": "2026-04-19T15:30:00+08:00"  # 记录时间戳
     },
     ...
   ]

用途双重：
  1. 用户：查看每只持仓股的完整交易史
  2. AI：基于历史推算"实际成本均价 / 持有时长 / 累计盈亏"，辅助分析

API：
  log_transaction(...)            记一笔
  get_history(code)               按 code 取历史（最新在前）
  get_summary(code, current_price=None)  当前持有/平均成本/累计盈亏
  get_all_codes()                 所有有过交易的 code
  delete_transaction(idx)         删除某条（误录修正）
"""
import json
import os
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "transaction_log.json")
_BEIJING = timezone(timedelta(hours=8))

# 操作类型
ACTIONS = {
    'buy': '🆕 建仓',
    'buy_add': '➕ 增持',
    'sell_partial': '➖ 减持',
    'sell_all': '🚪 清仓',
    'dividend': '💰 分红再投',
}


# ============================================================
# 基础读写
# ============================================================

def _load():
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def _save(data):
    try:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _now_iso():
    return datetime.now(_BEIJING).isoformat(timespec='seconds')


# ============================================================
# 写入 API
# ============================================================

def log_transaction(code, name, action, price, shares, date=None,
                     fee=0, note=''):
    """记一笔交易

    Args:
      code: 6 位股票代码
      name: 股票名称
      action: 'buy' / 'buy_add' / 'sell_partial' / 'sell_all' / 'dividend'
      price: 成交单价（元）
      shares: 数量（正数；卖出/分红用 action 类型区分方向）
      date: 实际交易日（'YYYY-MM-DD'，默认今天）
      fee: 手续费（元，默认 0）
      note: 备注

    Returns: (ok, msg)
    """
    code = str(code).zfill(6)
    if action not in ACTIONS:
        return False, f'未知 action: {action}（应为 {list(ACTIONS.keys())}）'
    if price <= 0 or shares <= 0:
        return False, '价格和数量必须 > 0'

    if not date:
        date = datetime.now(_BEIJING).strftime('%Y-%m-%d')

    # 现金变动方向：买/分红再投是花钱（负），卖是拿回（正）
    is_buy = action in ('buy', 'buy_add', 'dividend')
    cash_change = -(price * shares + fee) if is_buy else (price * shares - fee)

    rec = {
        'code': code,
        'name': name,
        'date': date,
        'action': action,
        'price': float(price),
        'shares': int(shares),
        'cash_change': round(cash_change, 2),
        'fee': round(float(fee), 2),
        'note': note or '',
        'added_at': _now_iso(),
    }
    data = _load()
    data.append(rec)
    if _save(data):
        return True, f'已记录 {ACTIONS[action]} {name} {shares} 股 @ ¥{price}'
    return False, '保存失败'


def delete_transaction(idx):
    """按索引删除（误录修正）"""
    data = _load()
    if 0 <= idx < len(data):
        rec = data.pop(idx)
        _save(data)
        return True, f'已删除：{rec.get("name")} {rec.get("date")} {ACTIONS.get(rec.get("action"), "")}'
    return False, '索引越界'


# ============================================================
# 读取 API
# ============================================================

def get_history(code):
    """按 code 拉历史（最新在前）"""
    code = str(code).zfill(6)
    data = _load()
    items = [r for r in data if str(r.get('code', '')).zfill(6) == code]
    # 按 date 倒序，同日按 added_at 倒序
    items.sort(key=lambda x: (x.get('date', ''), x.get('added_at', '')), reverse=True)
    return items


def get_all_codes():
    """所有有交易记录的 code 集合"""
    return sorted({str(r.get('code', '')).zfill(6) for r in _load()})


def get_summary(code, current_price=None):
    """汇总某只股的当前状态

    Returns: {
      'shares_held': 当前持有数量,
      'avg_cost': 平均成本（含手续费摊薄）,
      'total_invested': 累计投入（不含已收回），
      'total_received': 累计收回（卖出+分红现金，分红再投不算）,
      'realized_pnl': 已实现盈亏（卖出部分）,
      'unrealized_pnl': 浮盈（如果给 current_price）,
      'first_buy_date': 首次买入日期,
      'last_action_date': 最后操作日期,
      'days_held': 持有天数（从首次买入到今天）,
      'transaction_count': 交易次数,
    }
    """
    items = get_history(code)
    if not items:
        return None

    items_chrono = sorted(items, key=lambda x: (x.get('date', ''), x.get('added_at', '')))

    shares_held = 0
    total_cost = 0.0       # 当前持有部分的累计成本
    total_invested = 0.0    # 累计投入金额（永远累加买入金额）
    total_received = 0.0    # 累计收回金额（卖出实收）
    realized_pnl = 0.0
    avg_cost_running = 0.0  # 滚动平均成本

    for r in items_chrono:
        action = r.get('action', '')
        price = r.get('price', 0)
        shares = r.get('shares', 0)
        fee = r.get('fee', 0)

        if action in ('buy', 'buy_add', 'dividend'):
            cost = price * shares + fee
            total_invested += cost
            # 滚动加权平均成本
            new_total_shares = shares_held + shares
            if new_total_shares > 0:
                avg_cost_running = (avg_cost_running * shares_held + cost) / new_total_shares
            shares_held = new_total_shares
            total_cost = avg_cost_running * shares_held
        elif action == 'sell_partial':
            received = price * shares - fee
            total_received += received
            # 已实现盈亏 = 卖出实收 - 卖出股数 × 平均成本
            realized_pnl += received - avg_cost_running * shares
            shares_held = max(0, shares_held - shares)
            total_cost = avg_cost_running * shares_held
        elif action == 'sell_all':
            received = price * shares - fee
            total_received += received
            realized_pnl += received - avg_cost_running * shares_held
            shares_held = 0
            total_cost = 0
            # 清仓后重置平均成本（再买入会从新算）
            avg_cost_running = 0

    summary = {
        'shares_held': shares_held,
        'avg_cost': round(avg_cost_running, 4) if shares_held > 0 else 0,
        'total_invested': round(total_invested, 2),
        'total_received': round(total_received, 2),
        'realized_pnl': round(realized_pnl, 2),
        'unrealized_pnl': None,
        'first_buy_date': items_chrono[0].get('date') if items_chrono else None,
        'last_action_date': items_chrono[-1].get('date') if items_chrono else None,
        'days_held': None,
        'transaction_count': len(items_chrono),
    }

    if shares_held > 0 and current_price:
        summary['unrealized_pnl'] = round((current_price - avg_cost_running) * shares_held, 2)

    if summary['first_buy_date']:
        try:
            d0 = datetime.strptime(summary['first_buy_date'], '%Y-%m-%d')
            today = datetime.now(_BEIJING).replace(tzinfo=None)
            summary['days_held'] = (today - d0).days
        except Exception:
            pass

    return summary


# ============================================================
# 自检
# ============================================================

if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    print('=== transaction_log 自检 ===')

    # 备份
    backup = None
    if os.path.exists(LOG_FILE):
        backup = open(LOG_FILE, encoding='utf-8').read()

    # 清空测试
    _save([])

    # 模拟茅台 4 笔交易
    log_transaction('600519', '贵州茅台', 'buy', 1500, 100, '2023-05-15', fee=100, note='首次建仓')
    log_transaction('600519', '贵州茅台', 'buy_add', 1400, 50, '2023-09-20', fee=50, note='补仓')
    log_transaction('600519', '贵州茅台', 'sell_partial', 1700, 30, '2024-03-10', fee=80, note='高位减持')
    log_transaction('600519', '贵州茅台', 'dividend', 1600, 5, '2024-06-15', note='分红再投')

    # 汇总
    s = get_summary('600519', current_price=1450)
    print(f"持有：{s['shares_held']} 股")
    print(f"平均成本：¥{s['avg_cost']}")
    print(f"累计投入：¥{s['total_invested']:,.0f}")
    print(f"累计收回：¥{s['total_received']:,.0f}")
    print(f"已实现盈亏：¥{s['realized_pnl']:,.0f}")
    print(f"浮盈（@¥1450）：¥{s['unrealized_pnl']:,.0f}")
    print(f"首次买入：{s['first_buy_date']}")
    print(f"持有天数：{s['days_held']}")
    print(f"交易次数：{s['transaction_count']}")

    print(f"\n所有有交易的 code：{get_all_codes()}")

    # 恢复
    if backup is not None:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write(backup)
        print('\n已恢复原数据')
    else:
        os.remove(LOG_FILE)
        print('\n已清理测试数据')
