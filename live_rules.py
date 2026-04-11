"""
实时模型规则引擎 - 与 backtest_engine.py 保持逻辑一致的规则函数

输入：df_annual（akshare 拉取的年报 DataFrame，已按日期降序）
输出：规则判定结果

与 backtest_engine.py 的对应关系：
  check_10_year_king_live    ←→ backtest_engine.check_10_year_king
  is_good_quality_live       ←→ backtest_engine.is_good_quality_company
  check_moat_live            ←→ backtest_engine.check_moat_normal (8 条规则)
  check_consumer_leader_live ←→ backtest_engine.get_cash_flow_warnings

修改规则时请同步修改两边（未来可能合并为单一规则模块）
"""

import numpy as np
import pandas as pd

from data_fetcher import (
    extract_annual_data,
    find_column,
    get_roe_series,
    get_fcf_series,
    get_debt_info,
)


# ============================================================
# 工具：从 df_annual 提取指标序列
# ============================================================

def _get_gross_margin_series(df_annual):
    col = find_column(df_annual, ["销售毛利率", "毛利率"])
    if col is None:
        return None
    return pd.to_numeric(df_annual[col], errors="coerce").dropna()


def _get_debt_ratio_series(df_annual):
    col = find_column(df_annual, ["资产负债率"])
    if col is None:
        return None
    return pd.to_numeric(df_annual[col], errors="coerce").dropna()


def _get_revenue_growth_series(df_annual):
    col = find_column(df_annual, ["营业总收入同比增长率", "营业收入同比增长率", "营业总收入增长率"])
    if col is None:
        return None
    return pd.to_numeric(df_annual[col], errors="coerce").dropna()


def _get_eps_series(df_annual):
    col = find_column(df_annual, ["基本每股收益"])
    if col is None:
        return None
    return pd.to_numeric(df_annual[col], errors="coerce").dropna()


# ============================================================
# 十年王者判定（巴菲特核心：熊市买十年王者）
# ============================================================

def check_10_year_king_live(df_annual):
    """
    十年王者判定（与 backtest_engine.check_10_year_king 逻辑一致）
    4 个条件缺一不可：
      1. 近 10 年 ROE 均值 ≥ 15%
      2. 近 10 年至少 7 年 ROE ≥ 15%
      3. 最近 2 年 ROE 没有连续低于 10%
      4. 最新 1 年 ROE 不为负

    返回 (is_king, avg_roe, years_above)
    """
    if df_annual is None or df_annual.empty:
        return False, None, 0
    roe_series = get_roe_series(df_annual)
    if roe_series is None or len(roe_series) < 7:
        return False, None, 0

    recent_10 = roe_series.head(10).values
    avg_10y = float(recent_10.mean())

    if avg_10y < 15:
        return False, avg_10y, 0

    years_above = int(sum(1 for r in recent_10 if r >= 15))
    if years_above < 7:
        return False, avg_10y, years_above

    if len(recent_10) >= 2 and recent_10[0] < 10 and recent_10[1] < 10:
        return False, avg_10y, years_above

    if recent_10[0] < 0:
        return False, avg_10y, years_above

    return True, avg_10y, years_above


# ============================================================
# 好公司判定（合理价格买好公司规则用）
# ============================================================

def is_good_quality_live(df_annual):
    """
    判定是否为"好公司"
    两种情况任一满足即可：
      A. 十年王者
      B. 近 5 年 ROE 均值 ≥ 20% 且 最新毛利率 ≥ 30%
    """
    if df_annual is None or df_annual.empty:
        return False

    is_king, _, _ = check_10_year_king_live(df_annual)
    if is_king:
        return True

    roe_series = get_roe_series(df_annual)
    if roe_series is None or len(roe_series) < 3:
        return False
    avg_5y = float(roe_series.head(5).mean())
    if avg_5y < 20:
        return False

    gm_series = _get_gross_margin_series(df_annual)
    if gm_series is None or len(gm_series) < 1:
        return False
    if float(gm_series.iloc[0]) < 30:
        return False

    return True


# ============================================================
# 护城河检查（8 条规则，与 check_moat_normal 一致）
# ============================================================

def check_moat_live(df_annual, industry=""):
    """
    护城河检查（与 backtest_engine.check_moat_normal 一致的 8 条规则）
    返回 (is_intact, problems)
    """
    if df_annual is None or df_annual.empty:
        return True, []

    # 数据不足时返回完好（疑罪从无）
    roe_series = get_roe_series(df_annual)
    if roe_series is None or len(roe_series) < 2:
        return True, []

    problems = []
    roe_list = roe_series.head(6).tolist()  # 最新在前
    gm_series = _get_gross_margin_series(df_annual)
    gm_list = gm_series.head(6).tolist() if gm_series is not None else []
    rev_series = _get_revenue_growth_series(df_annual)
    rev_list = rev_series.head(6).tolist() if rev_series is not None else []
    debt_series = _get_debt_ratio_series(df_annual)
    debt_list = debt_series.head(6).tolist() if debt_series is not None else []

    # 预判十年王者（规则 2 和规则 6 需要）
    is_king, _, _ = check_10_year_king_live(df_annual)

    # 规则1：最新亏损（ROE<0）
    if roe_list and roe_list[0] < 0:
        problems.append(f"最新ROE={roe_list[0]:.1f}%（亏损）")

    # 规则2：ROE 单年暴跌 ≥6pp，跌后 <15%，十年王者豁免
    if len(roe_list) >= 2:
        drop = roe_list[1] - roe_list[0]
        if drop >= 6 and roe_list[0] < 15 and not is_king:
            problems.append(
                f"ROE单年暴跌{drop:.1f}pp（{roe_list[1]:.1f}%→{roe_list[0]:.1f}%）"
            )

    # 规则3：ROE 连续 3 年下滑 且 最新 <15%
    if len(roe_list) >= 3:
        r = roe_list[:3]
        if r[0] < r[1] < r[2] and r[0] < 15:
            problems.append(f"ROE连续3年下滑至{r[0]:.1f}%（<15%底线）")

    # 规则4：毛利率连续 3 年下滑，累计跌幅 ≥5pp
    if len(gm_list) >= 3:
        g = gm_list[:3]
        if g[0] < g[1] < g[2] and (g[2] - g[0]) >= 5:
            problems.append(f"毛利率连续3年下滑（{g[2]:.1f}%→{g[0]:.1f}%）")

    # 规则5：营收连续 2 年负增长 + ROE<15%
    if len(rev_list) >= 2 and len(roe_list) >= 1:
        if rev_list[0] < 0 and rev_list[1] < 0 and roe_list[0] < 15:
            problems.append(
                f"营收连续2年负增长（{rev_list[1]:.1f}%, {rev_list[0]:.1f}%）"
                f"+ ROE仅{roe_list[0]:.1f}%"
            )

    # 规则6：负债率升 + ROE 同时恶化
    if len(debt_list) >= 3 and len(roe_list) >= 3:
        d = debt_list[:3]
        r = roe_list[:3]
        debt_rising = d[0] - d[2] > 10 and d[0] > 70
        roe_falling = r[0] < r[2] and r[0] < 15
        if debt_rising and roe_falling:
            problems.append(
                f"负债率3年升{d[0]-d[2]:.1f}pp至{d[0]:.1f}% + ROE跌至{r[0]:.1f}%"
            )

    # 规则7：盈利质量恶化（经营现金流/EPS <0.3 连续 2 年）
    # 豁免：银行/保险/证券，消费龙头（ROE≥15% + 毛利≥50%）
    skip_bank = any(k in (industry or "") for k in ["银行", "保险", "证券", "券商"])
    is_consumer_leader = (
        len(roe_list) >= 1 and len(gm_list) >= 1
        and roe_list[0] >= 15 and gm_list[0] >= 50
    )
    if not skip_bank and not is_consumer_leader:
        fcf_series = get_fcf_series(df_annual)
        eps_series = _get_eps_series(df_annual)
        if fcf_series is not None and eps_series is not None:
            cash_ratios = []
            fcf_list = fcf_series.head(4).tolist()
            eps_list = eps_series.head(4).tolist()
            for fcf_v, eps_v in zip(fcf_list, eps_list):
                if eps_v and eps_v > 0:
                    cash_ratios.append(fcf_v / eps_v)
            if len(cash_ratios) >= 2:
                latest, prev = cash_ratios[0], cash_ratios[1]
                if latest < 0.3 and prev < 0.3:
                    problems.append(
                        f"盈利质量恶化：近2年经营现金流仅为净利润的"
                        f"{prev:.0%}、{latest:.0%}"
                    )

    # 规则8：ROE 长期温水煮青蛙（5 年单调下降 + 跌幅 ≥10pp + 最新 <20%）
    if len(roe_list) >= 5:
        r = roe_list[:5]
        monotone_down = r[0] < r[1] < r[2] < r[3] < r[4]
        total_drop = r[4] - r[0]
        if monotone_down and total_drop >= 10 and r[0] < 20:
            problems.append(
                f"ROE近5年持续下降且跌破卓越线"
                f"（{r[4]:.0f}→{r[3]:.0f}→{r[2]:.0f}→{r[1]:.0f}→{r[0]:.0f}%，"
                f"累计降{total_drop:.0f}pp）"
            )

    return len(problems) == 0, problems


# ============================================================
# 消费龙头警示（已豁免但需重点关注）
# ============================================================

def check_consumer_leader_warning_live(df_annual):
    """
    消费龙头现金流警示（已豁免规则 7 但需重点关注）
    返回警示字符串，无警示时返回 None
    """
    if df_annual is None or df_annual.empty:
        return None

    roe_series = get_roe_series(df_annual)
    gm_series = _get_gross_margin_series(df_annual)
    rev_series = _get_revenue_growth_series(df_annual)
    fcf_series = get_fcf_series(df_annual)
    eps_series = _get_eps_series(df_annual)

    if (roe_series is None or gm_series is None
            or fcf_series is None or eps_series is None):
        return None
    if len(roe_series) < 1 or len(gm_series) < 1:
        return None

    latest_roe = float(roe_series.iloc[0])
    latest_gm = float(gm_series.iloc[0])

    # 只对"好公司"（ROE≥15% 且 毛利≥50%）生效
    if latest_roe < 15 or latest_gm < 50:
        return None

    # 计算近 2 年现金流比值
    cash_ratios = []
    for fcf_v, eps_v in zip(fcf_series.head(3).tolist(), eps_series.head(3).tolist()):
        if eps_v and eps_v > 0:
            cash_ratios.append(fcf_v / eps_v)
    if len(cash_ratios) < 2:
        return None

    latest, prev = cash_ratios[0], cash_ratios[1]
    if not (latest < 0.3 and prev < 0.3):
        return None

    # 多维线索
    lines = [
        f"现金流近2年仅{prev:.0%}、{latest:.0%}（连续异常）",
        f"ROE={latest_roe:.0f}% 毛利={latest_gm:.0f}%仍强劲 → 豁免护城河规则",
    ]
    if len(roe_series) >= 2:
        drop = float(roe_series.iloc[1]) - latest_roe
        if drop >= 5:
            lines.append(f"ROE单年降{drop:.0f}pp→警惕")
        else:
            lines.append("ROE稳定")
    if len(gm_series) >= 2:
        gm_drop = float(gm_series.iloc[1]) - latest_gm
        if gm_drop >= 5:
            lines.append(f"毛利降{gm_drop:.0f}pp→警惕定价权")
        else:
            lines.append("毛利稳定")
    if rev_series is not None and len(rev_series) >= 1:
        r0_rev = float(rev_series.iloc[0])
        if r0_rev < -5:
            lines.append(f"营收同步下滑{r0_rev:.0f}%→疑似行业周期")
        elif r0_rev >= 0:
            lines.append(f"营收仍增长{r0_rev:.0f}%→警惕造假")

    return " | ".join(lines)
