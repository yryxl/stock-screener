"""
主程序 - 支持多种运行模式
--mode holdings   持仓检查（中午+收盘后）
--mode watchlist  关注表检查（收盘后）
--mode full       AI全市场扫描（凌晨）
--mode send_ai    发送AI推荐结果（早上9点）
--mode holiday    休市日消息
--mode all        全部运行
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

# GitHub Actions 默认 UTC，统一用北京时间
_BEIJING = timezone(timedelta(hours=8))


def beijing_now():
    """返回北京时间的 datetime"""
    return datetime.now(_BEIJING)

import pandas as pd
import numpy as np
import yaml

from screener import (
    screen_all_stocks, check_holdings_sell_signals,
    get_pe_signal, check_decline_signals,
    check_watchlist_financial_health, check_fundamental_health,
    check_position_sizes, compare_opportunity_cost,
)
from notifier import send_daily_report, send_msg, get_access_token, send_urgent_alert
from market_temperature import get_realtime_market_temperature
from etf_monitor import scan_and_update_holdings_etfs, classify_portfolio
from data_fetcher import (
    get_realtime_quotes, get_pe_ttm, get_dividend_yield, safe_fetch,
    get_financial_indicator, extract_annual_data, get_stock_industry,
)
import akshare as ak


def load_config():
    path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_json(filename):
    path = os.path.join(os.path.dirname(__file__), filename)
    if not os.path.exists(path):
        return [] if filename.endswith(".json") else {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data


def save_json(filename, data):
    """原子写入：先写 .tmp 再 rename，防止部分写入污染（2026-04-19 修 BUG-018）

    特殊保护：filename == 'daily_results.json' 且看起来是"全量覆盖"时，
    自动调 save_daily_results_safely 做缩水检查（避免超时截断丢数据）
    """
    # daily_results.json 特殊保护
    if filename == "daily_results.json" and isinstance(data, dict):
        # 只对"完整 daily 字典"启用保护（含 date+mode 是完整 daily）
        if 'date' in data and 'mode' in data:
            # 走保护路径，失败则降级
            try:
                if save_daily_results_safely(data):
                    return  # 写入成功
                # 保护拒绝（数据缩水）→ 不覆盖文件
                print(f"  ⚠ save_json('{filename}') 被保护拒绝，已保留旧数据")
                return
            except Exception as _e:
                # 保护逻辑出错时，降级到普通写入避免数据完全丢失
                print(f"  ⚠ save_daily_results_safely 异常({_e})，降级普通写入")

    # 通用原子写入
    path = os.path.join(os.path.dirname(__file__), filename)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp_path, path)


def save_daily_results_safely(daily_data):
    """专门保护 daily_results.json：写入前做字段校验，缺关键字段时拒绝覆盖

    背景：2026-04-19 19:25 GitHub Actions 45min 超时，daily_results 被截断写入
    后只剩 etf_signals=6，holding/watchlist/recommendations 全是 0 → 前端崩溃。

    保护规则：
      1. 校验关键字段是否存在
      2. 如果新数据明显比旧数据少（如旧 watchlist_signals=11 而新=0）→ 拒绝覆盖
      3. 写入失败时保留 .bak 备份

    Args:
      daily_data: 待写入的 daily_results dict

    Returns: True=写入成功, False=被保护拒绝
    """
    import shutil
    path = os.path.join(os.path.dirname(__file__), "daily_results.json")

    # 加载旧数据做对比
    old = load_json("daily_results.json") if os.path.exists(path) else {}

    # 字段校验
    must_keys = ['date', 'mode']
    for k in must_keys:
        if k not in daily_data:
            print(f"  ⚠ daily_results 校验失败：缺字段 {k}，拒绝覆盖")
            return False

    # 数据缩水检查（防止超时被截断）
    if isinstance(old, dict) and old:
        for sig_key in ['holding_signals', 'watchlist_signals', 'ai_recommendations']:
            old_count = len(old.get(sig_key, []) or [])
            new_count = len(daily_data.get(sig_key, []) or [])
            # 旧的 ≥3 项但新的为 0 → 大概率是数据丢失，拒绝
            if old_count >= 3 and new_count == 0:
                print(f"  ⚠ daily_results 缩水：{sig_key} 从 {old_count} → 0，拒绝覆盖")
                print(f"  💡 这通常是 GitHub Actions 超时导致的部分数据丢失")
                return False

    # 备份旧数据
    if os.path.exists(path):
        try:
            shutil.copy(path, path + ".bak")
        except Exception:
            pass

    # 原子写入新数据（不调 save_json 防止递归）
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(daily_data, f, ensure_ascii=False, indent=2, default=str)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp_path, path)
    print(f"  ✅ daily_results 安全写入完成")
    return True


def is_trading_day():
    """通过AKShare交易日历判断今天是否交易日（考虑节假日）

    2026-04-20 修 BUG-019：原实现接口返回的 df 不含今天日期时直接返回 False，
    导致 2026 年后的所有日期都被误判"休市"（接口数据只更新到 2025 年底）。
    现修为：超出接口数据范围时降级到"周末判断"（更符合实际）。
    """
    today_str = beijing_now().strftime("%Y%m%d")
    today_weekday = beijing_now().weekday()

    # 周末（周六/日）肯定休市，无需查接口
    if today_weekday >= 5:
        return False

    try:
        df = safe_fetch(ak.tool_trade_date_hist_sina)
        if df is not None and not df.empty:
            trade_dates = set(df["trade_date"].astype(str).str.replace("-", ""))

            # ★ BUG-019 修复：检查接口数据是否覆盖到今天
            max_date = max(trade_dates) if trade_dates else "00000000"
            if today_str > max_date:
                # 接口数据没更新到今天 → 不可信，降级到工作日判断
                # 宁可误判工作日为"交易日"（最多多跑一次扫描，无副作用）
                # 也别误判工作日为"休市"（导致用户收"休市"消息+扫描跳过）
                print(f"  ⚠ 交易日历只到 {max_date}，今天 {today_str} 超范围，按工作日处理")
                return True

            return today_str in trade_dates
    except Exception as e:
        print(f"  获取交易日历失败: {e}")
    # 失败时回退到简单判断（排除周末）
    return today_weekday < 5


def update_watchlist_industries(watchlist):
    updated = False
    for stock in watchlist:
        if stock.get("industry_auto") and len(stock["industry_auto"]) > 0:
            continue
        industry = get_stock_industry(stock["code"])
        if industry:
            stock["industry_auto"] = industry
            updated = True
        time.sleep(0.05)
    return updated


def check_watchlist(config, quotes_df):
    """关注表：只给买入方向信号，PE>=合理区间=观望"""
    watchlist = load_json("watchlist.json")
    if not watchlist:
        return [], watchlist

    print(f"检查关注表（{len(watchlist)}只）...")
    if update_watchlist_industries(watchlist):
        save_json("watchlist.json", watchlist)

    signals = []
    for stock in watchlist:
        code = stock["code"]
        name = stock.get("name", code)
        category = stock.get("industry_auto", "") or stock.get("category", "")

        if quotes_df is None or quotes_df.empty:
            continue
        row = quotes_df[quotes_df["代码"] == code]
        if row.empty:
            continue
        row = row.iloc[0]
        price = pd.to_numeric(row.get("最新价"), errors="coerce")
        price_val = price if not pd.isna(price) else 0

        # 获取PE(TTM)
        pe = None
        ttm_data = get_pe_ttm(code)
        if ttm_data and ttm_data.get("pe_ttm"):
            pe = ttm_data["pe_ttm"]
        else:
            pe = pd.to_numeric(row.get("市盈率-动态"), errors="coerce")
        pe_val = pe if (pe and not pd.isna(pe)) else 0

        # 获取股息率
        div_yield = get_dividend_yield(code, price_val, industry=category)

        # ============================================
        # 第一步：清单筛选（决定买不买）
        # ============================================
        signal, signal_text = get_pe_signal(pe_val, category)

        # 关注表：PE>=合理区间一律"观望"（关注表只给买入方向信号）
        # 但 signal_text 要保留"为什么观望"的解释，不能只说"继续观望"
        if signal and ("sell" in signal or signal == "hold"):
            from screener import match_industry_pe
            pe_range = match_industry_pe(category)
            fl, fh = pe_range["fair_low"], pe_range["fair_high"]
            if pe_val > fh:
                signal_text = f"PE={pe_val:.1f}，超出合理区间{fl}-{fh}，等待回落"
            elif pe_val >= fl:
                signal_text = f"PE={pe_val:.1f}，处于合理区间{fl}-{fh}，等待更低点"
            else:
                signal_text = f"PE={pe_val:.1f}，继续观望"
            signal = "hold"

        # 买入信号需过清单：ROE+财务+基本面
        if signal and "buy" in signal:
            # 清单1：ROE+杠杆
            health_ok, health_warning, roe_level = check_watchlist_financial_health(code, industry=category)
            signal_cap = {"heavy": "buy_heavy", "light": "buy_light", "watch": "buy_watch", "none": "hold"}
            max_signal = signal_cap.get(roe_level, "buy_light")
            signal_rank = {"buy_heavy": 0, "buy_medium": 1, "buy_light": 2, "buy_watch": 3, "hold": 4}

            if signal_rank.get(signal, 4) < signal_rank.get(max_signal, 4):
                signal = max_signal
                level_names = {"buy_light": "轻仓", "buy_watch": "关注", "buy_medium": "中仓", "hold": "观望"}
                signal_text = f"PE偏低，ROE限制最高{level_names.get(max_signal, '轻仓')}"

            if roe_level == "none":
                signal = "hold"
                signal_text = "PE偏低但ROE不达标，继续观望"

            # 清单2：财务风险
            if signal and "buy" in signal and not health_ok:
                signal = "hold"
                signal_text = f"财务风险({health_warning})，继续观望"

            # 清单3：基本面恶化（传入真实 PE 和 PB）
            if signal and "buy" in signal:
                real_pb = None
                if quotes_df is not None and not quotes_df.empty and "市净率" in quotes_df.columns:
                    _qrow = quotes_df[quotes_df["代码"] == code]
                    if not _qrow.empty:
                        _pb_val = pd.to_numeric(_qrow.iloc[0].get("市净率"), errors="coerce")
                        if not pd.isna(_pb_val):
                            real_pb = float(_pb_val)
                is_healthy, problems = check_fundamental_health(code, pe=pe_val, pb=real_pb)
                if is_healthy is not None and not is_healthy:
                    signal = "hold"
                    signal_text = f"基本面恶化({','.join(problems[:2])})，继续观望"

        # ============================================
        # 第二步：打分（仅展示+排序，不改变信号）
        # ============================================
        # 打分（仅展示+排序）
        from data_fetcher import get_financial_indicator
        df_indicator = get_financial_indicator(code)
        score_data = {}
        total_score = 0

        if df_indicator is not None:
            df_annual = extract_annual_data(df_indicator, years=10)
            if not df_annual.empty:
                from scorer import score_stock_for_display
                # 传入 div_yield（已经算好的真实股息率）给 scorer 使用
                # 这样同花顺数据源的银行/铁路股也能拿到股息率分数
                score_data = score_stock_for_display(
                    code, df_annual,
                    pe=pe_val, price=price_val, industry=category,
                    external_div_yield=div_yield,
                )
                total_score = score_data.get("total_score", 0)

        # 提取核心财务指标原始值（前端展示用）
        roe_val = None
        gm_val = None
        debt_val = None
        if df_indicator is not None:
            _df_a = extract_annual_data(df_indicator, years=3) if 'df_annual' not in dir() or df_annual.empty else df_annual
            if not _df_a.empty:
                _latest = _df_a.iloc[0]
                _r = _latest.get("roe")
                if _r is not None and not pd.isna(_r):
                    roe_val = round(float(_r), 1)
                _g = _latest.get("gross_margin")
                if _g is not None and not pd.isna(_g):
                    gm_val = round(float(_g), 1)
                _d = _latest.get("debt_ratio")
                if _d is not None and not pd.isna(_d):
                    debt_val = round(float(_d), 1)

        signals.append({
            "code": code, "name": name, "category": category,
            "note": stock.get("note", ""),
            "price": price_val, "pe": pe_val,
            "signal": signal, "signal_text": signal_text,
            "total_score": total_score,
            "dividend_yield": div_yield,
            "dimensions": score_data.get("dimensions", {}),
            "roe": roe_val,
            "gross_margin": gm_val,
            "debt_ratio": debt_val,
        })

    # 通过清单的股票按总分排序（分高的排前面）
    signals.sort(key=lambda x: x.get("total_score", 0), reverse=True)
    return signals, watchlist


def send_simple_msg(config, text):
    """发送简单文本消息"""
    wx = config["wechat"]
    if wx["appid"] == "YOUR_APPID":
        print(f"微信未配置: {text}")
        return
    access_token = get_access_token(wx["appid"], wx["appsecret"])
    if access_token:
        send_msg(access_token, wx["openid"], wx["template_id"], text)


def get_market_date():
    """获取大盘最新交易日期（判断数据是哪天的）"""
    try:
        quotes = get_realtime_quotes()
        if quotes is not None and not quotes.empty:
            # 用上证指数或任意一只股票的日期
            return beijing_now().strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def get_data_info():
    """获取已有数据的时间信息"""
    results = load_json("daily_results.json")
    if not results or not isinstance(results, dict):
        return None, None, False  # 无数据
    date_str = results.get("date", "")
    mode_str = results.get("mode", "")
    if not date_str:
        return None, None, False
    return date_str, mode_str, True


def should_run_and_update(mode, new_data=None):
    """
    判断是否需要运行/更新数据：
    1. 无数据 → 必须跑
    2. 有数据 → 比较时间，只保留更新的
    返回: (should_run: bool, reason: str)
    """
    date_str, last_mode, has_data = get_data_info()

    if not has_data:
        return True, "无历史数据，首次运行"

    print(f"  已有数据: {date_str} (模式:{last_mode})")

    # send_ai 总是执行（只是发消息，不更新数据）
    if mode == "send_ai":
        return True, "发送消息"

    # holdings和watchlist允许一天多次，但间隔至少3小时
    if mode in ("holdings", "watchlist"):
        try:
            last_time = datetime.strptime(date_str[:16], "%Y-%m-%d %H:%M")
            diff_hours = (beijing_now() - last_time).total_seconds() / 3600
            if diff_hours < 3 and last_mode == mode:
                return False, f"{mode}模式{diff_hours:.1f}小时前刚跑过"
        except Exception:
            pass
        return True, f"距上次{mode}超过3小时"

    # full模式：同一天只跑一次
    if mode == "full":
        today = beijing_now().strftime("%Y-%m-%d")
        if date_str[:10] == today and last_mode == "full":
            return False, "今天已跑过全盘扫描"
        return True, "需要全盘扫描"

    return True, "需要更新"


def merge_daily_data(existing, new_data):
    """
    合并数据：比较时间戳，保留更新的
    各字段独立比较，新数据只覆盖有内容的字段
    """
    if not existing or not isinstance(existing, dict):
        return new_data

    # 新数据的各字段：只有非空才覆盖
    for key in ["watchlist_signals", "holding_signals", "ai_recommendations",
                "position_warnings", "swap_suggestions"]:
        if key in new_data and new_data[key]:
            existing[key] = new_data[key]

    # 时间和模式始终用最新的
    existing["date"] = new_data.get("date", existing.get("date", ""))
    existing["mode"] = new_data.get("mode", existing.get("mode", ""))
    existing["data_source"] = new_data.get("data_source", existing.get("data_source", ""))
    existing["is_trading_day"] = new_data.get("is_trading_day", existing.get("is_trading_day"))

    return existing


def get_mode():
    """从命令行获取运行模式"""
    for i, arg in enumerate(sys.argv):
        if arg == "--mode" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    if "--force" in sys.argv:
        return "all"
    return "all"


def run_holdings(config):
    """持仓检查"""
    print("=== 持仓检查 ===")
    holdings = load_json("holdings.json")
    if not holdings:
        print("  无持仓")
        return []

    # 读出已注入的大盘温度，传给 check_holdings_sell_signals 用于"牛顶减仓提醒"
    existing = load_json("daily_results.json")
    market_temp_level = 0
    if isinstance(existing, dict):
        mt = existing.get("market_temperature") or {}
        market_temp_level = mt.get("level", 0)

    holding_signals = check_holdings_sell_signals(
        holdings, config, market_temp_level=market_temp_level
    )
    print(f"  信号: {len(holding_signals)}只")
    return holding_signals


def run_watchlist(config):
    """关注表检查"""
    print("=== 关注表检查 ===")
    quotes_df = get_realtime_quotes()
    watchlist_signals, _ = check_watchlist(config, quotes_df)
    w_buy = sum(1 for s in watchlist_signals if s.get("signal") and "buy" in s["signal"])
    print(f"  买入信号: {w_buy}只")
    return watchlist_signals


def run_full_scan(config):
    """全市场扫描"""
    print("=== 全市场扫描 ===")
    candidates = screen_all_stocks(config)
    ai_recs = [s for s in candidates if s.get("signal") and s["signal"] not in ("hold", None)]
    save_json("market_scan_cache.json", {
        "date": beijing_now().strftime("%Y-%m-%d"),
        "ai_recommendations": ai_recs,
    })
    print(f"  AI推荐: {len(ai_recs)}只")

    # TODO-021（2026-04-20 用户要求）：扫描结果合理性校验
    # 防"沉默失败"——4-13/4-19 那种 4420 只 → 0 推荐的异常被静悄悄当结果用
    try:
        from scan_validator import (validate_scan_result, log_anomaly,
                                      set_retry_pending, get_recent_anomalies)
        prev_anomalies = get_recent_anomalies(days=2)
        prev_recs = None
        for a in reversed(prev_anomalies):
            if a.get('recommendations_count') is not None:
                prev_recs = a['recommendations_count']
                break
        is_ok, level, msg, action = validate_scan_result(
            len(candidates), len(ai_recs), prev_recommendations=prev_recs
        )
        emoji = {'green': '🟢', 'yellow': '🟡', 'red': '🔴'}[level]
        print(f"  {emoji} 扫描健康度：{msg}")

        # 异常处理
        if action in ('warn', 'retry'):
            log_anomaly({
                'level': level,
                'candidates_count': len(candidates),
                'recommendations_count': len(ai_recs),
                'message': msg,
                'action': action,
            })
            # 严重异常 → 推送 + 标记重跑
            if action == 'retry':
                # 微信报警
                try:
                    alert_msg = (
                        f"🚨 模型扫描异常\n"
                        f"候选 {len(candidates)} 只，推荐 {len(ai_recs)} 只\n"
                        f"{msg}\n"
                        f"系统已标记自动重跑（下次 cron 触发时跑 mode=all）"
                    )
                    send_simple_msg(config, alert_msg)
                except Exception as _e2:
                    print(f"  ⚠ 报警推送失败：{_e2}")
                # 标记重跑（受 24h 内 2 次上限保护）
                ok2, msg2 = set_retry_pending(
                    msg, candidates_count=len(candidates),
                    recommendations_count=len(ai_recs)
                )
                print(f"  📌 重跑标记：{msg2}")
    except Exception as e:
        print(f"  ⚠ 扫描合理性校验异常（不影响主流程）: {e}")

    # TODO-045（2026-04-18 用户要求）：自动加入关注表
    # 规则："基本面全过 + 护城河完好 + 价格不合适"的股自动入关注表
    # 不重复加用户已手动加的；不删旧的（可能临时不达标但下次又过）
    try:
        auto_add_to_watchlist(candidates)
    except Exception as e:
        print(f"  ⚠ 自动加入关注表失败: {e}")

    return ai_recs


def auto_add_to_watchlist(candidates, max_new_per_day=10):
    """
    TODO-045 / TODO-047 自动加入关注表

    2026-04-18 重构（TODO-047）：
      - 加到 watchlist_model.json（不是旧的 watchlist.json）
      - 跳过已在 my/toohard/blacklist 表的（用户已知）
      - 黑名单到期自动清理 + 不重复加

    规则：
      - 基本面全过（passed=True）
      - 价格不合适（signal in 'buy_watch'/'sell_watch'/'hold'，非买入信号）
      - 质量过关（is_10y_king 或 is_good_quality）
      - 限制每天最多新增 max_new_per_day 只（按 total_score 排序）

    旧的自动加入项保留（不自动删）：可能下次扫描又达标
    """
    if not candidates:
        return

    try:
        from watchlist_manager import (cleanup_expired_blacklist,
                                        get_all_blocked_codes,
                                        add_to_model, get_summary)
    except ImportError:
        # fallback 到旧逻辑（兼容性）
        return _auto_add_to_watchlist_legacy(candidates, max_new_per_day)

    # 黑名单到期清理
    expired = cleanup_expired_blacklist()
    if expired:
        print(f"  🔓 黑名单到期清理 {expired} 只")

    # 跳过：持仓 + 已在 my/toohard/未到期黑名单
    holdings = load_json("holdings.json") or []
    blocked = get_all_blocked_codes()
    for h in holdings:
        blocked.add(str(h.get("code", "")).zfill(6))

    # 筛选符合条件的候选
    eligible = []
    for c in candidates:
        if not c.get("passed"):
            continue
        signal = c.get("signal", "")
        if signal not in ("buy_watch", "sell_watch", "hold", "hold_keep"):
            continue
        if not (c.get("is_10y_king") or c.get("is_good_quality")):
            continue
        code = str(c.get("code", "")).zfill(6)
        if code in blocked:
            continue
        eligible.append(c)

    # 按总分排序，取 top N
    eligible.sort(key=lambda x: -(x.get("total_score") or 0))
    new_picks = eligible[:max_new_per_day]

    today = beijing_now().strftime("%Y-%m-%d")
    added_count = 0
    for c in new_picks:
        code = str(c.get("code", "")).zfill(6)
        signal_text = c.get("signal_text", "")[:30]
        stock = {
            "code": code,
            "name": c.get("name", code),
            "category": c.get("industry") or c.get("category", ""),
            "note": f"自动添加 {today}：{signal_text}",
            "total_score": c.get("total_score"),
        }
        ok, _msg = add_to_model(stock)
        if ok:
            added_count += 1

    s = get_summary()
    if added_count > 0:
        print(f"  📌 自动加入模型推荐表: {added_count} 只 / 当前共 {s['model']} 只")
    else:
        print(f"  📌 自动加入模型推荐表: 0 只新增（model {s['model']} / toohard {s['toohard']} / my {s['my']} / 黑名单 {s['blacklist']}）")


def _auto_add_to_watchlist_legacy(candidates, max_new_per_day=10):
    """旧版兼容：仍写到 watchlist.json"""
    if not candidates:
        return

    holdings = load_json("holdings.json") or []
    watchlist = load_json("watchlist.json") or []

    # 已存在的代码集合（避免重复）
    existing_codes = set()
    for h in holdings:
        existing_codes.add(str(h.get("code", "")).zfill(6))
    for w in watchlist:
        existing_codes.add(str(w.get("code", "")).zfill(6))

    # 筛选符合条件的候选
    eligible = []
    for c in candidates:
        if not c.get("passed"):
            continue
        signal = c.get("signal", "")
        if signal not in ("buy_watch", "sell_watch", "hold", "hold_keep"):
            continue
        if not (c.get("is_10y_king") or c.get("is_good_quality")):
            continue
        code = str(c.get("code", "")).zfill(6)
        if code in existing_codes:
            continue
        eligible.append(c)

    eligible.sort(key=lambda x: -(x.get("total_score") or 0))
    new_picks = eligible[:max_new_per_day]

    today = beijing_now().strftime("%Y-%m-%d")
    for c in new_picks:
        code = str(c.get("code", "")).zfill(6)
        signal_text = c.get("signal_text", "")[:30]
        watchlist.append({
            "code": code,
            "name": c.get("name", code),
            "category": c.get("industry") or c.get("category", ""),
            "note": f"自动添加 {today}：{signal_text}",
            "auto_added": True,
            "auto_added_date": today,
            "total_score": c.get("total_score"),
        })

    if new_picks:
        save_json("watchlist.json", watchlist)
        print(f"  📌 自动加入关注表: {len(new_picks)} 只（基本面全过+质量过关+价格未到位）")
        for p in new_picks[:5]:
            print(f"     - {p.get('name','?')} ({p.get('code','')}) 总分 {p.get('total_score','?')}")
        if len(new_picks) > 5:
            print(f"     ... 等共 {len(new_picks)} 只")
    else:
        print(f"  📌 自动加入关注表: 0 只（无新候选符合条件）")


def _check_urgent_alerts(config, now, trading):
    """独立紧急通道（REQ-205，2026-04-24）

    F-4 把所有非 send_ai cron 的推送都禁了，代价是持仓紧急卖出信号不再
    实时推。这个函数补上紧急通道：
      - 持仓里 must_sell=True（基本面恶化/护城河松动）→ 立即推
      - 持仓命中 black_swan_filter.company_events → 立即推

    幂等：用 fingerprint 去重，同一事件只推一次
      - must_sell:{code}
      - black_swan:{code}:{event_start}
    事件解除后自动清理（fingerprint 从 daily_results.urgent_pushed 移除）

    任何 mode 跑完后调用（不受 push_sent_date 约束）
    """
    if not trading:
        return False
    holdings = load_json("holdings.json") or []
    if not holdings:
        return False
    dr = load_json("daily_results.json") or {}
    if not isinstance(dr, dict):
        dr = {}
    already_pushed = set(dr.get("urgent_pushed", []) or [])
    holding_signals = dr.get("holding_signals", []) or []
    sig_by_code = {str(s.get("code", "")).zfill(6): s for s in holding_signals if s.get("code")}

    new_alerts = []          # [(fingerprint, alert_dict), ...]
    active_fingerprints = set()  # 本次扫描到的所有活跃指纹

    try:
        from black_swan_filter import check_company_black_swan
    except Exception as _e:
        print(f"[紧急通道] 黑天鹅过滤器加载失败：{_e}")
        check_company_black_swan = lambda c: None

    for h in holdings:
        code = str(h.get("code", "")).zfill(6)
        name = h.get("name", code)
        sig = sig_by_code.get(code, {})

        # 触发 1：must_sell
        if sig.get("must_sell"):
            fp = f"must_sell:{code}"
            active_fingerprints.add(fp)
            if fp not in already_pushed:
                new_alerts.append((fp, {
                    "code": code, "name": name, "type": "must_sell",
                    "signal": sig.get("signal", ""),
                    "advice": sig.get("pnl_advice", "") or sig.get("signal_text", ""),
                }))

        # 触发 2：黑天鹅命中
        event = check_company_black_swan(code, now)
        if event:
            fp = f"black_swan:{code}:{event.get('start', '')}"
            active_fingerprints.add(fp)
            if fp not in already_pushed:
                new_alerts.append((fp, {
                    "code": code, "name": name, "type": "black_swan",
                    "desc": event.get("desc", ""),
                    "action": event.get("action_suggested", ""),
                    "event_start": event.get("start"),
                }))

    # 清理已解除的 fingerprint（不再活跃的）
    updated_pushed = already_pushed & active_fingerprints

    # 推送新紧急事件
    if new_alerts:
        try:
            sent = send_urgent_alert(config, [a[1] for a in new_alerts], now)
            if sent:
                for fp, _ in new_alerts:
                    updated_pushed.add(fp)
        except Exception as _e:
            print(f"[紧急通道] 推送异常：{_e}")
    else:
        print(f"[紧急通道] 无新紧急事件（活跃 {len(active_fingerprints)} 条已推过）")

    # 写回 daily_results（即使无新推送，也可能有清理需要持久化）
    if set(dr.get("urgent_pushed", []) or []) != updated_pushed:
        dr["urgent_pushed"] = sorted(updated_pushed)
        try:
            save_json("daily_results.json", dr)
        except Exception as _e:
            print(f"[紧急通道] 写回 urgent_pushed 失败：{_e}")

    return len(new_alerts) > 0


def _ensure_daily_push_sent(config, now, trading, mode):
    """D-007（2026-04-24）：每日推送兜底。

    GitHub Actions 经常跳过 08:55 send_ai / 09:05 备份 cron（实测已连续
    两天发生），导致用户收不到当天推送。这个函数在 holdings / patch_round /
    watchlist / merge_full 模式末尾调用，如果满足：
      1. 是交易日
      2. 当前北京时间在 9:00 ~ 12:00
      3. 今天还没发过推送（查 last_push_log.json）
    就读 market_scan_cache.json 发一次推送；如果 cache 日期不是今天，先
    就地跑一次 merge 逻辑再发（复用 send_ai 兜底 merge 那套）。

    幂等：成功发送后写 last_push_log.json，同一天其它 cron 再调用会直接跳过。

    返回：True=发送了一次推送；False=跳过
    """
    if mode in ("send_ai", "reanalyze"):
        return False  # send_ai 自己会发（且会写 last_push_log），reanalyze 调试模式不发
    if not trading:
        return False

    bj_hour = now.hour
    # 推送时间窗口：9:00 ~ 12:00 北京
    # 留 3 小时窗口给 GitHub 跳过后的补救 cron
    if bj_hour < 9 or bj_hour >= 12:
        return False

    today_str = now.strftime("%Y-%m-%d")
    # D-010（2026-04-24）：幂等状态改存 daily_results.json 里
    # 原 last_push_log.json 未在 yml 的 commit 列表里，每次 run 开头
    # `git reset --hard` 把它清掉 → D-007 每次都以为"今天没发过"
    # 实测 04-24 11:42 merge_full run 又重复推了一次 29 条。
    # 改用 daily_results.push_sent_date 字段（daily_results 已被 yml 备份+提交）
    existing_dr = load_json("daily_results.json")
    if (isinstance(existing_dr, dict)
            and existing_dr.get("push_sent_date") == today_str):
        _sent_at = existing_dr.get("push_sent_at", "?")
        print(f"[D-007] 今日已发过推送（{_sent_at}），跳过补发")
        return False

    print(f"[D-007] 当前 {bj_hour}:xx 北京 · 今日未推送 → 准备补发")

    # 读 cache；若非当天，就地补一次 merge
    cache = load_json("market_scan_cache.json")
    cache_date = cache.get("date", "") if isinstance(cache, dict) else ""

    if cache_date != today_str:
        print(f"  cache 日期={cache_date}，非当天 → 补跑 merge（读 6 段 + 当天 patch）")
        import glob as _glob
        _all_recs = []
        _seen = set()
        _merged_files = []
        _today_yyyymmdd = now.strftime("%Y%m%d")
        for _p in range(1, 7):
            _f = os.path.join(os.path.dirname(__file__),
                                f"market_scan_full_p{_p}.json")
            if os.path.exists(_f):
                try:
                    _data = load_json(f"market_scan_full_p{_p}.json")
                    if isinstance(_data, dict):
                        for _s in _data.get("ai_recommendations", []):
                            _c = str(_s.get("code", "")).zfill(6)
                            if _c and _c not in _seen:
                                _seen.add(_c)
                                _s["source"] = f"段 {_p}"
                                _all_recs.append(_s)
                        _merged_files.append(f"p{_p}")
                except Exception as _e:
                    print(f"    段 {_p} 读取失败：{_e}")
        _patch_pat = os.path.join(os.path.dirname(__file__),
                                    f"market_scan_patch_{_today_yyyymmdd}_*.json")
        for _pf in sorted(_glob.glob(_patch_pat)):
            try:
                _fname = os.path.basename(_pf)
                _data = load_json(_fname)
                if isinstance(_data, dict):
                    for _s in _data.get("ai_recommendations", []):
                        _c = str(_s.get("code", "")).zfill(6)
                        if _c and _c not in _seen:
                            _seen.add(_c)
                            _s["source"] = "补漏"
                            _all_recs.append(_s)
            except Exception as _e:
                print(f"    补漏文件 {_pf} 读取失败：{_e}")
        # 写 cache 让下次模式能直接用
        save_json("market_scan_cache.json", {
            "date": today_str,
            "ai_recommendations": _all_recs,
        })
        ai_recs = _all_recs
        print(f"  ✅ 补跑 merge 完成：{len(_all_recs)} 条推荐 / {len(_merged_files)} 个段文件")
    else:
        ai_recs = cache.get("ai_recommendations", []) if isinstance(cache, dict) else []
        _merged_files = []  # 没补跑时 merged_files 为空

    # D-009/010（2026-04-24）：不管是就地补跑 merge 还是用现成 cache，
    # 都要把 ai_recs 合并回 daily_results.json + 写 push_sent_date 幂等标记
    # 不然前端（读 daily_results）和微信（读 cache）内容不一致
    # 04-24 11:08 实测：微信 29 条，前端只看到 1 条陈旧数据
    _push_marker_written = False
    if ai_recs:
        try:
            _existing = load_json("daily_results.json")
            _src_label = (f"{mode} + 兜底合并 6 段（{len(_merged_files)} 个文件）"
                          if _merged_files else f"{mode} + 复用当天 cache")
            _new_data = {
                "date": now.strftime("%Y-%m-%d %H:%M"),
                "mode": f"{mode}_with_fallback_push",
                "is_trading_day": trading,
                "data_source": _src_label,
                "ai_recommendations": ai_recs,
                # D-010 幂等标记（随 daily_results 自动提交到 GitHub）
                "push_sent_date": today_str,
                "push_sent_at": now.isoformat(timespec="seconds"),
                "push_triggered_by": mode,
            }
            if _merged_files:
                _new_data["merged_files"] = _merged_files
            _merged_dr = merge_daily_data(
                _existing if isinstance(_existing, dict) else {}, _new_data)
            # merge_daily_data 不认识 push_sent_date → 需要手动保留
            _merged_dr["push_sent_date"] = today_str
            _merged_dr["push_sent_at"] = _new_data["push_sent_at"]
            _merged_dr["push_triggered_by"] = mode
            save_json("daily_results.json", _merged_dr)
            _push_marker_written = True
            print(f"[D-007] ✅ daily_results 已同步（{len(ai_recs)} 条 ai_recommendations）")
        except Exception as _me:
            print(f"[D-007] ⚠ daily_results 同步失败：{_me}")

    # 发推送
    today_md = now.strftime("%m-%d")
    try:
        if ai_recs:
            send_daily_report(
                watchlist_signals=[],
                candidates=ai_recs,
                holding_signals=[],
                config=config,
            )
            _msg_summary = f"{len(ai_recs)} 条推荐"
        else:
            send_simple_msg(config, f"芒格选股 {today_md}\n\nAI全市场扫描暂无推荐\n继续观察")
            _msg_summary = "无推荐"
        # D-010 幂等：无 ai_recs 时上面 _push_marker_written=False，这里补写
        # （有 ai_recs 时已在 daily_results 同步那里写过）
        if not _push_marker_written:
            try:
                _dr2 = load_json("daily_results.json")
                if not isinstance(_dr2, dict):
                    _dr2 = {}
                _dr2["push_sent_date"] = today_str
                _dr2["push_sent_at"] = now.isoformat(timespec="seconds")
                _dr2["push_triggered_by"] = mode
                save_json("daily_results.json", _dr2)
            except Exception as _me2:
                print(f"[D-007] ⚠ 无推荐时 push 标记写入失败：{_me2}")
        # 同时保留 last_push_log.json（冗余，单机调试用）
        save_json("last_push_log.json", {
            "date": today_str,
            "sent_at": now.isoformat(timespec="seconds"),
            "triggered_by": mode,  # 记录是哪个 cron 兜底发的
            "recs_count": len(ai_recs),
            "summary": _msg_summary,
        })
        print(f"[D-007] ✅ 兜底推送发送成功（{mode} · {_msg_summary}）")
        return True
    except Exception as _e:
        print(f"[D-007] ⚠ 兜底推送失败：{_e}")
        return False


def _inject_market_temperature():
    """
    获取实时市场温度并注入 daily_results.json 的 market_temperature 字段
    在每次保存 daily_results 后调用，统一注入，避免在每个模式里重复代码
    """
    try:
        print("获取市场温度计...")
        temp = get_realtime_market_temperature()
        existing = load_json("daily_results.json")
        if isinstance(existing, dict):
            existing["market_temperature"] = temp
            save_json("daily_results.json", existing)
        print(f"  市场温度：{temp['label']} ({temp['description']})")
    except Exception as e:
        print(f"  温度计获取失败: {e}")


def _inject_etf_monitor():
    """
    对持仓中的 ETF 做估值监测并注入 daily_results.json
    - etf_signals: 每只 ETF 的温度/分位/买卖信号
    - portfolio_classification: 组合按宽基/策略/行业/个股分类
    - etf_unmapped: 未在映射表里的 ETF（提示用户补充）

    注意：宽基 ETF 仍计入股票总仓位，不会被当成"类固收"剔除。
    """
    try:
        print("ETF 估值监测...")
        holdings = load_json("holdings.json")
        if not holdings:
            return

        # 读出已注入的大盘温度等级，传给 ETF 监测用于"牛顶减仓提醒"
        # 依赖 _inject_market_temperature 先跑
        existing_now = load_json("daily_results.json")
        market_temp_level = 0
        if isinstance(existing_now, dict):
            mt = existing_now.get("market_temperature") or {}
            market_temp_level = mt.get("level", 0)

        etf_results, unmapped = scan_and_update_holdings_etfs(
            holdings, market_temp_level=market_temp_level
        )
        portfolio_cls = classify_portfolio(holdings)

        existing = load_json("daily_results.json")
        if isinstance(existing, dict):
            existing["etf_signals"] = etf_results
            existing["portfolio_classification"] = portfolio_cls
            existing["etf_unmapped"] = unmapped
            save_json("daily_results.json", existing)

        hot_etfs = [r for r in etf_results
                    if r.get("signal") in ("sell_heavy", "sell_light")]
        if hot_etfs:
            # 措辞按巴菲特理念：高位是"暂停加仓"而非"要卖"
            print(f"  ⚠ {len(hot_etfs)} 只 ETF 估值偏高，建议暂停加仓：")
            for r in hot_etfs:
                print(f"    {r['code']} {r['name']} → {r.get('signal_text','')}")
    except Exception as e:
        print(f"  ETF 监测失败: {e}")


def main():
    mode = get_mode()
    now = beijing_now()
    print(f"=== 芒格选股系统 {now.strftime('%Y-%m-%d %H:%M')} 模式:{mode} ===\n")

    # TODO-021（2026-04-20）：启动时检查"上次扫描异常待重跑"标记
    # 如果有 → 强制把 mode 改为 all 跑一次完整扫描
    try:
        from scan_validator import get_pending_retry, clear_retry_flag
        retry_flag = get_pending_retry()
        if retry_flag and mode != 'all':
            print(f"📌 检测到上次扫描异常待重跑：{retry_flag.get('reason')}")
            print(f"   原 mode={mode} → 强制改为 all 重跑全市场扫描")
            mode = 'all'
            # 不在这里 clear flag——等扫描真正成功后再清除（在下方 run_full_scan 之后）
    except Exception as _e:
        print(f"  ⚠ 重跑标记检查失败（不影响主流程）: {_e}")

    config = load_config()
    today = now.strftime("%m-%d")
    trading = is_trading_day()
    print(f"交易日: {'是' if trading else '否（休市）'}")

    # 校验是否需要运行
    if mode != "all":
        need_run, reason = should_run_and_update(mode)
        if not need_run:
            print(f"跳过运行: {reason}")
            return
        print(f"运行原因: {reason}")

    # 先注入市场温度，让后面的 run_holdings / _inject_etf_monitor
    # 能读到最新温度等级用于"牛顶减仓提醒"
    if mode not in ("reanalyze",):
        _inject_market_temperature()

    # 休市日处理
    if not trading and mode != "all":
        _, _, has_data = get_data_info()
        if not has_data:
            # 首次使用，无数据，跑一次获取休市前数据
            print("首次使用+休市日，获取休市前数据...")
            quotes_df = get_realtime_quotes()
            if quotes_df is not None and not quotes_df.empty:
                watchlist_signals, _ = check_watchlist(config, quotes_df)
                holding_signals = run_holdings(config)
                cache = load_json("market_scan_cache.json")
                daily_data = {
                    "date": now.strftime("%Y-%m-%d %H:%M"),
                    "mode": "holiday_init",
                    "data_source": "休市前最后交易日数据",
                    "watchlist_signals": watchlist_signals,
                    "holding_signals": holding_signals,
                    "ai_recommendations": cache.get("ai_recommendations", []) if isinstance(cache, dict) else [],
                }
                save_json("daily_results.json", daily_data)
        # 发休市消息（保持48小时互动）
        send_simple_msg(config, f"芒格选股 {today}\n\n本日休市\n关注表和持仓数据已同步\n下个交易日自动运行")
        return

    # ========================================
    # 各模式执行
    # ========================================

    if mode == "holdings":
        holding_signals = run_holdings(config)
        holdings = load_json("holdings.json")

        # 仓位控制检查
        position_warnings = check_position_sizes(holdings)
        for w in position_warnings:
            print(f"  仓位提醒: {w['text']}")

        new_data = {
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "mode": "holdings",
            "is_trading_day": trading,
            "data_source": "持仓检查" + ("" if trading else "（休市日，上一交易日数据）"),
            "holding_signals": holding_signals,
            "position_warnings": position_warnings,
        }
        existing = load_json("daily_results.json")
        merged = merge_daily_data(existing if isinstance(existing, dict) else {}, new_data)
        save_json("daily_results.json", merged)
        # F-4（2026-04-24 用户反馈）：holdings 模式不再直推微信
        # 用户原意"晚上分段跑 + 早 9 点合并后一次性推"
        # 12:30/15:10 的 holdings cron 只更新数据，不推送
        # 紧急持仓卖出信号会通过次日 09:05 send_ai / D-007 兜底合并推送

    elif mode == "watchlist":
        watchlist_signals = run_watchlist(config)
        holding_signals = run_holdings(config)
        holdings = load_json("holdings.json")
        cache = load_json("market_scan_cache.json")

        # 构建统一的 ai_recommendations
        ai_recs = []
        seen_codes = set()
        for s in watchlist_signals:
            if s.get("signal") and "buy" in s["signal"]:
                s["source"] = "关注表"
                ai_recs.append(s)
                seen_codes.add(s["code"])
        if isinstance(cache, dict):
            for s in cache.get("ai_recommendations", []):
                if s.get("code") not in seen_codes:
                    ai_recs.append(s)

        # 仓位控制检查
        position_warnings = check_position_sizes(holdings)
        for w in position_warnings:
            print(f"  仓位提醒: {w['text']}")

        # 机会成本比较（持仓卖出信号 vs 关注表买入信号）
        swap_suggestions = compare_opportunity_cost(holding_signals, ai_recs)
        for s in swap_suggestions:
            print(f"  换仓建议: {s['text']}")

        new_data = {
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "mode": "watchlist",
            "is_trading_day": trading,
            "data_source": "关注表+持仓" + ("" if trading else "（休市日，上一交易日数据）"),
            "watchlist_signals": watchlist_signals,
            "holding_signals": holding_signals,
            "ai_recommendations": ai_recs,
            "position_warnings": position_warnings,
            "swap_suggestions": swap_suggestions,
        }
        existing = load_json("daily_results.json")
        merged = merge_daily_data(existing if isinstance(existing, dict) else {}, new_data)
        save_json("daily_results.json", merged)

        # F-4（2026-04-24）：watchlist 模式不再直推微信
        # 18:00/22:00 的 watchlist cron 只更新数据，不推送
        # 数据写到 daily_results.json，用户前端刷新看；紧急信号次日 09:05 合并推送

    elif mode == "full":
        ai_recs = run_full_scan(config)
        new_data = {
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "mode": "full",
            "is_trading_day": trading,
            "data_source": "全市场扫描" + ("" if trading else "（休市日，上一交易日数据）"),
            "ai_recommendations": ai_recs,
        }
        existing = load_json("daily_results.json")
        merged = merge_daily_data(existing if isinstance(existing, dict) else {}, new_data)
        save_json("daily_results.json", merged)
        print(f"  已保存，等待9点发送")

    elif mode and mode.startswith("full_p"):
        # TODO-022 第 2 批（方案 D 调整）：分段全市场扫描
        # 6 段，按 int(code) % 6 分桶（每段约 916 只）
        # mode=full_p1 跑桶 0，full_p2 跑桶 1，... full_p6 跑桶 5
        try:
            p_idx = int(mode[6:]) - 1  # 'full_p1' → 0
            if not (0 <= p_idx <= 5):
                raise ValueError(f"段编号超范围：{p_idx}（方案 D 已改 6 段）")
        except Exception as _e:
            print(f"  ⚠ 无效 mode={mode}: {_e}")
            return

        def code_filter(c):
            try:
                return int(c) % 6 == p_idx
            except (ValueError, TypeError):
                return False  # 非数字代码（如港股 ETF）跳过

        from screener import screen_all_stocks as _scan
        print(f"=== 分段扫描 段 {p_idx + 1}/6（mode={mode}）===")
        # BUG-033：传增量保存路径，每 20 只写一次（防超时全丢）
        _inc_path = os.path.join(os.path.dirname(__file__), f"market_scan_{mode}.json")
        candidates = _scan(config, code_filter=code_filter, track_freshness=True,
                            incremental_save_path=_inc_path, save_every_n=20)
        ai_recs = [s for s in candidates if s.get("signal") and s["signal"] not in ("hold", None)]

        # 写到段独立 cache（之后第 3 批 merge 用）
        save_json(f"market_scan_{mode}.json", {
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "mode": mode,
            "p_idx": p_idx,
            "candidates_count": len(candidates),
            "ai_recommendations": ai_recs,
        })
        print(f"  段 {p_idx + 1} 完成：候选 {len(candidates)} / 推荐 {len(ai_recs)}")

        # 段健康校验（< 50% 进 freshness 失败列表，仅记录不重跑——补漏轮处理）
        try:
            from scan_freshness import _load as _fr_load
            fr = _fr_load()
            # 这段对应的代码集合（方案 D 改为 % 6）
            seg_codes = [c for c in fr.keys() if c.isdigit() and int(c) % 6 == p_idx]
            seg_total = len(seg_codes)
            if seg_total > 0:
                seg_fails = sum(1 for c in seg_codes
                                if fr.get(c, {}).get('consecutive_fails', 0) > 0)
                completion = (seg_total - seg_fails) / seg_total
                if completion < 0.5:
                    print(f"  🚨 段健康度差：完成率 {completion:.0%}（< 50%）")
                    print(f"     失败 {seg_fails} 只将由后续补漏轮处理")
                elif completion < 0.98:
                    print(f"  🟡 段健康度警告：完成率 {completion:.0%}（漏 {seg_fails} 只）")
                else:
                    print(f"  🟢 段健康：完成率 {completion:.0%}")
        except Exception as _e:
            print(f"  ⚠ 段健康校验失败：{_e}")

    elif mode == "patch_round":
        # TODO-022 第 3 批：补漏轮
        # 跑 scan_freshness 里 fails ≥ 1 的股，按"持仓优先 + fails 倒序"排序
        # 单轮 timeout 内最多跑 1100 只（按 60 分钟 / 3 秒一只算）
        print("=== 补漏轮 patch_round ===")
        try:
            from scan_freshness import get_stale_stocks
            holdings = load_json("holdings.json") or []
            holdings_codes = [str(h.get("code", "")).zfill(6) for h in holdings
                              if h.get("code")]
            # ETF 也算持仓优先
            etf_codes = [c for c in holdings_codes if c.startswith(('5', '1'))]
            non_etf_holdings = [c for c in holdings_codes if c not in etf_codes]

            # 关注表（model + my，toohard 不算因为太复杂用户要慢慢分析）
            from watchlist_manager import _load as _wl_load
            watchlist_codes = []
            for table in ('model', 'my'):
                for it in _wl_load(table):
                    c = str(it.get("code", "")).zfill(6)
                    if c:
                        watchlist_codes.append(c)

            # 漏跑列表（按持仓优先 + fails 倒序）
            # 2026-04-23：加 max_lag_hours=24 兜底整段被 GitHub 跳过的情况
            # 2026-04-24 D-006：放宽到 30 小时
            # 背景：04-24 00:28 北京 patch_round 用 24h 阈值拉起 136 只股花了 67 分钟
            # 逼近 75 分钟 step timeout。30h 减少拉起量（24h 周期正常能覆盖，6h 缓冲）
            # 配合 D-005 全局 socket timeout，防止某只股 hang 死整段
            stale = get_stale_stocks(
                priority_holdings=non_etf_holdings,
                priority_etf=etf_codes,
                priority_watchlist=watchlist_codes,
                max_count=1100,  # 单轮上限
                max_lag_hours=30,
            )
            stale_codes = [c for c, _, _ in stale]

            if not stale_codes:
                print("  🟢 无漏跑数据，本轮跳过")
                return

            print(f"  待补 {len(stale_codes)} 只（前 5: {stale_codes[:5]}...）")
        except Exception as _e:
            print(f"  ⚠ 拉漏跑列表失败：{_e}")
            return

        # 用 screen_all_stocks 跑这些股
        stale_set = set(stale_codes)
        from screener import screen_all_stocks as _scan
        candidates = _scan(config,
                           code_filter=lambda c: c in stale_set,
                           track_freshness=True)
        ai_recs = [s for s in candidates if s.get("signal") and s["signal"] not in ("hold", None)]

        # 写到独立 cache（merge 时合并）
        round_id = now.strftime("patch_%Y%m%d_%H%M")
        save_json(f"market_scan_{round_id}.json", {
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "mode": "patch_round",
            "stale_count_input": len(stale_codes),
            "candidates_count": len(candidates),
            "ai_recommendations": ai_recs,
        })
        print(f"  补漏完成：输入 {len(stale_codes)} / 候选 {len(candidates)} / 推荐 {len(ai_recs)}")

    elif mode == "merge_full":
        # TODO-022 第 3 批：合并 7 段 + 当天补漏轮的所有 ai_recommendations
        # 写到 daily_results.json，给 09:05 send_ai 用
        print("=== merge_full ===")
        import glob
        all_recs = []
        seen_codes = set()
        merged_files = []
        today_str = now.strftime("%Y%m%d")

        # 1. 读 6 段（方案 D 从 7 段改为 6 段）
        for p in range(1, 7):
            f = os.path.join(os.path.dirname(__file__), f"market_scan_full_p{p}.json")
            if os.path.exists(f):
                try:
                    data = load_json(f"market_scan_full_p{p}.json")
                    if isinstance(data, dict):
                        recs = data.get("ai_recommendations", [])
                        for s in recs:
                            code = str(s.get("code", "")).zfill(6)
                            if code and code not in seen_codes:
                                seen_codes.add(code)
                                s["source"] = f"段 {p}"
                                all_recs.append(s)
                        merged_files.append(f"p{p}({len(recs)})")
                except Exception as _e:
                    print(f"  ⚠ 段 {p} 读取失败：{_e}")

        # 2. 读当天的补漏轮（market_scan_patch_YYYYMMDD_*.json）
        patch_pattern = os.path.join(os.path.dirname(__file__),
                                       f"market_scan_patch_{today_str}_*.json")
        for pf in sorted(glob.glob(patch_pattern)):
            try:
                fname = os.path.basename(pf)
                data = load_json(fname)
                if isinstance(data, dict):
                    recs = data.get("ai_recommendations", [])
                    added = 0
                    for s in recs:
                        code = str(s.get("code", "")).zfill(6)
                        if code and code not in seen_codes:
                            seen_codes.add(code)
                            s["source"] = "补漏"
                            all_recs.append(s)
                            added += 1
                    merged_files.append(f"{fname.replace('market_scan_', '').replace('.json', '')}(+{added})")
            except Exception as _e:
                print(f"  ⚠ 补漏文件 {pf} 读取失败：{_e}")

        # 3. 写 daily_results
        new_data = {
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "mode": "merge_full",
            "is_trading_day": trading,
            "data_source": f"分段扫描合并（含 {len(merged_files)} 个文件）",
            "ai_recommendations": all_recs,
            "merged_files": merged_files,
        }
        existing = load_json("daily_results.json")
        merged = merge_daily_data(existing if isinstance(existing, dict) else {}, new_data)
        save_json("daily_results.json", merged)

        # 同时写到 cache 给 send_ai 用
        save_json("market_scan_cache.json", {
            "date": now.strftime("%Y-%m-%d"),
            "ai_recommendations": all_recs,
        })

        print(f"  ✅ 合并完成：{len(all_recs)} 只推荐 / {len(merged_files)} 个源文件")
        for s in merged_files:
            print(f"     - {s}")

    elif mode == "send_ai":
        # 发送AI推荐（早上9点）
        # 2026-04-23 兜底：GitHub Actions 会跳 08:15 merge_full cron
        # （2026-04-23 今早就跳过了）。这里先看 cache 是不是当天的，
        # 不是就自己跑一遍 merge 再发，避免推送旧数据。
        cache = load_json("market_scan_cache.json")
        cache_date = cache.get("date", "") if isinstance(cache, dict) else ""
        today_date_str = now.strftime("%Y-%m-%d")
        if cache_date != today_date_str:
            print(f"⚠ market_scan_cache 日期={cache_date}，非当天 {today_date_str}")
            print("  → 推测 merge_full cron 被 GitHub 跳过，现场补一次合并")
            import glob as _glob
            _all_recs = []
            _seen = set()
            _merged_files = []
            _today_yyyymmdd = now.strftime("%Y%m%d")
            for _p in range(1, 7):
                _f = os.path.join(os.path.dirname(__file__),
                                    f"market_scan_full_p{_p}.json")
                if os.path.exists(_f):
                    try:
                        _data = load_json(f"market_scan_full_p{_p}.json")
                        if isinstance(_data, dict):
                            for _s in _data.get("ai_recommendations", []):
                                _c = str(_s.get("code", "")).zfill(6)
                                if _c and _c not in _seen:
                                    _seen.add(_c)
                                    _s["source"] = f"段 {_p}"
                                    _all_recs.append(_s)
                            _merged_files.append(f"p{_p}")
                    except Exception as _e:
                        print(f"  ⚠ 段 {_p} 读取失败：{_e}")
            _patch_pat = os.path.join(os.path.dirname(__file__),
                                        f"market_scan_patch_{_today_yyyymmdd}_*.json")
            for _pf in sorted(_glob.glob(_patch_pat)):
                try:
                    _fname = os.path.basename(_pf)
                    _data = load_json(_fname)
                    if isinstance(_data, dict):
                        for _s in _data.get("ai_recommendations", []):
                            _c = str(_s.get("code", "")).zfill(6)
                            if _c and _c not in _seen:
                                _seen.add(_c)
                                _s["source"] = "补漏"
                                _all_recs.append(_s)
                        _merged_files.append(_fname.replace("market_scan_", "").replace(".json", ""))
                except Exception as _e:
                    print(f"  ⚠ 补漏文件 {_pf} 读取失败：{_e}")
            # 写回 cache + daily_results
            save_json("market_scan_cache.json", {
                "date": today_date_str,
                "ai_recommendations": _all_recs,
            })
            _existing = load_json("daily_results.json")
            _new_data = {
                "date": now.strftime("%Y-%m-%d %H:%M"),
                "mode": "send_ai_with_fallback_merge",
                "is_trading_day": trading,
                "data_source": f"分段扫描合并 兜底（含 {len(_merged_files)} 个文件）",
                "ai_recommendations": _all_recs,
                "merged_files": _merged_files,
            }
            _merged = merge_daily_data(
                _existing if isinstance(_existing, dict) else {}, _new_data)
            save_json("daily_results.json", _merged)
            ai_recs = _all_recs
            print(f"  ✅ 兜底合并完成：{len(_all_recs)} 只推荐 / "
                  f"{len(_merged_files)} 个源文件")
        else:
            ai_recs = cache.get("ai_recommendations", []) if isinstance(cache, dict) else []

        if ai_recs:
            send_daily_report(
                watchlist_signals=[],
                candidates=ai_recs,
                holding_signals=[],
                config=config,
            )
        else:
            send_simple_msg(config, f"芒格选股 {today}\n\nAI全市场扫描暂无推荐\n继续观察")

        # D-010（2026-04-24）：幂等标记写到 daily_results（yml 会自动提交）
        # 原 last_push_log.json 未进 yml 提交列表，reset --hard 会擦掉
        try:
            _dr = load_json("daily_results.json")
            if not isinstance(_dr, dict):
                _dr = {}
            _dr["push_sent_date"] = now.strftime("%Y-%m-%d")
            _dr["push_sent_at"] = now.isoformat(timespec="seconds")
            _dr["push_triggered_by"] = "send_ai"
            save_json("daily_results.json", _dr)
        except Exception as _pe:
            print(f"  ⚠ push 标记写入失败：{_pe}")

        # TODO-022 第 4 批：freshness 报警单独推一条
        # 列出"持仓+ETF 红色 / 关注 红色 / 候选股聚合超阈值"的清单
        try:
            from scan_freshness import get_alert_level, get_freshness, get_lag_in_trading_days
            from holdings_attribution import filter_model_only

            holdings = load_json("holdings.json") or []

            red_alerts = []  # [(kind, code, name, lag, last_at), ...]
            yellow_alerts = []

            # 持仓 + ETF
            for h in holdings:
                code = str(h.get("code", "")).zfill(6)
                if not code:
                    continue
                level = get_alert_level(code)
                if level in ('red', 'yellow'):
                    fr = get_freshness(code) or {}
                    lag = get_lag_in_trading_days(code)
                    item = ('持仓' if not code.startswith(('5', '1')) else 'ETF',
                            code, h.get('name', code), lag,
                            fr.get('last_scanned_at', '?'))
                    if level == 'red':
                        red_alerts.append(item)
                    else:
                        yellow_alerts.append(item)

            # 关注表
            try:
                from watchlist_manager import _load as _wl_load
                for table in ('my', 'model'):
                    for it in _wl_load(table):
                        code = str(it.get("code", "")).zfill(6)
                        if not code:
                            continue
                        level = get_alert_level(code)
                        if level in ('red', 'yellow'):
                            fr = get_freshness(code) or {}
                            lag = get_lag_in_trading_days(code)
                            item = ('关注', code, it.get('name', code), lag,
                                    fr.get('last_scanned_at', '?'))
                            if level == 'red':
                                red_alerts.append(item)
                            else:
                                yellow_alerts.append(item)
            except Exception:
                pass

            # 只推有红色或多黄色的情况，避免日常消息打扰
            if red_alerts or len(yellow_alerts) >= 5:
                lines = [f"📊 数据新鲜度报警 {today}", ""]
                if red_alerts:
                    lines.append(f"🔴 严重未更新（{len(red_alerts)} 只）")
                    for kind, code, name, lag, last in red_alerts[:10]:
                        last_short = last[:10] if isinstance(last, str) and len(last) >= 10 else '?'
                        lines.append(f"  · [{kind}] {name}({code}) 已 {lag} 个交易日未更新（最后 {last_short}）")
                    if len(red_alerts) > 10:
                        lines.append(f"  ... 还有 {len(red_alerts) - 10} 只")
                    lines.append("")
                if yellow_alerts:
                    lines.append(f"🟡 偏旧（{len(yellow_alerts)} 只）")
                    for kind, code, name, lag, last in yellow_alerts[:5]:
                        lines.append(f"  · [{kind}] {name}({code}) 1 个交易日未更新")
                    if len(yellow_alerts) > 5:
                        lines.append(f"  ... 还有 {len(yellow_alerts) - 5} 只")
                    lines.append("")
                lines.append("💡 建议手动触发 patch_round 补漏，或检查 akshare 接口")
                _msg = "\n".join(lines).strip()
                if _msg:
                    send_simple_msg(config, _msg)
                    print(f"  ✅ freshness 报警已推送（红 {len(red_alerts)} 黄 {len(yellow_alerts)}）")
            else:
                print(f"  🟢 数据新鲜度健康（红 {len(red_alerts)} 黄 {len(yellow_alerts)}），无需报警")
        except Exception as _e:
            print(f"  ⚠ freshness 报警失败（不影响主流程）：{_e}")

    elif mode == "all":
        # 全部运行
        data_note = "交易日实时数据" if trading else "休市日，数据来自休市前最后交易日"
        print(f"获取行情（{data_note}）...")
        quotes_df = get_realtime_quotes()

        watchlist_signals, _ = check_watchlist(config, quotes_df)
        candidates = screen_all_stocks(config)
        holding_signals = run_holdings(config)

        # AI推荐 = 关注表买入信号 + 全市场扫描买入信号
        ai_recs = []
        seen_codes = set()
        # 先加关注表中有买入信号的
        for s in watchlist_signals:
            if s.get("signal") and "buy" in s["signal"]:
                s["source"] = "关注表"
                ai_recs.append(s)
                seen_codes.add(s["code"])
        # 再加全市场扫描中有买入信号的（去重）
        for s in candidates:
            if s.get("signal") and "buy" in s["signal"] and s["code"] not in seen_codes:
                s["source"] = "全市场筛选"
                ai_recs.append(s)

        daily_data = {
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "mode": "all",
            "is_trading_day": trading,
            "data_source": f"全量运行（{data_note}）",
            "ai_recommendations": ai_recs,
            "watchlist_signals": watchlist_signals,
            "holding_signals": holding_signals,
        }
        save_json("daily_results.json", daily_data)
        save_json("market_scan_cache.json", {"date": now.strftime("%Y-%m-%d"), "ai_recommendations": ai_recs})

        # 用统一的 ai_recs 发微信（和Streamlit一致）
        send_daily_report(
            watchlist_signals=[],
            candidates=ai_recs,
            holding_signals=holding_signals,
            config=config,
        )

    elif mode == "reanalyze":
        # 调试模式：用已缓存数据重跑模型，不抓网络
        print("=== 调试模式：从缓存数据重跑模型 ===")
        existing = load_json("daily_results.json")
        if not isinstance(existing, dict) or not existing.get("watchlist_signals"):
            print("无缓存数据，请先运行一次全量模式")
            return

        # 重新对已有数据应用最新模型逻辑
        # 这里不重新获取PE等数据，只重新计算信号
        print(f"  使用数据：{existing.get('date', '未知')}")
        print(f"  关注表：{len(existing.get('watchlist_signals', []))}只")
        print(f"  持仓：{len(existing.get('holding_signals', []))}只")

        # 重新构建ai_recommendations
        ai_recs = []
        for s in existing.get("watchlist_signals", []):
            if s.get("signal") and "buy" in s["signal"]:
                s["source"] = "关注表"
                ai_recs.append(s)

        existing["ai_recommendations"] = ai_recs
        existing["mode"] = "reanalyze"
        existing["data_source"] = f"调试模式（数据来自{existing.get('date', '未知')}）"
        save_json("daily_results.json", existing)
        print(f"  模型推荐：{len(ai_recs)}只")
        print("  结果已保存，刷新Streamlit查看")

    # 自动保存每周快照
    if mode in ("watchlist", "all", "full"):
        try:
            from snapshot import save_snapshot
            save_snapshot()
        except Exception as e:
            print(f"快照保存失败（不影响运行）: {e}")

    # 温度已在开头注入，这里只跑 ETF 监测（依赖已注入的 market_temp_level）
    if mode not in ("reanalyze",):
        _inject_etf_monitor()

    # D-007（2026-04-24）：每日推送兜底
    # 防 GitHub 跳过 08:55 send_ai / 09:05 备份（04-22 ~ 04-24 连续三天发生）
    # 9-12 点任何 cron 跑完都会检查"今天有没有发过推送"，没发就补发
    try:
        _ensure_daily_push_sent(config, now, trading, mode)
    except Exception as _e:
        print(f"⚠ 推送兜底逻辑异常（不影响主流程）：{_e}")

    # REQ-205（2026-04-24）：独立紧急通道
    # 与常规推送分开，不受 push_sent_date 幂等约束
    # 用 fingerprint 去重：同一紧急事件只推一次，事件解除后清理
    try:
        _check_urgent_alerts(config, now, trading)
    except Exception as _e:
        print(f"⚠ 紧急通道异常（不影响主流程）：{_e}")

    print(f"\n=== 完成 ===")


if __name__ == "__main__":
    main()
