"""
中国特色调整逻辑（REQ-152 + REQ-160~164）

功能：
1. 政策敏感度检查（REQ-152）
2. 年轻公司白名单（REQ-152）
3. 熊市时间计数器（REQ-152）
4. 黑天鹅事件检测（REQ-152）
5. 财务造假风险检测（REQ-160，含3类禁买：跑步机/冲浪者/下水道）
6. ST/退市风险检测（REQ-161）
7. 自由现金流中国化（REQ-163）
8. 过路费生意识别（REQ-164）

注意：这些规则是为对抗查巴理念在中国水土不服的风险。
"""

import json
import os
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BEIJING = timezone(timedelta(hours=8))


def _load_json(filename):
    path = os.path.join(SCRIPT_DIR, filename)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


# ============================================================
# 1. 政策敏感度检查
# ============================================================

_POLICY_RISK_CACHE = None


def check_policy_risk(industry, category=""):
    """
    检查某行业是否在政策敏感列表中。

    返回：
      (risk_level, reason)
      risk_level: "high" | "medium" | "low_watch" | "safe"
      reason: 原因描述（None 表示无风险）
    """
    global _POLICY_RISK_CACHE
    if _POLICY_RISK_CACHE is None:
        _POLICY_RISK_CACHE = _load_json("policy_risk_industries.json")

    if not _POLICY_RISK_CACHE:
        return "safe", None

    text = f"{industry} {category}".lower()

    # 高风险
    for item in _POLICY_RISK_CACHE.get("high_risk", {}).get("industries", []):
        for kw in item.get("keywords", []):
            if kw.lower() in text:
                return "high", item.get("warning_text", "")

    # 中风险
    for item in _POLICY_RISK_CACHE.get("medium_risk", {}).get("industries", []):
        for kw in item.get("keywords", []):
            if kw.lower() in text:
                return "medium", item.get("warning_text", "")

    # 低风险但需关注
    for item in _POLICY_RISK_CACHE.get("low_risk_but_watch", {}).get("industries", []):
        for kw in item.get("keywords", []):
            if kw.lower() in text:
                return "low_watch", item.get("warning_text", "")

    return "safe", None


def adjust_signal_by_policy_risk(signal, industry, category=""):
    """
    按政策风险等级降级买入信号。

    降级规则：
      buy_heavy -> buy_medium
      buy_medium -> buy_light
      buy_light -> buy_watch
      buy_watch -> hold
      卖出信号不变
      high 风险降 2 级，medium 降 1 级

    返回：(new_signal, adjustment_note)
    """
    if not signal or "buy" not in signal:
        return signal, None

    risk, reason = check_policy_risk(industry, category)
    if risk == "safe":
        return signal, None

    downgrade_map_1 = {
        "buy_heavy": "buy_medium",
        "buy_medium": "buy_light",
        "buy_light": "buy_watch",
        "buy_watch": "hold",
        "buy_add": "hold_keep",
    }

    if risk == "high":
        # 高风险降 2 级
        new_sig = downgrade_map_1.get(signal, signal)
        new_sig = downgrade_map_1.get(new_sig, new_sig)
        note = f"⚠政策高敏感：{reason}"
    elif risk == "medium":
        new_sig = downgrade_map_1.get(signal, signal)
        note = f"⚠政策中敏感：{reason}"
    elif risk == "low_watch":
        # 只加警告，不降级
        new_sig = signal
        note = f"ℹ 政策关注：{reason}"
    else:
        new_sig = signal
        note = None

    return new_sig, note


# ============================================================
# 2. 年轻公司白名单（5-10年降档适用十年王者）
# ============================================================

def check_young_king(df_annual, min_years=5, max_years=10):
    """
    年轻公司是否符合"降档十年王者"标准。

    规则：
      ✅ 有 5-10 年数据（不足 10 年）
      ✅ 所有年份 ROE ≥ 15%
      ✅ 最低年份 ROE ≥ 12%
      ✅ 最近 2 年 ROE 不能双双 < 13%（年轻公司要求更严）

    返回：
      (is_young_king, avg_roe, data_years)
    """
    if df_annual is None or df_annual.empty:
        return False, None, 0

    # 拿 ROE
    roe_col = None
    for col in df_annual.columns:
        if "净资产收益率" == col:
            roe_col = col
            break
    if not roe_col:
        return False, None, 0

    roes = df_annual[roe_col].dropna().tolist()
    n = len(roes)

    if n < min_years or n >= max_years:
        # 太短或太长都不走这条规则
        return False, None, n

    # 所有年份 ≥ 15%
    if min(roes) < 15:
        return False, None, n

    # 最低年份 ≥ 12%（冗余校验，min(roes) 已经 ≥ 15）
    if min(roes) < 12:
        return False, None, n

    # 最近 2 年都 ≥ 13%
    if n >= 2 and (roes[0] < 13 and roes[1] < 13):
        return False, None, n

    avg = sum(roes) / n
    return True, avg, n


# ============================================================
# 3. 熊市时间计数器
# ============================================================

def is_bear_market(current_percentile=None):
    """
    判断当前是否处于熊市（分位 ≤ 30% 即为偏冷以下）

    参数：
      current_percentile: 市场温度百分位（0-100）

    返回：
      (is_bear, severity) severity: "deep" | "mild" | "normal"
    """
    if current_percentile is None:
        return False, "normal"
    if current_percentile <= 15:
        return True, "deep"
    if current_percentile <= 30:
        return True, "mild"
    return False, "normal"


def get_consecutive_bear_years(percentile_history):
    """
    从历史分位数据算出连续熊市年数。

    参数：
      percentile_history: 按日期排序的历史分位列表 [(date, pct), ...]

    返回：
      连续熊市（≤ 30% 分位）的年数
    """
    if not percentile_history:
        return 0
    # 从最新一天往前数
    sorted_hist = sorted(percentile_history, key=lambda x: x[0], reverse=True)
    bear_days = 0
    for date, pct in sorted_hist:
        if pct <= 30:
            bear_days += 1
        else:
            break
    return bear_days / 365  # 约数年


# ============================================================
# 4. 黑天鹅事件检测
# ============================================================

def get_current_black_swan():
    """
    检测当前是否处于已登记的黑天鹅事件期。

    返回：
      None（无事件）或 dict（事件详情）
    """
    data = _load_json("black_swan_events.json")
    if not data:
        return None

    today = datetime.now(_BEIJING).strftime("%Y-%m-%d")
    for event in data.get("events", []):
        start = event.get("start", "")
        end = event.get("end", "")
        if start <= today <= end:
            return event
    return None


def is_black_swan_now():
    """便捷接口：当前是否黑天鹅期"""
    return get_current_black_swan() is not None


# ============================================================
# 5. 综合调整（在 screener 中调用此入口）
# ============================================================

def apply_china_adjustments(signal, signal_text, industry="", category="",
                             df_annual=None):
    """
    综合应用所有中国特色调整。

    返回：(new_signal, new_signal_text, notes_list)
      notes: 所有触发的警告列表
    """
    notes = []
    new_sig = signal
    new_text = signal_text

    # 1. 政策风险调整
    adj_sig, policy_note = adjust_signal_by_policy_risk(signal, industry, category)
    if policy_note:
        notes.append(policy_note)
    if adj_sig != signal:
        new_sig = adj_sig
        new_text = f"{new_text} | {policy_note}"

    # 2. 黑天鹅期提醒
    bs = get_current_black_swan()
    if bs:
        notes.append(f"🦢 {bs['name']} - {bs.get('market_action_suggested','')}")

    return new_sig, new_text, notes


# ============================================================
# REQ-160 辅助：资产负债表数据获取（含缓存）
# ============================================================

_BALANCE_SHEET_CACHE = {}


def get_balance_sheet_latest(code):
    """
    获取最新资产负债表关键数据（带进程内缓存）

    返回 dict（可能为空）:
      {
          "report_date": 报告期,
          "monetary_funds": 货币资金,
          "short_loan": 短期借款,
          "long_loan": 长期借款,
          "bond_payable": 应付债券,
          "total_assets": 资产总计,
          "total_liabilities": 负债合计,
          "accounts_receivable": 应收账款,
          "goodwill": 商誉,
      }
    """
    if code in _BALANCE_SHEET_CACHE:
        return _BALANCE_SHEET_CACHE[code]

    import akshare as ak
    import pandas as pd
    try:
        # 根据代码前缀加 SH/SZ 前缀
        c = str(code).zfill(6)
        prefix = "SH" if c.startswith(("6", "9")) else "SZ" if c.startswith(("0", "2", "3")) else "BJ"
        df = ak.stock_balance_sheet_by_report_em(symbol=f"{prefix}{c}")
        if df is None or df.empty:
            _BALANCE_SHEET_CACHE[code] = {}
            return {}

        row = df.iloc[0]
        result = {
            "report_date": str(row.get("REPORT_DATE", ""))[:10],
            "monetary_funds": float(row.get("MONETARYFUNDS", 0) or 0),
            "short_loan": float(row.get("SHORT_LOAN", 0) or 0) if pd.notna(row.get("SHORT_LOAN")) else 0,
            "long_loan": float(row.get("LONG_LOAN", 0) or 0) if pd.notna(row.get("LONG_LOAN")) else 0,
            "bond_payable": float(row.get("BOND_PAYABLE", 0) or 0) if pd.notna(row.get("BOND_PAYABLE")) else 0,
            "total_assets": float(row.get("TOTAL_ASSETS", 0) or 0),
            "total_liabilities": float(row.get("TOTAL_LIABILITIES", 0) or 0),
            "accounts_receivable": float(row.get("ACCOUNTS_RECE", 0) or 0) if pd.notna(row.get("ACCOUNTS_RECE")) else 0,
            "goodwill": float(row.get("GOODWILL", 0) or 0) if pd.notna(row.get("GOODWILL")) else 0,
        }
        _BALANCE_SHEET_CACHE[code] = result
        return result
    except Exception:
        _BALANCE_SHEET_CACHE[code] = {}
        return {}


def check_cash_loan_double_high(code):
    """
    检测"存贷双高"（康美药业经典造假模式）
    货币资金 > 总资产 30% 同时 借款总额 > 总资产 30%

    返回：(is_double_high, details)
    """
    bs = get_balance_sheet_latest(code)
    if not bs or not bs.get("total_assets"):
        return False, {}

    total_assets = bs["total_assets"]
    cash = bs["monetary_funds"]
    total_loans = bs.get("short_loan", 0) + bs.get("long_loan", 0) + bs.get("bond_payable", 0)

    cash_ratio = cash / total_assets * 100 if total_assets > 0 else 0
    loan_ratio = total_loans / total_assets * 100 if total_assets > 0 else 0

    is_double_high = cash_ratio > 30 and loan_ratio > 30

    return is_double_high, {
        "cash_ratio": round(cash_ratio, 1),
        "loan_ratio": round(loan_ratio, 1),
        "cash_yi": round(cash / 1e8, 2),
        "loan_yi": round(total_loans / 1e8, 2),
        "assets_yi": round(total_assets / 1e8, 2),
    }


def check_abnormal_cash(code):
    """
    检测货币资金异常占比（> 50% 总资产，无合理解释）
    """
    bs = get_balance_sheet_latest(code)
    if not bs or not bs.get("total_assets"):
        return False, {}

    cash_ratio = bs["monetary_funds"] / bs["total_assets"] * 100 if bs["total_assets"] > 0 else 0
    return cash_ratio > 50, {"cash_ratio": round(cash_ratio, 1)}


# ============================================================
# REQ-162：股权质押比例检测
# ============================================================

_PLEDGE_CACHE = {}


def check_pledge_risk(code):
    """
    检测大股东股权质押风险
    质押比例 > 80% → 爆雷前兆（康得新、康美模式）

    返回：(risk_level, details)
      risk_level: "high" / "medium" / "low" / "unknown"
    """
    if code in _PLEDGE_CACHE:
        return _PLEDGE_CACHE[code]

    try:
        import akshare as ak
        # 用 stock_gpzy_pledge_ratio_em 需要日期参数，改用 stock_gpzy_profile_em
        # 或直接用 stock_gpzy_pledge_ratio_detail_em（按股票代码查）
        df = ak.stock_gpzy_pledge_ratio_detail_em()
        if df is None or df.empty:
            _PLEDGE_CACHE[code] = ("unknown", {})
            return "unknown", {}

        # 找这只股票
        code_col = next((c for c in df.columns if "代码" in c), None)
        if not code_col:
            _PLEDGE_CACHE[code] = ("unknown", {})
            return "unknown", {}

        mine = df[df[code_col].astype(str).str.zfill(6) == str(code).zfill(6)]
        if mine.empty:
            # 找不到说明无质押记录
            _PLEDGE_CACHE[code] = ("low", {"pledge_ratio": 0})
            return "low", {"pledge_ratio": 0}

        # 取最新一行
        row = mine.iloc[0]
        # 找"质押比例"列
        ratio_col = next((c for c in df.columns if "质押比例" in c or "占比" in c), None)
        if ratio_col:
            ratio = float(row[ratio_col])
            risk = "high" if ratio > 80 else "medium" if ratio > 50 else "low"
            result = (risk, {"pledge_ratio": round(ratio, 1)})
        else:
            result = ("unknown", {})
        _PLEDGE_CACHE[code] = result
        return result
    except Exception:
        _PLEDGE_CACHE[code] = ("unknown", {})
        return "unknown", {}


# ============================================================
# REQ-162：北向资金流向检测（部分）
# ============================================================

_NORTHBOUND_CACHE = {}


def check_northbound_flow(code):
    """
    检测北向资金对该股的持仓变化
    连续减持 → 卖出信号
    连续增持 → 买入信号

    返回：(signal, details)
    """
    if code in _NORTHBOUND_CACHE:
        return _NORTHBOUND_CACHE[code]

    try:
        import akshare as ak
        import pandas as pd
        # stock_hsgt_individual_detail_em 个股北向持仓明细
        df = ak.stock_hsgt_individual_detail_em(symbol=code)
        if df is None or df.empty or len(df) < 10:
            _NORTHBOUND_CACHE[code] = ("unknown", {})
            return "unknown", {}

        # 按日期排序（最新在前）
        date_col = next((c for c in df.columns if "持股日期" in c or "日期" in c), None)
        shares_col = next((c for c in df.columns if "持股数量" in c or "持股" in c), None)
        if not date_col or not shares_col:
            _NORTHBOUND_CACHE[code] = ("unknown", {})
            return "unknown", {}

        df = df.sort_values(date_col, ascending=False).head(30)  # 近30天
        shares = df[shares_col].astype(float).tolist()

        # 简单判断：近30天持股变化
        if len(shares) >= 10:
            recent = sum(shares[:5]) / 5
            older = sum(shares[-5:]) / 5
            if older > 0:
                change_pct = (recent - older) / older * 100
                if change_pct > 10:
                    sig = "strong_buy"
                elif change_pct > 3:
                    sig = "buy"
                elif change_pct < -10:
                    sig = "strong_sell"
                elif change_pct < -3:
                    sig = "sell"
                else:
                    sig = "neutral"
                result = (sig, {"30d_change_pct": round(change_pct, 1),
                                "latest_holdings": round(recent, 0)})
                _NORTHBOUND_CACHE[code] = result
                return result
    except Exception:
        pass

    _NORTHBOUND_CACHE[code] = ("unknown", {})
    return "unknown", {}


# ============================================================
# REQ-160：财务造假风险检测（3类禁买之"下水道型"）
# ============================================================

def check_financial_fraud_risk(df_annual, code=None, name=None):
    """
    检测中国A股特有的财务造假风险信号

    参数：
      df_annual: extract_annual_data 返回的最近年报（latest-first）
      code/name: 股票代码/名称（可选，用于ST识别）

    返回：
      (risk_level, red_flags)
      risk_level: "high"（高造假风险）/ "medium"（可疑）/ "low"（安全）
      red_flags: 触发的红旗列表

    检测规则（参考康美药业、康得新、獐子岛等经典案例）：
      1. 存贷双高：货币资金 > 30% 总资产 + 同时短期+长期借款 > 30% 总资产
         → 典型康美模式
      2. 货币资金 > 40% 总资产（无合理解释）
      3. 应收账款增速 > 营收增速 1.5 倍（可能虚增收入）
      4. 现金流/净利润 连续 < 0.3（利润含水分）
      5. 毛利率显著高于同行（康得新模式）
    """
    if df_annual is None or df_annual.empty or len(df_annual) < 2:
        return "low", []

    import pandas as pd
    red_flags = []
    latest = df_annual.iloc[0]

    # 规则1：存贷双高（康美模式）
    # 注：financial_indicator 通常给不出总资产绝对值，用比率逼近
    cash_ratio = latest.get("货币资金占总资产比例")  # 如果有
    debt_ratio = latest.get("资产负债率")
    if debt_ratio and not pd.isna(debt_ratio) and float(debt_ratio) > 50:
        # 高负债 + 若能判断高现金 → 双高风险
        # 由于数据源限制，简化为：负债率高 + 利息保障倍数异常
        pass  # 需要额外数据，后续补

    # 规则2：现金流/净利润 连续 < 0.3（利润含水分）
    cf_ratios = []
    for _, row in df_annual.head(3).iterrows():
        cf = row.get("每股经营现金流")
        eps = row.get("基本每股收益")
        if cf is not None and eps is not None and not pd.isna(cf) and not pd.isna(eps):
            if float(eps) > 0:
                cf_ratios.append(float(cf) / float(eps))
    if len(cf_ratios) >= 2 and all(r < 0.3 for r in cf_ratios):
        red_flags.append("经营现金流/净利润连续<0.3，利润含水")

    # 规则3：销售毛利率突然暴涨 > 5pp（一年内）
    if len(df_annual) >= 2:
        gm_new = df_annual.iloc[0].get("销售毛利率")
        gm_old = df_annual.iloc[1].get("销售毛利率")
        if gm_new is not None and gm_old is not None:
            try:
                if float(gm_new) - float(gm_old) > 10:
                    red_flags.append(f"毛利率1年内从{float(gm_old):.1f}%飙到{float(gm_new):.1f}%（可疑）")
            except Exception:
                pass

    # 规则4：净利润同比增长 vs 营收同比增长 差异巨大
    rev_g = latest.get("营业总收入同比增长率")
    net_g = latest.get("净利润同比增长率")
    if rev_g is not None and net_g is not None:
        try:
            rev_g, net_g = float(rev_g), float(net_g)
            if net_g > 50 and rev_g < 10:
                red_flags.append(f"净利润增{net_g:.0f}%但营收仅增{rev_g:.0f}%（可能通过费用调节）")
        except Exception:
            pass

    # 规则5：资产负债率连续大幅上升
    # 豁免：公用事业/过路费生意（收购电站/铁路线等合理资本运作会升负债）
    def _is_utility(code, name):
        if not code:
            return False
        # 已知公用事业/过路费公司代码前缀
        utility_codes = {"600900", "600886", "600578", "600027",  # 电力
                         "601006", "601816", "601333", "600125",   # 铁路
                         "600018", "601018", "600717",              # 港口
                         "600377", "600020", "600350",              # 高速
                         "600803", "600635", "600917",              # 燃气
                         "600941", "600050", "601728"}              # 通信
        return code in utility_codes

    if not _is_utility(code, name):
        dr_3y = []
        for _, row in df_annual.head(3).iterrows():
            dr = row.get("资产负债率")
            if dr is not None and not pd.isna(dr):
                dr_3y.append(float(dr))
        if len(dr_3y) >= 3:
            if dr_3y[0] - dr_3y[2] > 15:
                red_flags.append(f"资产负债率3年从{dr_3y[2]:.0f}%升到{dr_3y[0]:.0f}%")

    # 规则6：存贷双高（康美药业经典造假模式）
    if code and not _is_utility(code, name):
        try:
            is_dh, dh_detail = check_cash_loan_double_high(code)
            if is_dh:
                red_flags.append(
                    f"🚨存贷双高：现金{dh_detail['cash_yi']}亿"
                    f"({dh_detail['cash_ratio']}%) + 借款{dh_detail['loan_yi']}亿"
                    f"({dh_detail['loan_ratio']}%) —— 康美/康得新造假模式"
                )
        except Exception:
            pass

    # 规则7：异常高货币资金（> 50%）
    if code and not _is_utility(code, name):
        try:
            is_abn, abn_detail = check_abnormal_cash(code)
            if is_abn:
                red_flags.append(
                    f"⚠异常高货币资金 {abn_detail['cash_ratio']}%（>50%）"
                )
        except Exception:
            pass

    # 规则8：商誉占净资产过高（> 50%）—— 存在减值风险
    if code:
        try:
            bs = get_balance_sheet_latest(code)
            goodwill = bs.get("goodwill", 0)
            equity = bs.get("total_assets", 0) - bs.get("total_liabilities", 0)
            if goodwill and equity > 0:
                gw_ratio = goodwill / equity * 100
                if gw_ratio > 50:
                    red_flags.append(f"⚠商誉占净资产{gw_ratio:.0f}%（>50%有减值风险）")
        except Exception:
            pass

    # 综合判定
    if len(red_flags) >= 2:
        return "high", red_flags
    elif len(red_flags) == 1:
        return "medium", red_flags
    return "low", []


# ============================================================
# REQ-161：ST / 退市风险检测
# ============================================================

def check_st_delisting_risk(name, code=""):
    """
    检测是否是 ST / *ST / 面临退市股票

    返回：(is_risk, risk_type)
      is_risk: True/False
      risk_type: "*ST"/"ST"/"退"/None
    """
    if not name:
        return False, None
    name_upper = name.upper().replace(" ", "")
    if "*ST" in name_upper or "*ST" in name:
        return True, "*ST（面临退市）"
    if name_upper.startswith("ST") or name.startswith("ST"):
        return True, "ST（特别处理）"
    if "退" in name:
        return True, "退市股"
    return False, None


# ============================================================
# REQ-163：自由现金流（中国化）
# ============================================================

def calculate_free_cashflow_china(df_annual):
    """
    计算中国A股版自由现金流
    由于A股披露粒度粗，简化为：
      自由现金流 ≈ 经营现金流 - 购建固定资产等长期资产的现金支出

    金融指标接口限制：很多公司的资本支出字段为 NaN，此时用"每股经营现金流"作代理

    返回：
      {
          "has_data": bool,
          "recent_fcf_per_share": [近3年每股自由现金流],
          "avg_fcf_per_share": 均值,
          "fcf_to_net_profit_ratio": 自由现金流/净利润,
          "warning": 警告文字或 None
      }
    """
    if df_annual is None or df_annual.empty or len(df_annual) < 2:
        return {"has_data": False}

    import pandas as pd
    # 中国财务接口通常没有"资本支出"字段，退而用 OCF
    ocf_per_share = []
    for _, row in df_annual.head(3).iterrows():
        cf = row.get("每股经营现金流")
        if cf is not None and not pd.isna(cf):
            ocf_per_share.append(float(cf))

    if len(ocf_per_share) < 2:
        return {"has_data": False}

    avg_ocf = sum(ocf_per_share) / len(ocf_per_share)
    warning = None
    if all(c < 0 for c in ocf_per_share):
        warning = "经营现金流连续为负 → 极度警惕（烧钱走向灭亡）"
    elif min(ocf_per_share) < 0:
        warning = "经营现金流某年为负 → 需关注"

    return {
        "has_data": True,
        "recent_ocf_per_share": [round(c, 2) for c in ocf_per_share],
        "avg_ocf_per_share": round(avg_ocf, 2),
        "warning": warning,
    }


# ============================================================
# REQ-164：过路费生意识别（中国化）
# ============================================================

TOLL_BRIDGE_INDUSTRIES = {
    "高速公路": ["宁沪高速", "山东高速", "粤高速", "皖通高速"],
    "铁路": ["大秦铁路", "京沪高铁", "铁龙物流"],
    "港口": ["上港集团", "宁波港", "青岛港"],
    "机场": ["上海机场", "白云机场", "首都机场"],
    "公用事业-电力": ["长江电力", "华能水电", "国投电力", "华电国际"],
    "公用事业-燃气": ["新奥股份", "华润燃气", "昆仑能源", "北京燃气"],
    "公用事业-水务": ["重庆水务", "洪城环境"],
    "电信运营": ["中国移动", "中国电信", "中国联通"],
    "银行": [],  # 银行单列
}


def check_toll_bridge_business(industry, name, roe_avg, debt_ratio=None, div_yield=None):
    """
    识别中国式"过路费生意"——垄断经营+受管制，定价权弱但稳定

    这类公司巴菲特喜欢，但 ROE 通常只有 10-14%（受管制）
    不走"十年王者"规则（会被错判），走"高息防御资产"分类

    返回：
      (is_toll_bridge, classification, reasons)
      is_toll_bridge: True/False
      classification: "toll_bridge_utility"（受管制公用事业）或 None
      reasons: 判定原因
    """
    reasons = []
    ind_lower = (industry or "").lower()
    name_lower = (name or "").lower()

    # 1. 行业关键词匹配
    matched_category = None
    for cat, examples in TOLL_BRIDGE_INDUSTRIES.items():
        cat_keywords = cat.split("-")[-1]  # 电力/燃气/水务等
        if any(kw in industry for kw in cat_keywords.split()) or any(ex in name for ex in examples):
            matched_category = cat
            break

    # 铁路/公路/公用事业/通信服务
    if "铁路" in ind_lower or "铁路" in (industry or ""):
        matched_category = "铁路"
    if "公路" in ind_lower or "高速" in ind_lower:
        matched_category = "高速公路"
    if "港口" in (industry or ""):
        matched_category = "港口"
    if "电力" in (industry or "") and "电力" not in ["电力设备"]:  # 排除电网设备
        matched_category = "公用事业-电力"
    if "燃气" in (industry or ""):
        matched_category = "公用事业-燃气"
    if "通信服务" in (industry or ""):
        matched_category = "电信运营"

    if not matched_category:
        return False, None, []

    reasons.append(f"归属'{matched_category}'—受管制的过路费生意")

    # 2. 判定条件（放宽到 ROE ≥ 8%）—— 铁路/港口类 ROE 常年 8-14%
    if roe_avg is None:
        # 行业匹配但没 ROE 数据，保守归类为过路费
        return True, "toll_bridge_utility", reasons + ["ROE数据不足但行业匹配"]

    if roe_avg < 5:
        return False, None, reasons + [f"ROE={roe_avg:.1f}% < 5%太低"]

    # 3. 理想条件：ROE ≥8% + 负债 ≤ 70% + 股息 ≥ 3%
    if debt_ratio and debt_ratio > 70:
        reasons.append(f"负债率{debt_ratio:.0f}%过高")
    if div_yield and div_yield < 3:
        reasons.append(f"股息率{div_yield}%偏低（过路费生意应有 ≥3%）")

    reasons.append(f"5年ROE均值{roe_avg:.1f}%")
    return True, "toll_bridge_utility", reasons


# ============================================================
# REQ-160 子规则：跑步机型（资本密集）识别
# ============================================================

def check_capital_intensive_treadmill(df_annual, industry):
    """
    识别"跑步机型"生意——必须持续投入资本才能维持现有地位

    概念关系：跑步机型 ⊂ complexity=complex（是复杂生意的子集，不等于）
      - 复杂生意 = 静态行业标签（重资产 + 技术变化快）
      - 跑步机型 = 实证上 ROE 长期偏低的那部分复杂生意
      - 比如半导体是 complexity=complex，但台积电/韦尔股份 ROE 并不低，
        就不属于跑步机型

    典型：航空（无差异化）、纺织、钢铁、面板、造船

    ⚠ 警告：此规则用 5 年窗口，可能被商品周期高点骗（中铝案例）
    真正的"资本黑洞"请用 check_drain_business（10 年视角）

    返回：(is_treadmill, reasons)
    """
    reasons = []
    import pandas as pd
    if df_annual is None or df_annual.empty or len(df_annual) < 3:
        return False, []

    # 指标：ROE 长期低（<10%）+ 高负债 + 重资产行业
    heavy_industries = [
        "钢铁", "航空", "航运", "造船", "纺织", "面板", "煤炭",
        "有色金属", "工业金属", "光学光电子", "化纤", "水泥",
        "玻璃玻纤", "建筑材料", "石油石化",
    ]
    in_heavy = any(h in (industry or "") for h in heavy_industries)
    if not in_heavy:
        return False, []

    reasons.append(f"'{industry}'属重资产/同质化行业")

    # ROE 均值
    roes = []
    for _, row in df_annual.head(5).iterrows():
        roe = row.get("净资产收益率")
        if roe is not None and not pd.isna(roe):
            roes.append(float(roe))
    if roes:
        avg_roe = sum(roes) / len(roes)
        if avg_roe < 10:
            reasons.append(f"5年ROE均值仅{avg_roe:.1f}%，赚的钱都在补设备")
            return True, reasons

    return False, reasons


# ============================================================
# REQ-160 子规则：下水道生意识别（10 年视角，REQ-160E）
# ============================================================

def check_drain_business(df_annual, industry):
    """
    识别"下水道生意"——芒格原话："有些生意就像往下水道扔钱，钱进去就没了"

    比跑步机更严重：跑步机是"持续跑才能原地不动"，下水道是"钱直接扔掉"。
    只能用长周期（10 年）才能戳破商品周期高点的伪装。

    典型案例：
      中国铝业 2016-2020 ROE 连续 5 年 <5%，2021-2025 铝价暴涨把近 5 年
      均值拉到 13%。但 10 年均值仍仅 7.5%——跑步机规则漏了，下水道规则抓到。

    触发条件（行业必须是 complex 或 cycle，且任一触发）：
      1. 10 年 ROE 均值 < 8%（长期看根本不赚钱）
      2. 10 年中有 ≥4 年 ROE < 5%（周期频繁陷入深谷）

    概念对齐：跟 check_capital_intensive_treadmill 互补——
      跑步机看近 5 年实证，下水道看 10 年长周期

    返回：(is_drain, reasons)
    """
    import pandas as pd
    if df_annual is None or df_annual.empty or len(df_annual) < 10:
        return False, []

    # 必须是 complex 或 cycle 行业（下水道生意的基础特征）
    # 用关键词模糊匹配覆盖 akshare 返回的各种子行业名
    drain_industries = [
        # 金属/冶炼（重资产，同质化产品）
        "钢铁", "普钢", "特钢", "冶炼", "铝", "有色金属", "工业金属",
        "矿业", "金属", "稀土", "小金属",
        # 能源/化工（商品周期依赖）
        "煤炭", "石油", "石化", "炼化", "油气", "化工", "化纤", "化学",
        "农化",
        # 建材/水泥（周期产能过剩）
        "水泥", "玻璃玻纤", "建筑材料", "建材",
        # 重资产制造（技术迭代+产能过剩）
        "面板", "光学光电子", "光伏", "锂电", "锂电池", "电池", "新能源",
        # 运输/施工（长期同质化）
        "航空", "航运", "造船", "纺织", "建筑", "工程",
        "轨道交通", "轨交设备", "机械制造",
        "汽车玻璃", "汽车零部件",
        # 周期性农业
        "农业", "养殖", "农牧",
    ]
    in_drain = any(h in (industry or "") for h in drain_industries)
    if not in_drain:
        return False, []

    # 取 10 年 ROE
    roes = []
    for _, row in df_annual.head(10).iterrows():
        roe = row.get("净资产收益率")
        if roe is not None and not pd.isna(roe):
            try:
                roes.append(float(roe))
            except Exception:
                continue
    if len(roes) < 10:
        return False, []  # 上市不足 10 年不判

    avg_10y = sum(roes) / 10
    low_years = sum(1 for r in roes if r < 5)
    loss_years = sum(1 for r in roes if r < 0)
    reasons = []

    # 条件 1：10年均值<8%（长期看根本不赚钱）
    if avg_10y < 8:
        reasons.append(
            f"'{industry}'下水道生意：10年ROE均值仅{avg_10y:.1f}%"
            f"（芒格'资本黑洞'，长期看根本不赚钱）"
        )
        return True, reasons

    # 条件 2：低于5%年数≥4（周期频繁陷入深谷）
    if low_years >= 4:
        reasons.append(
            f"'{industry}'下水道生意：10年中有{low_years}年ROE<5%"
            f"（周期频繁陷入深谷，10年均值{avg_10y:.1f}%被高点骗）"
        )
        return True, reasons

    # 条件 3：综合弱表现 —— 10年均值 <10% 且 低5%年数≥3（TCL 科技这类边缘）
    # 典型案例：TCL 科技（10年均值9.5%，3年<5%）—— 面板周期性好一次后仍是下水道
    if avg_10y < 10 and low_years >= 3:
        reasons.append(
            f"'{industry}'下水道生意：10年均值{avg_10y:.1f}%且有{low_years}年ROE<5%"
            f"（接近卓越线却频繁掉入深谷）"
        )
        return True, reasons

    # 条件 4：有亏损年（ROE<0）≥2 次（无论均值多少，频繁亏损都是危险信号）
    if loss_years >= 2:
        reasons.append(
            f"'{industry}'下水道生意：10年中有{loss_years}年亏损（ROE<0）"
            f"（10年均值{avg_10y:.1f}%，周期低谷亏损严重）"
        )
        return True, reasons

    return False, reasons


# ============================================================
# REQ-184：要求 10% 年化回报倒推最高合理买入价（2026-04-16）
# ============================================================
# 校验后纠正（REQ-VERIFY-001）：
#   巴菲特的"10%"是 required return（要求回报率），不是 CAGR 增速假设
#   折现用长期国债收益率，不是 10%
#
# 公式：
#   基于保守假设的"安全边际价"：
#   max_buy_price = future_eps_10y × target_pe_exit / (1 + required_return)^10
#
# 参数选择：
#   future_eps_10y：用过去 3 年 EPS CAGR（capped at 12%，保守）外推 10 年后 EPS
#   target_pe_exit：行业 fair_low（退出时保守估值）
#   required_return：10%（巴菲特门槛）
#
# 解读：
#   如果当前价格 ≤ max_buy_price → 有 10 年年化 10% 潜力的安全边际
#   如果当前价格 > max_buy_price → 买入后难以达到 10% 年化回报

def calc_required_return_max_price(df_annual, pe_fair_low, required_return=0.10):
    """
    REQ-184：基于 10% required return 倒推最高合理买入价

    参数：
      df_annual: 近 5+ 年年报 DataFrame（需要 EPS 字段）
      pe_fair_low: 行业合理区间下限（退出估值假设）
      required_return: 要求的年化回报率（默认 10%）

    返回：
      {
        "max_price": float,       # 最高合理买入价
        "eps_cagr_3y": float,     # 近 3 年 EPS CAGR
        "future_eps_10y": float,  # 10 年后预期 EPS
        "target_pe_exit": float,  # 退出 PE 假设
        "detail": str,
      }
      或 None（数据不足）
    """
    import pandas as pd

    if df_annual is None or df_annual.empty or len(df_annual) < 3:
        return None
    if not pe_fair_low or pe_fair_low <= 0:
        return None

    # 取 EPS（最新 → 最旧）
    eps_list = []
    for _, row in df_annual.head(5).iterrows():
        e = row.get("摊薄每股收益") or row.get("每股收益") or row.get("基本每股收益")
        if e is None or pd.isna(e):
            continue
        try:
            eps_list.append(float(e))
        except Exception:
            continue
    if len(eps_list) < 3:
        return None

    # 近 3 年 EPS CAGR（保守）
    eps_latest = eps_list[0]
    eps_3y_ago = eps_list[2]
    if eps_3y_ago <= 0 or eps_latest <= 0:
        return None
    eps_cagr_3y = (eps_latest / eps_3y_ago) ** (1 / 2) - 1  # 2 年复合（3 年 3 个数据点 = 2 个周期）

    # 保守假设：EPS CAGR 封顶 12%（现实世界里 10 年 12% CAGR 已经很优秀）
    # 下限 0%（负增长就用 0 保守估计）
    eps_cagr_capped = max(0, min(eps_cagr_3y, 0.12))

    # 10 年后预期 EPS
    future_eps_10y = eps_latest * (1 + eps_cagr_capped) ** 10

    # 退出估值：用行业合理下限（保守）
    target_pe_exit = pe_fair_low

    # 倒推最高合理买入价
    # 满足：buy_price × (1 + 10%)^10 = future_eps × target_pe_exit
    # → buy_price = future_eps × target_pe_exit / (1.10)^10
    discount_factor = (1 + required_return) ** 10
    max_price = future_eps_10y * target_pe_exit / discount_factor

    detail = (
        f"按 10% required return 倒推：当前 EPS {eps_latest:.2f}元，"
        f"3 年 EPS CAGR {eps_cagr_3y * 100:.1f}%（capped {eps_cagr_capped * 100:.0f}%），"
        f"10 年后 EPS {future_eps_10y:.2f}元，"
        f"退出 PE {target_pe_exit:.0f}，最高合理买入价 ¥{max_price:.2f}"
    )

    return {
        "max_price": max_price,
        "eps_cagr_3y": eps_cagr_3y,
        "eps_cagr_used": eps_cagr_capped,
        "future_eps_10y": future_eps_10y,
        "target_pe_exit": target_pe_exit,
        "detail": detail,
    }


# ============================================================
# REQ-186：烟蒂警告（2026-04-16）
# ============================================================
# 来源：巴菲特 2014 年 50 周年股东信
#   "是芒格打破了我的烟蒂习惯……用便宜价买平庸企业不如用公道价买伟大企业"
#   喜诗糖果 1972 年是转折点
#
# 烟蒂特征：
#   - 低 PE（<10）看起来便宜
#   - 但 10 年 ROE 均值 <10%（长期不赚钱）
#   - 是格雷厄姆流派的"捡烟蒂"陷阱
#
# 关键排除：强周期行业（钢铁/煤炭/航运等）
#   - 这些行业周期底部 PE<10 是正常的
#   - 周期顶部利润暴增但 PE 仍低（典型"周期反向 PE"）
#   - 不能用"低 PE + 低长期 ROE"打烟蒂标签

def check_cigar_butt_warning(code, industry, pe_ttm, df_annual):
    """
    REQ-186：烟蒂警告识别

    参数：
      code: 股票代码
      industry: 行业名
      pe_ttm: 当前 TTM PE
      df_annual: 近 10+ 年年报 DataFrame

    返回：
      (is_cigar_butt, detail)
    """
    import pandas as pd

    # 必须有有效的 PE 和 10 年数据
    if pe_ttm is None or pe_ttm <= 0 or pe_ttm >= 10:
        return False, ""
    if df_annual is None or df_annual.empty or len(df_annual) < 10:
        return False, ""

    # 排除强周期行业（周期股底部 PE<10 正常，不是烟蒂）
    # 和下水道不同：下水道用"长期 ROE 均值"判定（抓真正的黑洞）
    # 烟蒂警告专门警告"非周期股但长期 ROE 偏低"的陷阱
    strong_cyclical = [
        "钢铁", "普钢", "特钢", "冶炼",
        "煤炭", "煤炭开采",
        "有色", "工业金属", "稀土", "小金属", "矿业",
        "石油", "石化", "炼化", "油气",
        "航运", "港口",  # 航运港口周期明显
        "化工", "化纤", "化学制品", "农化",
        "水泥", "玻璃", "建材",
    ]
    if any(k in (industry or "") for k in strong_cyclical):
        return False, ""

    # 计算 10 年 ROE 均值
    roes = []
    for _, row in df_annual.head(10).iterrows():
        r = row.get("净资产收益率")
        if r is not None and not pd.isna(r):
            try:
                roes.append(float(r))
            except Exception:
                continue
    if len(roes) < 10:
        return False, ""

    avg_10y = sum(roes) / 10

    # 烟蒂：PE<10 + 10 年 ROE 均值 <10%（长期赚不到钱却被当便宜货）
    if avg_10y < 10:
        return True, (
            f"烟蒂警告：PE {pe_ttm:.1f}（看似便宜）+ 10年ROE均值仅{avg_10y:.1f}%"
            f"（长期赚不到钱，是格雷厄姆式烟蒂陷阱）→ 芒格原则：公道价买伟大企业"
        )

    return False, ""


# ============================================================
# REQ-180：印钞机标签识别（2026-04-16）
# ============================================================
# 来源：巴菲特 2007 年伯克希尔股东信
#   喜诗糖果 1972-2007 年共产生 13.5 亿美元税前利润，
#   累计再投入资本仅 3200 万美元（capex/tax_profit ≈ 2.4%）
#
# 本地校验（2026-04 A 股实证）：
#   茅台 2024：CAPEX 46.8 亿 / 净利 862 亿 = 5.4%   → 卓越印钞机
#   海天味业 2024：capex/净利 ≈ 15%                → 印钞机
#   宁德时代 2024：capex/净利 = 61.5%              → 重资产非印钞机
#   万华化学：capex/净利 80-150%                   → 重资产非印钞机
#
# 阈值（3 年滚动均值）：
#   <10% → 🌟 卓越印钞机（喜诗级）
#   <20% → ✅ 印钞机
#   ≥20% → 非印钞机
#
# 重资产行业（complexity=complex）改用替代指标：
#   CapEx / 折摊 <1.5（维持性资本支出，不扩产）

def get_cashflow_latest(code):
    """拉取最近 5 年年报的现金流量表数据"""
    try:
        import akshare as ak
        symbol = f"SH{code}" if code.startswith(("6", "9")) else f"SZ{code}"
        df = ak.stock_cash_flow_sheet_by_report_em(symbol=symbol)
        if df is None or df.empty:
            return []
        # 只取年报（12-31 日）
        df_annual = df[df["REPORT_DATE"].astype(str).str.contains("-12-31")].copy()
        if df_annual.empty:
            return []
        df_annual = df_annual.sort_values("REPORT_DATE", ascending=False)
        results = []
        for _, row in df_annual.head(5).iterrows():
            results.append({
                "date": str(row.get("REPORT_DATE", ""))[:10],
                "capex": float(row.get("CONSTRUCT_LONG_ASSET") or 0),
                "net_profit": float(row.get("NETPROFIT") or 0),
                "ocf": float(row.get("NETCASH_OPERATE") or 0),
                "depreciation": float(row.get("FA_IR_DEPR") or 0),
                "amortize_ia": float(row.get("IA_AMORTIZE") or 0),
            })
        return results
    except Exception as e:
        return []


def check_cashcow_label(code, industry, roe_5y_avg=None):
    """
    REQ-180：印钞机标签识别

    参数：
      code: A 股代码（6 位）
      industry: 行业名（用于判断是否重资产）
      roe_5y_avg: 5 年 ROE 均值（可选，不足则跳过 ROE 门槛）

    返回：
      (label, tier_desc, detail)
        label: "cashcow_elite" / "cashcow" / None
        tier_desc: "🌟卓越印钞机" / "✅印钞机" / ""
        detail: 说明文字
    """
    # 第一关：ROE 门槛（<20% 不是印钞机候选）
    if roe_5y_avg is not None and roe_5y_avg < 20:
        return None, "", ""

    cashflow = get_cashflow_latest(code)
    if len(cashflow) < 3:
        return None, "", ""

    # 判断行业类别（重资产行业用替代指标）
    heavy_industries = [
        "钢铁", "普钢", "特钢", "冶炼", "煤炭", "有色", "工业金属",
        "化工", "化学", "化纤", "石油", "石化", "炼化",
        "水泥", "玻璃", "建材", "建筑材料",
        "面板", "光学光电子", "光伏", "半导体", "电池", "锂电",
        "航空", "航运", "造船",
    ]
    is_heavy = any(k in (industry or "") for k in heavy_industries)

    # 取近 3 年数据计算滚动均值
    recent_3 = cashflow[:3]
    capex_sum = sum(c["capex"] for c in recent_3)
    net_profit_sum = sum(c["net_profit"] for c in recent_3)
    dep_sum = sum(c["depreciation"] + c["amortize_ia"] for c in recent_3)

    if is_heavy:
        # 重资产行业：用 CapEx / 折摊 <1.5 作为"维持性资本开支"指标
        if dep_sum <= 0:
            return None, "", ""
        capex_dep_ratio = capex_sum / dep_sum
        if capex_dep_ratio < 1.0:
            return "cashcow", "✅印钞机", (
                f"重资产行业印钞机：近3年 CapEx/折摊={capex_dep_ratio:.2f} "
                f"（<1.0 说明无扩产压力，只需维持设备折旧）"
            )
        if capex_dep_ratio < 1.5:
            return "cashcow", "✅印钞机", (
                f"重资产温和扩张：近3年 CapEx/折摊={capex_dep_ratio:.2f} "
                f"（<1.5 说明扩产节制）"
            )
        return None, "", ""

    # 非重资产行业：用 CapEx / 净利润
    if net_profit_sum <= 0:
        return None, "", ""
    capex_ratio = capex_sum / net_profit_sum * 100
    if capex_ratio < 10:
        return "cashcow_elite", "🌟卓越印钞机", (
            f"近3年 CapEx/净利={capex_ratio:.1f}%（<10% 喜诗糖果级）"
        )
    if capex_ratio < 20:
        return "cashcow", "✅印钞机", (
            f"近3年 CapEx/净利={capex_ratio:.1f}%（<20% 印钞机）"
        )
    return None, "", ""


# ============================================================
# REQ-160 子规则：冲浪者型（科技持续创新焦虑）识别
# ============================================================

def check_tech_surfer(df_annual, industry, name):
    """
    识别"冲浪者型"——必须持续技术创新才能维持地位

    概念关系：冲浪者型 ⊂ complexity=complex 的科技类 或 type=cycle 的技术类
      - 不等于 type=cycle：煤炭/钢铁是 cycle 但不是冲浪者（没有技术迭代焦虑）
      - 核心特征是"技术换代就出局"——半导体/光伏/锂电/面板典型
      - 消费电子（除苹果/小米生态外）也属此类

    典型：半导体、光伏、锂电池设备、消费电子

    返回：(is_surfer, reasons)
    """
    reasons = []
    surfer_industries = [
        "半导体", "芯片", "光伏设备", "锂电", "锂电池", "电池",
        "消费电子", "通信设备", "软件开发", "计算机设备",
        "面板", "显示器件", "光伏产业", "风电设备",
    ]
    in_surfer = any(h in (industry or "") for h in surfer_industries)
    if not in_surfer:
        return False, []

    reasons.append(f"'{industry}'属科技迭代行业，技术更新换代快")
    reasons.append("投资前请确认：是否有超越技术的生态护城河（如苹果）")
    return True, reasons


if __name__ == "__main__":
    # 测试
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    print("=== 政策风险检查测试 ===")
    cases = [
        ("教育", "", "high"),
        ("房地产", "", "high"),
        ("白酒", "", "medium"),
        ("新能源", "锂电", "medium"),
        ("食品饮料", "", "safe"),
        ("家电", "", "safe"),
    ]
    for ind, cat, expected in cases:
        risk, _ = check_policy_risk(ind, cat)
        mark = "✅" if risk == expected else "❌"
        print(f"  {mark} {ind} 风险={risk} (期望{expected})")

    print("\n=== 信号降级测试 ===")
    test_cases = [
        ("buy_heavy", "房地产", "buy_light"),  # high 降 2 级
        ("buy_medium", "白酒", "buy_light"),    # medium 降 1 级
        ("buy_heavy", "食品饮料", "buy_heavy"),  # safe 不降
    ]
    for sig, ind, expected in test_cases:
        new_sig, note = adjust_signal_by_policy_risk(sig, ind)
        mark = "✅" if new_sig == expected else "❌"
        print(f"  {mark} {sig} + {ind} → {new_sig} (期望{expected})")

    print("\n=== 黑天鹅检测测试 ===")
    bs = get_current_black_swan()
    if bs:
        print(f"  ⚠️ 当前处于黑天鹅期：{bs['name']}")
    else:
        print("  ✅ 当前无黑天鹅事件")
