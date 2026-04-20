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
from notifier import send_daily_report, send_msg, get_access_token
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
    """通过AKShare交易日历判断今天是否交易日（考虑节假日）"""
    today_str = beijing_now().strftime("%Y%m%d")
    try:
        # 获取交易日历
        df = safe_fetch(ak.tool_trade_date_hist_sina)
        if df is not None and not df.empty:
            trade_dates = set(df["trade_date"].astype(str).str.replace("-", ""))
            return today_str in trade_dates
    except Exception as e:
        print(f"  获取交易日历失败: {e}")
    # 失败时回退到简单判断（排除周末）
    return beijing_now().weekday() < 5


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
        send_daily_report(
            watchlist_signals=[],
            candidates=[],
            holding_signals=holding_signals,
            config=config,
        )

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

        # 推送（用同一份 ai_recs，保证微信和Streamlit一致）
        send_daily_report(
            watchlist_signals=[],
            candidates=ai_recs,
            holding_signals=holding_signals,
            position_warnings=position_warnings,
            swap_suggestions=swap_suggestions,
            config=config,
        )

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

    elif mode == "send_ai":
        # 发送AI推荐（早上9点）
        cache = load_json("market_scan_cache.json")
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

    print(f"\n=== 完成 ===")


if __name__ == "__main__":
    main()
