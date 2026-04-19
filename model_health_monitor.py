"""
模型健康度监控（REQ-151 + REQ-153）

功能：
1. 计算模型的历史准确率
2. 对比沪深300超额收益
3. 持仓胜率
4. 最大回撤
5. 生成中文健康度报告

使用：
  python model_health_monitor.py         # 计算并打印结果
  python model_health_monitor.py --html  # 生成 HTML 报告
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BEIJING = timezone(timedelta(hours=8))


def _load_json(filename):
    path = os.path.join(SCRIPT_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _load_snapshots():
    """读取所有历史快照"""
    snap_dir = os.path.join(SCRIPT_DIR, 'snapshots')
    if not os.path.exists(snap_dir):
        return []
    snapshots = []
    for f in sorted(os.listdir(snap_dir)):
        if f.endswith('.json'):
            try:
                with open(os.path.join(snap_dir, f), encoding='utf-8') as fp:
                    snap = json.load(fp)
                    snap['_filename'] = f
                    snapshots.append(snap)
            except Exception:
                pass
    return snapshots


# ============================================================
# 指标计算
# ============================================================

def _get_hs300_history():
    """
    拉沪深 300 历史日线（带文件缓存避免反复拉）
    返回：DataFrame[date, close] 或 None
    """
    import os
    cache_path = os.path.join(SCRIPT_DIR, 'hs300_history_cache.json')

    # 缓存判断（24 小时刷一次）
    now_ts = datetime.now(_BEIJING).timestamp()
    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding='utf-8') as f:
                cache = json.load(f)
            if now_ts - cache.get('cached_at', 0) < 86400:
                # 缓存有效
                import pandas as pd
                df = pd.DataFrame(cache.get('data', []))
                if not df.empty:
                    df['date'] = pd.to_datetime(df['date'])
                    return df
        except Exception:
            pass

    # 重新拉
    try:
        import akshare as ak
        import pandas as pd
        df = ak.stock_zh_index_daily_em(symbol='sh000300')
        if df is None or df.empty:
            return None
        # akshare 返回字段：date, open, close, high, low, amount
        df['date'] = pd.to_datetime(df['date'])
        df = df[['date', 'close']].sort_values('date').reset_index(drop=True)

        # 写缓存
        try:
            cache_data = {
                'cached_at': now_ts,
                'data': [{'date': d.strftime('%Y-%m-%d'), 'close': float(c)}
                         for d, c in zip(df['date'], df['close'])]
            }
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False)
        except Exception:
            pass

        return df
    except Exception:
        return None


def _get_hs300_close_at(date_str):
    """取沪深 300 在某日期的收盘价（最近一个交易日）"""
    df = _get_hs300_history()
    if df is None or df.empty:
        return None
    try:
        import pandas as pd
        target = pd.to_datetime(date_str)
        # 取 ≤ target 的最近一条
        df_filtered = df[df['date'] <= target]
        if df_filtered.empty:
            return None
        return float(df_filtered.iloc[-1]['close'])
    except Exception:
        return None


def calc_signal_accuracy(snapshots=None, lookback_months=3):
    """
    REQ-151：计算模型买入信号的准确率
    定义：
      - 某时点给出 buy_* 信号的股票
      - N 个月后价格上涨则算准确（精确逻辑：N 月后价格 > 当时价格）
      - 准确率 = 准确数 / 总推荐数

    返回：
      None - 数据不足
      dict - {'accuracy_pct', 'total_signals', 'success', 'lookback_months', 'detail'}
    """
    if snapshots is None:
        snapshots = _load_snapshots()
    if not snapshots:
        return None

    from datetime import datetime as _dt, timedelta
    now = datetime.now(_BEIJING)

    # 用最新价格作为"当前价"参考（来自 daily_results.json）
    daily = _load_json("daily_results.json") or {}
    current_prices = {}
    for src in ["watchlist_signals", "holding_signals"]:
        for s in daily.get(src, []):
            code = str(s.get('code', '')).zfill(6)
            price = s.get('price')
            if code and price:
                current_prices[code] = price

    if not current_prices:
        return None

    # 遍历快照里的 buy_* 信号
    success = 0
    total = 0
    skipped_no_price = 0
    skipped_too_recent = 0

    for snap in snapshots:
        snap_date_str = snap.get('snapshot_date', '')[:10]
        try:
            snap_date = _dt.strptime(snap_date_str, '%Y-%m-%d')
        except Exception:
            continue

        # 至少经过 lookback_months 个月才能评判
        days_passed = (now.replace(tzinfo=None) - snap_date).days
        if days_passed < lookback_months * 30:
            skipped_too_recent += 1
            continue

        for signal_list_key in ['watchlist_signals', 'recommendations', 'holding_signals']:
            for s in snap.get(signal_list_key, []):
                signal = s.get('signal', '')
                if not signal.startswith('buy_'):
                    continue
                code = str(s.get('code', '')).zfill(6)
                old_price = s.get('price')
                new_price = current_prices.get(code)
                if not old_price or not new_price:
                    skipped_no_price += 1
                    continue
                total += 1
                if new_price > old_price:
                    success += 1

    if total == 0:
        return {
            'accuracy_pct': None,
            'total_signals': 0,
            'success': 0,
            'lookback_months': lookback_months,
            'detail': f'数据积累中：现有 {len(snapshots)} 份快照，'
                      f'其中 {skipped_too_recent} 份太新（不足 {lookback_months} 月），'
                      f'{skipped_no_price} 个信号缺当前价'
        }

    return {
        'accuracy_pct': round(success / total * 100, 1),
        'total_signals': total,
        'success': success,
        'lookback_months': lookback_months,
        'detail': f'{lookback_months} 月窗口内 {success}/{total} 只买入信号股票上涨'
    }


def calc_vs_hs300(snapshots=None):
    """
    REQ-151：对比沪深 300 的超额收益
    定义：
      - 用历史快照里的"持仓组合"模拟"如果当时按模型操作"
      - 计算组合从快照日到现在的累计收益
      - 对比沪深 300 同期收益
      - 超额收益 = 模型组合收益 - 沪深 300 收益

    返回：
      None - 数据不足
      dict - {'alpha_pp', 'model_return_pct', 'hs300_return_pct',
              'snapshots_used', 'detail'}
    """
    if snapshots is None:
        snapshots = _load_snapshots()
    if len(snapshots) < 2:
        return None

    from datetime import datetime as _dt
    now = datetime.now(_BEIJING)

    # 用最新价格
    daily = _load_json("daily_results.json") or {}
    current_prices = {}
    for src in ["watchlist_signals", "holding_signals"]:
        for s in daily.get(src, []):
            code = str(s.get('code', '')).zfill(6)
            price = s.get('price')
            if code and price:
                current_prices[code] = price

    if not current_prices:
        return None

    # 取最早的快照作为基准
    earliest = sorted(snapshots, key=lambda s: s.get('snapshot_date', ''))[0]
    snap_date_str = earliest.get('snapshot_date', '')[:10]
    try:
        snap_date = _dt.strptime(snap_date_str, '%Y-%m-%d')
    except Exception:
        return None

    days_passed = (now.replace(tzinfo=None) - snap_date).days
    if days_passed < 30:
        return {
            'alpha_pp': None,
            'model_return_pct': None,
            'hs300_return_pct': None,
            'snapshots_used': len(snapshots),
            'detail': f'数据积累中：最早快照仅 {days_passed} 天前，不足 1 个月'
        }

    # 模型组合（用最早快照的持仓 + 等权计算收益）
    model_returns = []
    for h in earliest.get('holdings', []):
        code = str(h.get('code', '')).zfill(6)
        old_price = None
        # 从 holding_signals 取当时价格
        for hs in earliest.get('holding_signals', []):
            if str(hs.get('code', '')).zfill(6) == code:
                old_price = hs.get('price')
                break
        new_price = current_prices.get(code)
        if old_price and new_price and old_price > 0:
            model_returns.append((new_price / old_price - 1) * 100)

    if not model_returns:
        return None

    model_return_pct = sum(model_returns) / len(model_returns)

    # 沪深 300 同期
    hs300_old = _get_hs300_close_at(snap_date_str)
    hs300_now = _get_hs300_close_at(now.strftime('%Y-%m-%d'))
    if not hs300_old or not hs300_now:
        return {
            'alpha_pp': None,
            'model_return_pct': round(model_return_pct, 2),
            'hs300_return_pct': None,
            'snapshots_used': len(snapshots),
            'detail': '沪深 300 历史数据拉取失败（可能网络/接口问题）'
        }

    hs300_return_pct = (hs300_now / hs300_old - 1) * 100
    alpha_pp = model_return_pct - hs300_return_pct

    return {
        'alpha_pp': round(alpha_pp, 2),
        'model_return_pct': round(model_return_pct, 2),
        'hs300_return_pct': round(hs300_return_pct, 2),
        'snapshots_used': len(snapshots),
        'detail': f'从 {snap_date_str} 到今 ({days_passed} 天)：'
                  f'模型组合 {model_return_pct:+.1f}% vs 沪深300 {hs300_return_pct:+.1f}%'
    }


def calc_holding_win_rate(holdings_file="holdings.json"):
    """
    持仓胜率：当前持仓里多少只是盈利的。

    2026-04-19 用户提出归因要求：
    只算 attribution='model' 的持仓，避免把"模型上线前持有的股票"算入模型成绩。

    不是真的"胜率"（需要完整交易历史），是当前浮盈比例。
    """
    all_holdings = _load_json(holdings_file)
    if not all_holdings:
        return None

    # 归因过滤：只算模型推荐的持仓
    try:
        from holdings_attribution import filter_model_only, summarize_attribution
        holdings = filter_model_only(all_holdings)
        attribution_summary = summarize_attribution(all_holdings)
    except Exception:
        holdings = all_holdings
        attribution_summary = None

    if not holdings:
        # 当前没有任何"模型推荐"的持仓 → 无法评估模型胜率
        return {
            "wins": 0, "losses": 0, "total": 0, "rate": None,
            "note": "0 只模型推荐持仓（用户未标 attribution=model）",
            "attribution_summary": attribution_summary,
        }

    daily = _load_json("daily_results.json") or {}
    holding_signals = {s.get("code"): s for s in daily.get("holding_signals", [])}

    wins = 0
    losses = 0
    for h in holdings:
        code = str(h.get("code", "")).zfill(6)
        sig = holding_signals.get(code) or holding_signals.get(h.get("code"))
        if not sig:
            continue
        cost = h.get("cost", 0)
        price = sig.get("price", 0)
        if cost > 0 and price > 0:
            if price > cost:
                wins += 1
            else:
                losses += 1

    total = wins + losses
    if total == 0:
        return None
    return {
        "wins": wins, "losses": losses, "total": total,
        "rate": round(wins / total * 100, 1),
        "attribution_summary": attribution_summary,
        "note": (f"基于 {total} 只 attribution=model 的持仓计算"
                 + (f"（共 {attribution_summary['total']} 只持仓里"
                    f" {attribution_summary['unattributed_count']} 只未计入）"
                    if attribution_summary else ''))
    }


def calc_max_drawdown_current(holdings_file="holdings.json"):
    """
    当前持仓最大回撤：所有 attribution=model 持仓里亏得最惨的一只。

    2026-04-19 用户要求：归因过滤后才算（避免 pre_model 的股污染模型评估）
    """
    all_holdings = _load_json(holdings_file)
    if not all_holdings:
        return None

    try:
        from holdings_attribution import filter_model_only
        holdings = filter_model_only(all_holdings)
    except Exception:
        holdings = all_holdings

    if not holdings:
        return {"worst_stock": None, "drawdown_pct": 0,
                "note": "0 只 attribution=model 持仓"}

    daily = _load_json("daily_results.json") or {}
    holding_signals = {s.get("code"): s for s in daily.get("holding_signals", [])}

    worst = None
    worst_pct = 0
    for h in holdings:
        code = str(h.get("code", "")).zfill(6)
        sig = holding_signals.get(code) or holding_signals.get(h.get("code"))
        if not sig:
            continue
        cost = h.get("cost", 0)
        price = sig.get("price", 0)
        if cost > 0 and price > 0:
            pct = (price / cost - 1) * 100
            if pct < worst_pct:
                worst_pct = pct
                worst = h.get("name")

    return {"worst_stock": worst, "drawdown_pct": round(worst_pct, 1)}


def calc_recent_bugs_count():
    """读 TESTING.md 的 Bug 追踪表，返回 (未修复数, 已修复数, 总数)

    BUG-012 修复（2026-04-18）：旧版硬编码 return 1，今天累计修了 10+ 个 bug 都没反映
    新版按 TESTING.md 实际 BUG 表统计：未修复 / 已修复 / 总数
    """
    docs_path = os.path.join(SCRIPT_DIR, 'docs', 'TESTING.md')
    if not os.path.exists(docs_path):
        return {'unfixed': 0, 'fixed': 0, 'total': 0}

    try:
        with open(docs_path, encoding='utf-8') as f:
            content = f.read()
        # 找 BUG 追踪表行（| 2026-XX-XX | BUG-XXX | ... | 状态 |）
        import re
        bug_lines = re.findall(r'^\| 20\d{2}-\d{2}-\d{2} \| BUG-\d+ \|.*$', content, re.MULTILINE)
        total = len(bug_lines)
        # 已修复识别：✅ 后跟"已修复 / 已纠偏 / 已解决 / 已处理"等
        # 黄灯 🟡 不算修复
        fixed = sum(1 for line in bug_lines
                    if '✅' in line and not '🟡' in line)
        unfixed = total - fixed
        return {'unfixed': unfixed, 'fixed': fixed, 'total': total}
    except Exception:
        return {'unfixed': 0, 'fixed': 0, 'total': 0}


# ============================================================
# REQ-151 规则 B：黑天鹅事件检测（2026-04-17 实施）
# ============================================================
# 输入：当前日期
# 输出：当前是否落在某个黑天鹅事件窗口内
# 数据源：black_swan_events.json（已建好，含 6 个历史事件）

def check_black_swan_window(check_date=None):
    """
    REQ-151 规则 B：检测当前日期是否落在黑天鹅事件窗口内

    返回：
      None - 当前不在黑天鹅期
      dict - 当前事件信息：{
        'name': 事件名,
        'impact': 'severe' / 'major' / 'moderate',
        'desc': 描述,
        'action': 建议动作,
        'days_remaining': 距离事件结束天数,
      }
    """
    events_data = _load_json('black_swan_events.json')
    if not events_data:
        return None

    if check_date is None:
        check_date = datetime.now(_BEIJING).strftime('%Y-%m-%d')

    for event in events_data.get('events', []):
        start = event.get('start')
        end = event.get('end')
        if not start or not end:
            continue
        if start <= check_date <= end:
            # 计算距离结束的天数
            from datetime import date
            try:
                check_d = date.fromisoformat(check_date)
                end_d = date.fromisoformat(end)
                days_left = (end_d - check_d).days
            except Exception:
                days_left = None
            return {
                'name': event.get('name'),
                'impact': event.get('impact'),
                'desc': event.get('desc'),
                'action': event.get('market_action_suggested'),
                'days_remaining': days_left,
            }
    return None


# ============================================================
# REQ-151 规则 C：单股持有超 3 年累计负收益（2026-04-17 实施）
# ============================================================
# 注：当前 holdings.json 缺少 buy_date 字段，无法精确判定持有时长
# 暂提供代理实现：如有 buy_date 字段则用，否则跳过该股

def check_long_held_losers(holdings_file="holdings.json"):
    """
    REQ-151 规则 C：识别"持有 > 3 年且累计收益为负"的股票

    2026-04-19 用户要求归因过滤：只看 attribution=model 持仓
    （否则云南白药这种"模型上线前买的"会无辜中枪）

    返回：[
      {'code': xxx, 'name': xxx, 'years_held': X.X, 'pnl_pct': -XX.X},
      ...
    ]
    返回空列表 = 无符合条件的股票
    """
    all_holdings = _load_json(holdings_file)
    if not all_holdings:
        return []

    try:
        from holdings_attribution import filter_model_only
        holdings = filter_model_only(all_holdings)
    except Exception:
        holdings = all_holdings

    daily = _load_json("daily_results.json") or {}
    holding_signals = {s.get("code"): s for s in daily.get("holding_signals", [])}

    losers = []
    now = datetime.now(_BEIJING)
    for h in holdings:
        code = str(h.get("code", "")).zfill(6)
        cost = h.get("cost", 0)
        buy_date = h.get("buy_date")  # 可选字段

        if not buy_date:
            continue  # 缺日期跳过

        try:
            buy_d = datetime.strptime(buy_date, "%Y-%m-%d").replace(tzinfo=_BEIJING)
            years_held = (now - buy_d).days / 365.25
        except Exception:
            continue

        if years_held < 3:
            continue

        sig = holding_signals.get(code) or holding_signals.get(h.get("code"))
        if not sig:
            continue

        price = sig.get("price", 0)
        if cost > 0 and price > 0:
            pnl_pct = (price / cost - 1) * 100
            if pnl_pct < 0:
                losers.append({
                    'code': code,
                    'name': h.get('name', ''),
                    'years_held': round(years_held, 1),
                    'pnl_pct': round(pnl_pct, 1),
                })
    return losers


# ============================================================
# REQ-151 规则 A：连续 3 年跑输沪深 300（2026-04-17 实施 - 框架版）
# ============================================================
# 完整实施需要：3 年的历史推荐快照 + 沪深 300 同期价格
# 当前快照只有 2 周（2026-W14、W15），数据不足
# 故先建框架，数据积累足够后自动激活

def check_consistent_underperform(min_years=3):
    """
    REQ-151 规则 A：检测模型推荐组合是否连续 N 年跑输沪深 300

    数据规则（2026-04-17 修正）：
      - 用户实际规则是"每周一份快照"（snapshots 命名为 YYYY-Www）
      - 判定标准应该是"时间跨度"而非"份数"，避免漏几份就判错
      - 最早快照距今 ≥ min_years 年 → 数据够，可以判定

    返回：
      None - 数据不足无法判定
      dict - {
        'years_underperform': 连续跑输年数,
        'avg_alpha_pp': 平均年化超额收益（pp，负值表示跑输）,
        'should_circuit_break': 是否应触发熔断,
        'data_status': 数据状态描述,
      }
    """
    snapshots = _load_snapshots()
    if not snapshots:
        return {
            'years_underperform': None,
            'avg_alpha_pp': None,
            'should_circuit_break': False,
            'data_status': '快照目录为空，规则待激活',
        }

    # 看最早快照距今多少年（按时间跨度判定）
    from datetime import datetime as _dt
    earliest_date = None
    for snap in snapshots:
        snap_date_str = snap.get('snapshot_date', '')[:10]
        try:
            d = _dt.strptime(snap_date_str, '%Y-%m-%d')
            if earliest_date is None or d < earliest_date:
                earliest_date = d
        except Exception:
            continue

    if earliest_date is None:
        return {
            'years_underperform': None,
            'avg_alpha_pp': None,
            'should_circuit_break': False,
            'data_status': f'快照 {len(snapshots)} 份但日期解析失败',
        }

    now = datetime.now(_BEIJING)
    years_span = (now.replace(tzinfo=None) - earliest_date).days / 365.25

    if years_span < min_years:
        return {
            'years_underperform': None,
            'avg_alpha_pp': None,
            'should_circuit_break': False,
            'data_status': (
                f'快照 {len(snapshots)} 份，最早 {earliest_date.strftime("%Y-%m-%d")} '
                f'（{years_span:.1f} 年前，不足 {min_years} 年）→ 规则待激活'
            ),
        }

    # 数据足够。检查每周快照的覆盖密度（3 年 ≈ 156 周，至少要有 100 份保证不漏太多）
    expected_weekly_snapshots = int(years_span * 52 * 0.7)  # 容忍 30% 缺失
    if len(snapshots) < expected_weekly_snapshots:
        return {
            'years_underperform': None,
            'avg_alpha_pp': None,
            'should_circuit_break': False,
            'data_status': (
                f'时间跨度够（{years_span:.1f} 年），但快照仅 {len(snapshots)} 份，'
                f'缺失过多（应≥{expected_weekly_snapshots} 份）→ 数据不可信'
            ),
        }

    # TODO: 数据足够后，实现真实的逐周回溯计算
    # 1. 取每周快照的推荐组合 → 算周收益 → 累计
    # 2. 取每周快照对应日期的沪深 300 → 算周收益 → 累计
    # 3. 滚动 12 个月窗口，看模型组合是否连续 3 个 12 月窗口都跑输
    return {
        'years_underperform': None,
        'avg_alpha_pp': None,
        'should_circuit_break': False,
        'data_status': (
            f'时间跨度{years_span:.1f}年 + {len(snapshots)}份快照，'
            f'数据已足。逐周回算逻辑待实施'
        ),
    }


def calc_signal_contradictions():
    """
    跑一次逻辑一致性测试，返回发现的矛盾数量。
    """
    try:
        # 导入测试模块
        sys.path.insert(0, os.path.join(SCRIPT_DIR, 'tests'))
        from test_signal_consistency import check_text_consistency

        daily = _load_json("daily_results.json") or {}
        count = 0
        for src in ["ai_recommendations", "watchlist_signals", "holding_signals"]:
            for s in daily.get(src, []):
                ok, _ = check_text_consistency(s.get("signal_text", ""))
                if not ok:
                    count += 1
        return count
    except Exception as e:
        return None


# ============================================================
# 汇总健康度
# ============================================================

def get_health_report():
    """生成完整健康度报告字典"""
    now = datetime.now(_BEIJING)

    report = {
        "检查日期": now.strftime("%Y-%m-%d %H:%M"),
        "检查人": "自动生成",
        "指标": {}
    }

    # 1. 持仓胜率
    wr = calc_holding_win_rate()
    if wr:
        report["指标"]["持仓胜率"] = {
            "值": f"{wr['rate']}%",
            "说明": f"{wr['wins']}只盈利 / {wr['total']}只总持仓",
            "阈值": "≥ 60% 绿灯",
            "状态": "🟢 健康" if wr['rate'] >= 60 else ("🟡 需关注" if wr['rate'] >= 40 else "🔴 警示"),
        }
    else:
        report["指标"]["持仓胜率"] = {
            "值": "数据不足", "说明": "需要持仓和最新信号数据", "状态": "⚪ 未知",
        }

    # 2. 最大回撤
    md = calc_max_drawdown_current()
    if md:
        pct = md.get("drawdown_pct", 0)
        report["指标"]["最大回撤"] = {
            "值": f"{pct:+.1f}%",
            "说明": f"最惨的一只：{md.get('worst_stock', '')}",
            "阈值": "≤ -30% 亮红灯",
            "状态": "🟢 健康" if pct > -10 else ("🟡 需关注" if pct > -30 else "🔴 警示"),
        }
    else:
        report["指标"]["最大回撤"] = {"值": "数据不足", "状态": "⚪ 未知"}

    # 3. 信号矛盾
    contra = calc_signal_contradictions()
    if contra is not None:
        report["指标"]["信号矛盾数"] = {
            "值": f"{contra} 条",
            "说明": "逻辑一致性测试检测出的矛盾文案数量",
            "阈值": "= 0 绿灯",
            "状态": "🟢 健康" if contra == 0 else "🔴 警示",
        }

    # 4. 模型准确率（基于历史快照 + 当前价对比）
    acc = calc_signal_accuracy()
    if acc:
        if acc.get('accuracy_pct') is not None:
            ap = acc['accuracy_pct']
            report["指标"]["3月买入准确率"] = {
                "值": f"{ap}%",
                "说明": acc.get('detail', ''),
                "阈值": "≥ 55% 绿灯, < 40% 红灯",
                "状态": "🟢 健康" if ap >= 55 else ("🟡 需关注" if ap >= 40 else "🔴 警示"),
            }
        else:
            report["指标"]["3月买入准确率"] = {
                "值": "数据积累中",
                "说明": acc.get('detail', ''),
                "阈值": "≥ 55% 绿灯",
                "状态": "⚪ 待数据",
            }
    else:
        report["指标"]["3月买入准确率"] = {
            "值": "数据不足", "说明": "需要历史快照 + daily_results", "状态": "⚪ 未知",
        }

    # 5. 对比沪深300（基于历史快照 + 沪深 300 实时数据）
    vs_hs = calc_vs_hs300()
    if vs_hs:
        if vs_hs.get('alpha_pp') is not None:
            alpha = vs_hs['alpha_pp']
            report["指标"]["跑赢沪深300"] = {
                "值": f"{alpha:+.2f}pp",
                "说明": vs_hs.get('detail', ''),
                "阈值": "> 0 绿灯, < 0 黄灯, 连续 3 年 < 0 红灯",
                "状态": "🟢 健康" if alpha > 0 else "🟡 跑输观察",
            }
        else:
            report["指标"]["跑赢沪深300"] = {
                "值": "数据积累中",
                "说明": vs_hs.get('detail', ''),
                "阈值": "> 0 绿灯",
                "状态": "⚪ 待数据",
            }
    else:
        report["指标"]["跑赢沪深300"] = {
            "值": "数据不足", "说明": "需要历史快照 + 沪深300 价格", "状态": "⚪ 未知",
        }

    # 6. 最近发现的 Bug（BUG-012 修复 2026-04-18：实时统计 TESTING.md）
    bug_stats = calc_recent_bugs_count()
    if isinstance(bug_stats, dict):
        unfixed = bug_stats.get('unfixed', 0)
        fixed = bug_stats.get('fixed', 0)
        total = bug_stats.get('total', 0)
        # 健康度判定：未修复才算问题，已修复都算正常
        if unfixed == 0:
            status = "🟢 健康"
        elif unfixed <= 2:
            status = "🟡 需关注"
        else:
            status = "🔴 警示"
        report["指标"]["已知 Bug 数"] = {
            "值": f"未修复 {unfixed} / 已修复 {fixed} / 总数 {total}",
            "说明": "参见 TESTING.md 的 Bug 追踪表",
            "阈值": "未修复 = 0 绿灯，≤2 黄灯，>2 红灯",
            "状态": status,
        }
    else:
        # 老格式兼容
        report["指标"]["已知 Bug 数"] = {
            "值": f"{bug_stats} 个", "说明": "参见 TESTING.md", "状态": "⚪ 未知"
        }

    # 7. REQ-151 规则 B：黑天鹅事件检测
    swan = check_black_swan_window()
    if swan:
        impact_emoji = {'severe': '🔴', 'major': '🟡', 'moderate': '🟡'}.get(swan.get('impact'), '🟡')
        report["指标"]["黑天鹅状态"] = {
            "值": f"{impact_emoji} {swan['name']}",
            "说明": f"{swan['desc']}（剩余 {swan['days_remaining']} 天结束）",
            "阈值": "无事件 = 绿灯",
            "状态": f"{impact_emoji} 触发：{swan['action']}",
        }
    else:
        report["指标"]["黑天鹅状态"] = {
            "值": "无事件",
            "说明": "当前未落在任何已记录的黑天鹅事件窗口内",
            "阈值": "无事件 = 绿灯",
            "状态": "🟢 健康",
        }

    # 8. REQ-151 规则 C：单股 3 年负收益检测
    losers = check_long_held_losers()
    if losers:
        loser_names = ', '.join(f"{l['name']}({l['pnl_pct']:+.0f}%/{l['years_held']:.0f}年)" for l in losers[:3])
        report["指标"]["长期亏损股"] = {
            "值": f"{len(losers)} 只",
            "说明": loser_names,
            "阈值": "0 只 = 绿灯",
            "状态": "🟡 需复查" if len(losers) <= 2 else "🔴 多股长亏",
        }
    else:
        report["指标"]["长期亏损股"] = {
            "值": "0 只",
            "说明": "（注：依赖 holdings.json 的 buy_date 字段，若无该字段会跳过）",
            "阈值": "0 只 = 绿灯",
            "状态": "🟢 健康",
        }

    # 9. REQ-151 规则 A：连续 3 年跑输沪深 300（框架版）
    underperf = check_consistent_underperform()
    if underperf:
        if underperf.get('should_circuit_break'):
            report["指标"]["连续跑输沪深300"] = {
                "值": f"已 {underperf['years_underperform']} 年",
                "说明": f"年化超额 {underperf['avg_alpha_pp']:+.1f}pp",
                "阈值": "≥ 3 年跑输 → 红灯",
                "状态": "🔴 触发熔断",
            }
        else:
            report["指标"]["连续跑输沪深300"] = {
                "值": "数据积累中",
                "说明": underperf.get('data_status', ''),
                "阈值": "≥ 3 年跑输 → 红灯",
                "状态": "⚪ 待数据",
            }

    # 综合判断
    warnings_count = sum(1 for m in report["指标"].values()
                         if "🔴" in m.get("状态", ""))
    if warnings_count == 0:
        report["总体"] = "🟢 模型健康"
        report["建议"] = "继续按模型信号操作"
    elif warnings_count == 1:
        report["总体"] = "🟡 模型需要关注"
        report["建议"] = "查看告警指标并处理"
    else:
        report["总体"] = "🔴 模型可能失效"
        report["建议"] = "暂停按模型信号操作，回归定投宽基"

    return report


def print_report(report):
    """文本输出"""
    print(f"\n{'='*60}")
    print(f"  📊 模型健康度报告")
    print(f"{'='*60}")
    print(f"  检查日期：{report['检查日期']}")
    print(f"  总体状态：{report['总体']}")
    print(f"  建议动作：{report['建议']}")
    print(f"{'-'*60}")
    for name, data in report["指标"].items():
        print(f"  [{data['状态']}] {name}：{data['值']}")
        if '说明' in data:
            print(f"       说明：{data['说明']}")
        if '阈值' in data:
            print(f"       阈值：{data['阈值']}")
        print()


def generate_html_report(report, output="docs/模型健康度监控.html"):
    """生成中文HTML报告"""
    path = os.path.join(SCRIPT_DIR, output)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    indicators_html = ""
    for name, data in report["指标"].items():
        status = data['状态']
        color = "#27ae60" if "🟢" in status else \
                "#f39c12" if "🟡" in status else \
                "#e74c3c" if "🔴" in status else "#95a5a6"
        indicators_html += f"""
        <div class="indicator" style="border-left-color:{color};">
          <div class="status" style="color:{color};">{status}</div>
          <div class="name">{name}</div>
          <div class="value">{data['值']}</div>
          <div class="desc">{data.get('说明','')}</div>
          <div class="threshold">阈值：{data.get('阈值','无')}</div>
        </div>"""

    # 总体状态颜色
    overall_color = "#27ae60" if "🟢" in report["总体"] else \
                   "#f39c12" if "🟡" in report["总体"] else "#e74c3c"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>模型健康度监控</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  background: #f5f7fa; color: #2c3e50; padding: 20px;
}}
.container {{ max-width: 900px; margin: 0 auto; }}
header {{
  background: linear-gradient(135deg, {overall_color}, #764ba2);
  color: #fff; padding: 40px 30px; border-radius: 12px;
  margin-bottom: 24px;
}}
header h1 {{ font-size: 28px; margin-bottom: 12px; }}
header .meta {{ opacity: 0.95; font-size: 14px; margin-top: 8px; }}
header .overall {{
  font-size: 36px; margin-top: 20px; font-weight: bold;
}}
header .suggestion {{
  background: rgba(255,255,255,0.2); padding: 12px 18px;
  border-radius: 6px; margin-top: 16px; font-size: 16px;
}}
.indicators {{
  display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
}}
.indicator {{
  background: #fff; padding: 20px; border-radius: 8px;
  border-left: 4px solid #667eea;
  box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}}
.indicator .status {{
  font-size: 22px; font-weight: bold; margin-bottom: 8px;
}}
.indicator .name {{
  font-size: 14px; color: #666; margin-bottom: 6px;
}}
.indicator .value {{
  font-size: 28px; font-weight: bold; margin-bottom: 10px;
}}
.indicator .desc {{
  font-size: 13px; color: #555; margin-bottom: 8px;
  padding-bottom: 8px; border-bottom: 1px dashed #ddd;
}}
.indicator .threshold {{
  font-size: 12px; color: #888;
}}
.explain {{
  background: #fff; padding: 24px 28px; border-radius: 10px;
  margin-top: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}}
.explain h2 {{ color: #2c3e50; margin-bottom: 16px; font-size: 20px; }}
.explain h3 {{ color: #555; margin: 16px 0 8px; font-size: 16px; }}
.explain p {{ line-height: 1.7; color: #555; margin-bottom: 8px; }}
.explain ul {{ padding-left: 24px; line-height: 1.9; color: #555; }}
.rule-box {{
  background: #fef9e7; border-left: 4px solid #f39c12;
  padding: 14px 18px; margin: 12px 0; border-radius: 6px;
}}
footer {{
  text-align: center; color: #888; padding: 30px 0; font-size: 13px;
}}
</style>
</head>
<body>
<div class="container">

<header>
  <h1>📊 模型健康度监控</h1>
  <div class="meta">检查日期：{report['检查日期']}</div>
  <div class="overall">{report['总体']}</div>
  <div class="suggestion">建议动作：{report['建议']}</div>
</header>

<div class="indicators">
{indicators_html}
</div>

<div class="explain">
<h2>📖 本页面的使用方法</h2>

<h3>什么时候查？</h3>
<p>建议<b>每月查一次</b>，或者在市场发生重大变化（黑天鹅事件、连续大涨大跌）后查。</p>

<h3>红灯什么意思？</h3>
<div class="rule-box">
  <p>如果任意指标显示 <b>🔴 警示</b>，表示模型可能已经失效。这时：</p>
  <ul>
    <li><b>暂停</b> 按模型信号操作</li>
    <li><b>回归</b> 定投宽基 ETF（沪深 300 + 纳指）</li>
    <li>等所有指标回到绿灯再继续使用模型</li>
  </ul>
</div>

<h3>各指标的含义</h3>
<ul>
  <li><b>持仓胜率</b>：当前持仓中盈利的股票占比。长期低于 40% 说明选股有问题。</li>
  <li><b>最大回撤</b>：当前最惨一只股票的亏损幅度。超过 -30% 要警惕。</li>
  <li><b>3 月买入准确率</b>：模型推荐买入的股票 3 个月后涨幅为正的比例。低于 55% 说明模型预测失效。需历史快照≥ 3 个月才能算。</li>
  <li><b>跑赢沪深 300</b>：模型推荐组合对比沪深 300 的超额收益。短期参考，连续 3 年跑输就应该用指数替代。</li>
  <li><b>信号矛盾数</b>：逻辑一致性测试检测出的文案矛盾数量。应为 0。</li>
  <li><b>已知 Bug 数</b>：TESTING.md 中记录的 bug 数量。</li>
  <li><b>🦢 黑天鹅状态</b>（REQ-151 规则 B）：当前日期是否落在已记录的黑天鹅事件窗口（疫情/政策剧变等）。触发时建议主动降仓。事件配置在 black_swan_events.json。</li>
  <li><b>📉 长期亏损股</b>（REQ-151 规则 C）：持仓中"持有 >3 年且累计收益为负"的股票。需要 holdings.json 里有 buy_date 字段。触发时强制复查"是真错还是假错"。</li>
  <li><b>📊 连续跑输沪深 300</b>（REQ-151 规则 A）：滚动 3 年模型组合 vs 沪深 300。判定标准是<b>时间跨度</b>（最早快照≥ 3 年）+ 密度检查（防止数据残缺误判）。触发时建议暂停模型转定投宽基。</li>
</ul>

<h3>3 条熔断规则的设计依据</h3>
<div class="rule-box">
  <p><b>REQ-151 设计原则</b>：模型基于巴菲特/芒格美股理念 + A 股回测推出，可能"水土不服"。3 条规则各管一种失效模式：</p>
  <ul>
    <li><b>规则 A</b> 处理"模型规则在 A 股某段时间不适用" → 用结果（是否跑赢沪深 300）说话</li>
    <li><b>规则 B</b> 处理"突发黑天鹅" → 时间窗口检测，自动告警</li>
    <li><b>规则 C</b> 处理"个股长期蛀虫" → 强制复查，避免侥幸持有</li>
  </ul>
  <p>详细背景见 docs/REQUIREMENTS.md REQ-151（按 7 要素模板撰写）</p>
</div>

<h3>局限</h3>
<div class="rule-box">
  <p>本监控只是"统计层面"的健康度。以下风险它<b>不能检测</b>：</p>
  <ul>
    <li>政策冲击突然发生（见黑天鹅事件列表）</li>
    <li>整个市场进入 20 年长熊（日本式衰退）</li>
    <li>AI 革命颠覆某行业 ROE 基础</li>
  </ul>
  <p>这些"范式转换"类风险，需要<b>你自己的判断</b>。模型不是神。</p>
</div>
</div>

<footer>
  <p>📊 模型健康度监控 · 自动生成于 {report['检查日期']}</p>
  <p>数据来源：holdings.json / daily_results.json / snapshots/</p>
</footer>

</div>
</body>
</html>"""

    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    return path


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8')
    report = get_health_report()
    print_report(report)

    if "--html" in sys.argv:
        path = generate_html_report(report)
        print(f"\n✅ HTML 报告已生成：{path}")
        print(f"   双击打开即可查看")
