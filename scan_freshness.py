"""
扫描数据新鲜度跟踪（TODO-022 / 2026-04-20 用户提出）

核心问题：4-13、4-19 出现"4420 候选 → 0 推荐"的沉默失败，
用户的持仓股可能已经几天没成功扫描，但模型仍按陈旧数据出信号。

设计：
- 每只股记录"最后成功扫描时间 + 连续失败计数"
- 算"交易日 lag"（不计周末/节假日）：1 天黄 / 2 天红
- Tab 级聚合：1 红 → tab 红 OR 持仓≥2 黄 / 关注≥3 黄 / 候选≥5 黄 → tab 红
- 提供"补漏轮"用的"漏跑列表"API（按 fails 倒序，持仓优先）

数据：scan_freshness.json
{
  "600519": {
    "last_scanned_at": "2026-04-20T19:30:00+08:00",
    "last_signal": "buy_watch",
    "consecutive_fails": 0,
    "first_fail_at": null
  },
  "000538": {
    "last_scanned_at": "2026-04-13T19:30:00+08:00",
    "last_signal": "hold",
    "consecutive_fails": 7,
    "first_fail_at": "2026-04-14T19:30:00+08:00"
  }
}

API：
  log_scan_success(code, signal)       跑成功 → 重置 fails
  log_scan_fail(code)                  跑失败 → fails += 1
  get_freshness(code)                  取某只股新鲜度
  get_stale_stocks(...)                取漏跑列表（补漏轮用）
  get_lag_in_trading_days(code)        算交易日 lag
  get_alert_level(code, kind)          单只股颜色：green/yellow/red
  get_tab_alert_level(stocks)          一组股的 tab 颜色
"""
import json
import os
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FRESHNESS_FILE = os.path.join(SCRIPT_DIR, "scan_freshness.json")
_BEIJING = timezone(timedelta(hours=8))

# 阈值（用户 2026-04-20 拍板）
LAG_YELLOW_DAYS = 1   # 1 个交易日没更新 → 黄
LAG_RED_DAYS = 2      # 2 个交易日没更新 → 红

# Tab 聚合：N 黄 → tab 红
TAB_RED_BY_KIND = {
    'holding': 2,    # 持仓+ETF：≥ 2 黄 → tab 红
    'watchlist': 3,  # 关注：≥ 3 黄 → tab 红
    'candidate': 5,  # 候选：≥ 5 黄 → tab 红
}


# ============================================================
# 基础读写（原子）
# ============================================================

def _now_iso():
    return datetime.now(_BEIJING).isoformat(timespec='seconds')


def _today():
    return datetime.now(_BEIJING).strftime('%Y-%m-%d')


def _load():
    if not os.path.exists(FRESHNESS_FILE):
        return {}
    try:
        with open(FRESHNESS_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data):
    """原子写入"""
    tmp = FRESHNESS_FILE + ".tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, FRESHNESS_FILE)


def _zfill_code(code):
    return str(code).zfill(6)


# ============================================================
# 写入 API
# ============================================================

def log_scan_success(code, signal=None):
    """记录一次成功扫描，重置 fails 计数"""
    code = _zfill_code(code)
    data = _load()
    data[code] = {
        'last_scanned_at': _now_iso(),
        'last_signal': signal,
        'consecutive_fails': 0,
        'first_fail_at': None,
    }
    _save(data)
    return True


def log_scan_fail(code):
    """记录一次失败扫描，fails += 1"""
    code = _zfill_code(code)
    data = _load()
    rec = data.get(code, {
        'last_scanned_at': None,
        'last_signal': None,
        'consecutive_fails': 0,
        'first_fail_at': None,
    })
    rec['consecutive_fails'] = rec.get('consecutive_fails', 0) + 1
    if rec['consecutive_fails'] == 1:
        rec['first_fail_at'] = _now_iso()
    data[code] = rec
    _save(data)
    return rec['consecutive_fails']


def log_scan_batch(success_codes_with_signal, fail_codes):
    """批量记录（性能优化 — 全市场扫描时用）

    Args:
      success_codes_with_signal: [(code, signal), ...]
      fail_codes: [code, ...]
    """
    data = _load()
    now = _now_iso()
    for code, signal in success_codes_with_signal:
        code = _zfill_code(code)
        data[code] = {
            'last_scanned_at': now,
            'last_signal': signal,
            'consecutive_fails': 0,
            'first_fail_at': None,
        }
    for code in fail_codes:
        code = _zfill_code(code)
        rec = data.get(code, {
            'last_scanned_at': None,
            'last_signal': None,
            'consecutive_fails': 0,
            'first_fail_at': None,
        })
        rec['consecutive_fails'] = rec.get('consecutive_fails', 0) + 1
        if rec['consecutive_fails'] == 1:
            rec['first_fail_at'] = now
        data[code] = rec
    _save(data)


# ============================================================
# 读取 API
# ============================================================

def get_freshness(code):
    """取某只股新鲜度信息"""
    code = _zfill_code(code)
    return _load().get(code)


def get_all():
    """取全量 freshness 数据（前端展示用）"""
    return _load()


def get_stale_stocks(min_fails=1, exclude_codes=None,
                       priority_holdings=None, priority_etf=None,
                       priority_watchlist=None,
                       max_count=None,
                       max_lag_hours=None):
    """取漏跑列表（补漏轮用）

    Args:
      min_fails: 至少 fails ≥ 多少才算漏跑（默认 1）
      exclude_codes: 排除的代码集合（已经成功跑过的）
      priority_holdings: 持仓代码列表（优先排前面）
      priority_etf: ETF 代码列表（优先排前面）
      priority_watchlist: 关注代码列表
      max_count: 最多返回多少只（None = 全部）
      max_lag_hours: 2026-04-23 加入 — 按"距今 N 小时未扫"也算漏跑
        背景：GitHub Actions 有时会整段跳过 cron（如昨晚
        21:00/03:00 北京全段跳过），这批股从未被 attempt，
        fails=0 → 永远进不了补漏名单。加个时间兜底。
        None = 不启用（只按 fails）
        推荐值：24（一天一扫）

    Returns: [(code, fails, last_scanned_at), ...] 按优先级排序
    """
    data = _load()
    exclude_codes = set(_zfill_code(c) for c in (exclude_codes or []))
    priority_holdings = set(_zfill_code(c) for c in (priority_holdings or []))
    priority_etf = set(_zfill_code(c) for c in (priority_etf or []))
    priority_watchlist = set(_zfill_code(c) for c in (priority_watchlist or []))

    # 时间阈值：距今超过 max_lag_hours 小时的也算漏跑
    lag_cutoff = None
    if max_lag_hours is not None and max_lag_hours > 0:
        lag_cutoff = datetime.now(_BEIJING) - timedelta(hours=max_lag_hours)

    items = []
    for code, rec in data.items():
        if code in exclude_codes:
            continue
        fails = rec.get('consecutive_fails', 0)

        # 两个入围条件：fails 达标 OR 时间过老
        stale_by_fails = fails >= min_fails
        stale_by_time = False
        last_at_str = rec.get('last_scanned_at')
        if lag_cutoff is not None and last_at_str:
            try:
                last_dt = datetime.fromisoformat(last_at_str)
                if last_dt < lag_cutoff:
                    stale_by_time = True
            except Exception:
                # 解析失败视为"时间未知 → 算老"
                stale_by_time = True
        elif lag_cutoff is not None and not last_at_str:
            stale_by_time = True  # 从未扫过

        if not (stale_by_fails or stale_by_time):
            continue

        # 优先级：持仓+ETF > 关注 > 其它候选；同级别按 fails 倒序
        if code in priority_holdings or code in priority_etf:
            priority = 0  # 最高
        elif code in priority_watchlist:
            priority = 1
        else:
            priority = 2
        items.append((priority, -fails, code, fails, last_at_str))

    items.sort()  # 按 (priority, -fails) 升序 = 优先级高 + fails 大优先
    result = [(code, fails, last_at) for _, _, code, fails, last_at in items]
    if max_count:
        result = result[:max_count]
    return result


# ============================================================
# 交易日 lag 计算
# ============================================================

# 内存缓存的交易日历（避免每只股都拉一次）
_TRADE_DATES_CACHE = {'value': None, 'cached_at': None}


def _get_trade_dates_set():
    """拉交易日历集合（带缓存，避免重复请求）"""
    cache = _TRADE_DATES_CACHE
    # 1 小时缓存
    if cache['value'] is not None and cache['cached_at']:
        cached_dt = datetime.fromisoformat(cache['cached_at'])
        if (datetime.now(_BEIJING) - cached_dt).total_seconds() < 3600:
            return cache['value']

    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        if df is not None and not df.empty:
            dates = set(df['trade_date'].astype(str).str.replace('-', ''))
            cache['value'] = dates
            cache['cached_at'] = _now_iso()
            return dates
    except Exception:
        pass
    return None


def _count_trading_days_between(start_dt, end_dt):
    """算两个 datetime 之间的交易日数（不含周末/节假日）

    fallback：拉不到交易日历时，用"工作日数"近似
    """
    if start_dt >= end_dt:
        return 0

    trade_dates = _get_trade_dates_set()
    start_date = start_dt.date()
    end_date = end_dt.date()

    if trade_dates:
        # 用真实交易日历
        count = 0
        current = start_date + timedelta(days=1)  # 不含 start 日
        while current <= end_date:
            if current.strftime('%Y%m%d') in trade_dates:
                count += 1
            current += timedelta(days=1)
        return count
    else:
        # fallback：算工作日（周一到周五）
        count = 0
        current = start_date + timedelta(days=1)
        while current <= end_date:
            if current.weekday() < 5:
                count += 1
            current += timedelta(days=1)
        return count


def get_lag_in_trading_days(code):
    """算某只股距上次成功扫描的"交易日 lag"

    Returns: int（lag 交易日数）/ None（从未扫过）
    """
    code = _zfill_code(code)
    rec = _load().get(code)
    if not rec or not rec.get('last_scanned_at'):
        return None

    try:
        last_dt = datetime.fromisoformat(rec['last_scanned_at'])
    except Exception:
        return None

    now_dt = datetime.now(_BEIJING)
    return _count_trading_days_between(last_dt, now_dt)


# ============================================================
# 报警颜色判定
# ============================================================

def get_alert_level(code, kind='candidate'):
    """单只股的颜色等级

    Args:
      code: 股票代码
      kind: 'holding' / 'watchlist' / 'candidate'（影响 tab 聚合，但单只判定相同）

    Returns: 'green' / 'yellow' / 'red' / 'unknown'（从未扫过）
    """
    lag = get_lag_in_trading_days(code)
    if lag is None:
        return 'unknown'  # 从未成功扫描过
    if lag >= LAG_RED_DAYS:
        return 'red'
    if lag >= LAG_YELLOW_DAYS:
        return 'yellow'
    return 'green'


def get_tab_alert_level(stocks):
    """一组股的 Tab 整体颜色

    Args:
      stocks: [{'code': xxx, 'kind': 'holding'/'watchlist'/'candidate'}, ...]

    Returns: 'green' / 'yellow' / 'red'

    规则：
      1. 任何 1 红 → tab 红
      2. 同类型 ≥ 阈值 黄 → tab 红
      3. 任何 1 黄 → tab 黄
      4. 全绿 → tab 绿
    """
    yellow_by_kind = {'holding': 0, 'watchlist': 0, 'candidate': 0}
    has_red = False
    has_yellow = False

    for s in stocks:
        code = s.get('code')
        kind = s.get('kind', 'candidate')
        # 持仓 + ETF 都算 holding
        if kind == 'etf':
            kind = 'holding'
        if kind not in yellow_by_kind:
            kind = 'candidate'

        level = get_alert_level(code, kind=kind)
        if level == 'red':
            has_red = True
        elif level == 'yellow':
            has_yellow = True
            yellow_by_kind[kind] += 1

    # 规则 1：任何 1 红 → tab 红
    if has_red:
        return 'red'
    # 规则 2：聚合阈值
    for kind, count in yellow_by_kind.items():
        if count >= TAB_RED_BY_KIND.get(kind, 5):
            return 'red'
    # 规则 3：任何 1 黄
    if has_yellow:
        return 'yellow'
    return 'green'


# ============================================================
# 自检
# ============================================================

if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    # 备份
    backup = open(FRESHNESS_FILE, encoding='utf-8').read() if os.path.exists(FRESHNESS_FILE) else None
    _save({})

    print('=== 写入 API ===')
    log_scan_success('600519', 'buy_watch')
    log_scan_success('000538', 'hold')
    fails = log_scan_fail('510330')
    print(f'  log_scan_fail 510330 → fails = {fails}')
    fails = log_scan_fail('510330')
    print(f'  log_scan_fail 510330 → fails = {fails}')

    print()
    print('=== get_freshness ===')
    r1 = get_freshness('600519')
    r2 = get_freshness('510330')
    r3 = get_freshness('999999')
    print(f'  600519: {r1}')
    print(f'  510330: {r2}')
    print(f'  不存在: {r3}')

    print()
    print('=== get_stale_stocks ===')
    log_scan_fail('000001')
    log_scan_fail('000002')
    log_scan_fail('000002')
    log_scan_fail('000002')

    # 持仓优先 510330
    stale = get_stale_stocks(priority_holdings=['510330'])
    print(f'  按优先级排序: {stale}')

    print()
    print('=== get_alert_level（单只股）===')
    a1 = get_alert_level('600519')
    a2 = get_alert_level('510330')
    print(f'  600519 (刚成功): {a1}')
    print(f'  510330 (从未成功，刚 fail 2 次): {a2}')

    # 模拟 last_scanned_at = 3 天前
    data = _load()
    data['600519']['last_scanned_at'] = (datetime.now(_BEIJING) - timedelta(days=3)).isoformat(timespec='seconds')
    _save(data)
    lag = get_lag_in_trading_days('600519')
    a3 = get_alert_level('600519')
    print(f'  600519 (改成 3 天前): lag = {lag} 交易日, alert = {a3}')

    print()
    print('=== get_tab_alert_level ===')
    # 模拟一个全绿的 tab
    log_scan_success('111111', 'buy')
    log_scan_success('222222', 'buy')
    tab = get_tab_alert_level([
        {'code': '111111', 'kind': 'holding'},
        {'code': '222222', 'kind': 'holding'},
    ])
    print(f'  全绿: {tab}')

    # 模拟 1 红 → tab 红
    data = _load()
    data['111111']['last_scanned_at'] = (datetime.now(_BEIJING) - timedelta(days=5)).isoformat(timespec='seconds')
    _save(data)
    tab = get_tab_alert_level([
        {'code': '111111', 'kind': 'holding'},
        {'code': '222222', 'kind': 'holding'},
    ])
    print(f'  1 红 (111111): {tab}')

    # 恢复
    if backup is not None:
        open(FRESHNESS_FILE, 'w', encoding='utf-8').write(backup)
    else:
        os.remove(FRESHNESS_FILE)
    print()
    print('已恢复原数据')
