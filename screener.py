"""
筛选引擎 - 芒格/巴菲特价值投资体系
估值模型：行业分类PE区间 + PEG + 周期股特殊处理
"""

import time
import numpy as np
import pandas as pd
import yaml
from data_fetcher import (
    get_all_stocks,
    get_financial_indicator,
    extract_annual_data,
    get_roe_series,
    get_debt_info,
    get_opm_series,
    get_fcf_series,
    get_realtime_quotes,
    get_batch_roe_data,
    get_pe_ttm,
    find_column,
    get_stock_industry,
)


def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ============================================
# 行业PE估值区间（A股参考）
# ============================================
INDUSTRY_PE = {
    # =============================================
    # 简单生意（simple）：一看就懂、涨价换标签、低资本开支
    # 巴菲特最爱：可口可乐、喜诗糖果类
    # =============================================
    "白酒": {"low": 15, "fair_low": 20, "fair_high": 30, "high": 40, "type": "consumer", "complexity": "simple"},
    "食品饮料": {"low": 15, "fair_low": 20, "fair_high": 30, "high": 40, "type": "consumer", "complexity": "simple"},
    "调味品": {"low": 18, "fair_low": 22, "fair_high": 35, "high": 45, "type": "consumer", "complexity": "simple"},
    "调味发酵品": {"low": 18, "fair_low": 22, "fair_high": 35, "high": 45, "type": "consumer", "complexity": "simple"},
    "乳制品": {"low": 12, "fair_low": 15, "fair_high": 25, "high": 30, "type": "consumer", "complexity": "simple"},
    "饮料乳品": {"low": 12, "fair_low": 15, "fair_high": 25, "high": 30, "type": "consumer", "complexity": "simple"},
    "中药": {"low": 15, "fair_low": 20, "fair_high": 30, "high": 40, "type": "consumer", "complexity": "simple"},
    "家电": {"low": 10, "fair_low": 15, "fair_high": 25, "high": 30, "type": "consumer", "complexity": "simple"},
    "传媒": {"low": 15, "fair_low": 20, "fair_high": 30, "high": 40, "type": "consumer", "complexity": "simple"},
    "银行": {"low": 5, "fair_low": 6, "fair_high": 9, "high": 12, "type": "mature", "complexity": "simple", "high_leverage": True},
    "保险": {"low": 6, "fair_low": 8, "fair_high": 12, "high": 16, "type": "mature", "complexity": "simple", "high_leverage": True},
    "证券": {"low": 10, "fair_low": 14, "fair_high": 22, "high": 30, "type": "mature", "complexity": "medium", "high_leverage": True},
    "券商": {"low": 10, "fair_low": 14, "fair_high": 22, "high": 30, "type": "mature", "complexity": "medium", "high_leverage": True},
    "多元金融": {"low": 10, "fair_low": 14, "fair_high": 22, "high": 30, "type": "mature", "complexity": "medium", "high_leverage": True},
    "免税": {"low": 18, "fair_low": 25, "fair_high": 40, "high": 50, "type": "consumer", "complexity": "simple"},
    "旅游零售": {"low": 18, "fair_low": 25, "fair_high": 40, "high": 50, "type": "consumer", "complexity": "simple"},
    "医药": {"low": 15, "fair_low": 20, "fair_high": 30, "high": 40, "type": "growth", "complexity": "simple"},
    "生物制品": {"low": 15, "fair_low": 20, "fair_high": 30, "high": 40, "type": "growth", "complexity": "simple"},

    # =============================================
    # 中等复杂（medium）：能理解但有门槛、资本开支中等
    # =============================================
    "电力": {"low": 8, "fair_low": 10, "fair_high": 18, "high": 22, "type": "utility", "complexity": "medium"},
    "公用事业": {"low": 8, "fair_low": 10, "fair_high": 18, "high": 22, "type": "utility", "complexity": "medium"},
    "交通运输": {"low": 8, "fair_low": 12, "fair_high": 16, "high": 22, "type": "utility", "complexity": "medium"},
    "铁路": {"low": 8, "fair_low": 10, "fair_high": 16, "high": 20, "type": "utility", "complexity": "medium"},
    "铁路公路": {"low": 8, "fair_low": 10, "fair_high": 16, "high": 20, "type": "utility", "complexity": "medium"},
    "高速": {"low": 8, "fair_low": 10, "fair_high": 16, "high": 20, "type": "utility", "complexity": "medium"},
    "通信": {"low": 15, "fair_low": 20, "fair_high": 35, "high": 50, "type": "tech", "complexity": "medium"},
    "通信服务": {"low": 15, "fair_low": 20, "fair_high": 35, "high": 50, "type": "tech", "complexity": "medium"},
    "医疗器械": {"low": 18, "fair_low": 22, "fair_high": 35, "high": 50, "type": "growth", "complexity": "medium"},

    # =============================================
    # 复杂生意（complex）：重资产、技术变化快、需持续大额投入
    # 巴菲特不爱：需要不断烧钱、买设备、盖工厂
    # 买入信号自动降一级
    # =============================================
    "半导体": {"low": 30, "fair_low": 40, "fair_high": 65, "high": 80, "type": "tech", "complexity": "complex"},
    "芯片": {"low": 30, "fair_low": 40, "fair_high": 65, "high": 80, "type": "tech", "complexity": "complex"},
    "软件": {"low": 30, "fair_low": 40, "fair_high": 60, "high": 80, "type": "tech", "complexity": "medium"},
    "军工": {"low": 25, "fair_low": 35, "fair_high": 55, "high": 70, "type": "tech", "complexity": "complex"},
    "航空航天": {"low": 25, "fair_low": 35, "fair_high": 55, "high": 70, "type": "tech", "complexity": "complex"},
    # 锂电、新能源、电池：归为周期股（锂价大宗商品周期明显，用反向规则而非成长股规则）
    "新能源": {"low": 20, "fair_low": 30, "fair_high": 50, "high": 60, "type": "cycle", "complexity": "complex"},
    "锂电": {"low": 20, "fair_low": 30, "fair_high": 50, "high": 60, "type": "cycle", "complexity": "complex"},
    "电池": {"low": 20, "fair_low": 30, "fair_high": 50, "high": 60, "type": "cycle", "complexity": "complex"},
    "光伏": {"low": 15, "fair_low": 25, "fair_high": 45, "high": 55, "type": "tech", "complexity": "complex"},
    "轨道交通": {"low": 10, "fair_low": 13, "fair_high": 20, "high": 28, "type": "cycle", "complexity": "complex"},
    "轨交设备": {"low": 10, "fair_low": 13, "fair_high": 20, "high": 28, "type": "cycle", "complexity": "complex"},
    "铁路装备": {"low": 10, "fair_low": 13, "fair_high": 20, "high": 28, "type": "cycle", "complexity": "complex"},
    "铁路设备": {"low": 10, "fair_low": 13, "fair_high": 20, "high": 28, "type": "cycle", "complexity": "complex"},
    "机械制造": {"low": 10, "fair_low": 15, "fair_high": 25, "high": 35, "type": "cycle", "complexity": "complex"},
    "汽车玻璃": {"low": 10, "fair_low": 14, "fair_high": 22, "high": 30, "type": "cycle", "complexity": "complex"},
    "汽车零部件": {"low": 10, "fair_low": 14, "fair_high": 22, "high": 30, "type": "cycle", "complexity": "complex"},
    "建筑": {"low": 5, "fair_low": 7, "fair_high": 12, "high": 16, "type": "cycle", "complexity": "complex"},
    "钢铁": {"low": 5, "fair_low": 7, "fair_high": 12, "high": 16, "type": "cycle", "complexity": "complex"},
    "煤炭": {"low": 5, "fair_low": 7, "fair_high": 12, "high": 16, "type": "cycle", "complexity": "complex"},
    "煤炭开采": {"low": 5, "fair_low": 7, "fair_high": 12, "high": 16, "type": "cycle", "complexity": "complex"},
    "化工": {"low": 8, "fair_low": 12, "fair_high": 20, "high": 30, "type": "cycle", "complexity": "complex"},
    "化学制品": {"low": 8, "fair_low": 12, "fair_high": 20, "high": 30, "type": "cycle", "complexity": "complex"},
    "农化制品": {"low": 8, "fair_low": 12, "fair_high": 20, "high": 30, "type": "cycle", "complexity": "complex"},
    "有色金属": {"low": 8, "fair_low": 12, "fair_high": 20, "high": 30, "type": "cycle", "complexity": "complex"},
    "工业金属": {"low": 8, "fair_low": 12, "fair_high": 20, "high": 30, "type": "cycle", "complexity": "complex"},
    "稀土": {"low": 10, "fair_low": 15, "fair_high": 25, "high": 35, "type": "cycle", "complexity": "complex"},
    "小金属": {"low": 10, "fair_low": 15, "fair_high": 25, "high": 35, "type": "cycle", "complexity": "complex"},
    "矿业": {"low": 8, "fair_low": 10, "fair_high": 18, "high": 25, "type": "cycle", "complexity": "complex"},
    "石油": {"low": 6, "fair_low": 9, "fair_high": 15, "high": 22, "type": "cycle", "complexity": "complex"},
    "石油石化": {"low": 6, "fair_low": 9, "fair_high": 15, "high": 22, "type": "cycle", "complexity": "complex"},
    "油气": {"low": 6, "fair_low": 9, "fair_high": 15, "high": 22, "type": "cycle", "complexity": "complex"},
    # 高杠杆行业（地产）：豁免负债率>70%规则和杠杆惩罚
    # 注意：地产 type 用 mature 而非 cycle —— 地产的 ROE 下滑通常反映真实的行业恶化
    # （不像稀土/煤炭那样是纯周期波动），所以应走普通护城河规则
    "房地产": {"low": 5, "fair_low": 7, "fair_high": 12, "high": 18, "type": "mature", "complexity": "medium", "high_leverage": True},
    "地产": {"low": 5, "fair_low": 7, "fair_high": 12, "high": 18, "type": "mature", "complexity": "medium", "high_leverage": True},
    "房地产开发": {"low": 5, "fair_low": 7, "fair_high": 12, "high": 18, "type": "mature", "complexity": "medium", "high_leverage": True},
    # 科技/电子
    "面板": {"low": 12, "fair_low": 18, "fair_high": 30, "high": 45, "type": "tech", "complexity": "complex"},
    "显示": {"low": 12, "fair_low": 18, "fair_high": 30, "high": 45, "type": "tech", "complexity": "complex"},
    "光学光电子": {"low": 15, "fair_low": 20, "fair_high": 35, "high": 50, "type": "tech", "complexity": "complex"},
    "电子": {"low": 15, "fair_low": 22, "fair_high": 35, "high": 50, "type": "tech", "complexity": "medium"},
    "消费电子": {"low": 15, "fair_low": 22, "fair_high": 35, "high": 50, "type": "tech", "complexity": "medium"},
    "电子制造": {"low": 15, "fair_low": 22, "fair_high": 35, "high": 50, "type": "tech", "complexity": "medium"},
    # 互联网
    "互联网": {"low": 15, "fair_low": 25, "fair_high": 40, "high": 60, "type": "tech", "complexity": "medium"},
    "互联网服务": {"low": 15, "fair_low": 25, "fair_high": 40, "high": 60, "type": "tech", "complexity": "medium"},
    # 农业
    "农业": {"low": 10, "fair_low": 15, "fair_high": 25, "high": 35, "type": "cycle", "complexity": "medium"},
    "养殖": {"low": 10, "fair_low": 15, "fair_high": 25, "high": 35, "type": "cycle", "complexity": "medium"},
    "农牧": {"low": 10, "fair_low": 15, "fair_high": 25, "high": 35, "type": "cycle", "complexity": "medium"},
}

# 默认PE区间（找不到行业时用）
DEFAULT_PE = {"low": 10, "fair_low": 15, "fair_high": 25, "high": 35, "type": "default", "complexity": "medium"}

# 复杂度对ROE门槛（基于巴菲特1987年股东信）
# 巴菲特原话：10年均值≥20%，且单年不低于15%
# 简单生意：按巴菲特标准（20%重仓/15%轻仓/12%关注）
# 中等复杂：略严（需更多利润缓冲技术门槛）
# 复杂生意：最严（重资产持续烧钱，必须赚足够多）
COMPLEXITY_ROE_ADJUST = {
    "simple": {"heavy": 20, "light": 15, "watch": 12},   # 巴菲特标准
    "medium": {"heavy": 22, "light": 17, "watch": 14},   # 略严
    "complex": {"heavy": 25, "light": 20, "watch": 15},  # 最严
}


def match_industry_pe(industry_str):
    """根据股票所属行业匹配PE区间"""
    if not industry_str:
        return DEFAULT_PE
    for key, val in INDUSTRY_PE.items():
        if key in industry_str:
            return val
    return DEFAULT_PE


def get_pe_signal(current_pe, industry="", net_profit_growth=None):
    """
    行业感知的PE估值信号
    1. 按行业PE区间判断
    2. 结合PEG（如果有增速数据）
    3. 周期股特殊处理
    """
    if current_pe is None or np.isnan(current_pe):
        return None, "PE数据缺失"

    if current_pe <= 0:
        return None, "PE为负（亏损），不适用PE估值"

    pe_range = match_industry_pe(industry)
    industry_type = pe_range["type"]

    # 周期股特殊处理：PE极低可能是周期顶部
    if industry_type == "cycle":
        if current_pe < pe_range["low"]:
            # 周期股PE极低反而可能是卖点（盈利暴增的顶部）
            return "sell_watch", f"PE={current_pe:.1f}（周期股PE极低，可能是周期顶部，注意风险）"
        elif current_pe > pe_range["high"] * 2:
            # 周期股PE极高反而可能是买点（盈利低谷）
            return "buy_watch", f"PE={current_pe:.1f}（周期股PE极高，可能是周期底部，关注拐点）"

    # PEG判断（如有增速数据）
    peg_hint = ""
    if net_profit_growth and net_profit_growth > 0 and industry_type in ("growth", "tech", "consumer"):
        peg = current_pe / net_profit_growth
        if peg <= 0.8:
            peg_hint = f" PEG={peg:.1f}极低"
        elif peg <= 1.0:
            peg_hint = f" PEG={peg:.1f}合理"
        elif peg <= 1.5:
            peg_hint = f" PEG={peg:.1f}偏高"
        else:
            peg_hint = f" PEG={peg:.1f}高估"

    # 基于行业PE区间判断
    if current_pe <= pe_range["low"]:
        return "buy_heavy", f"PE={current_pe:.1f}，远低于行业底部{pe_range['low']}{peg_hint}→可以重仓买入"
    elif current_pe <= (pe_range["low"] + pe_range["fair_low"]) / 2:
        return "buy_medium", f"PE={current_pe:.1f}，明显低于合理区间{peg_hint}→可以中仓买入"
    elif current_pe <= pe_range["fair_low"]:
        return "buy_light", f"PE={current_pe:.1f}，低于行业合理区间{pe_range['fair_low']}-{pe_range['fair_high']}{peg_hint}→可以轻仓买入"
    elif current_pe <= pe_range["fair_high"]:
        mid = (pe_range["fair_low"] + pe_range["fair_high"]) / 2
        if current_pe <= mid * 0.9:
            return "buy_watch", f"PE={current_pe:.1f}，合理偏低{peg_hint}→重点关注买入"
        elif current_pe >= mid * 1.1:
            return "sell_watch", f"PE={current_pe:.1f}，合理偏高{peg_hint}→重点关注卖出"
        else:
            return "hold", f"PE={current_pe:.1f}，处于合理区间{pe_range['fair_low']}-{pe_range['fair_high']}{peg_hint}"
    elif current_pe <= (pe_range["fair_high"] + pe_range["high"]) / 2:
        return "sell_light", f"PE={current_pe:.1f}，高于合理区间{peg_hint}→可以适当卖出"
    elif current_pe <= pe_range["high"]:
        return "sell_medium", f"PE={current_pe:.1f}，明显高于合理区间{peg_hint}→可以中仓卖出"
    else:
        return "sell_heavy", f"PE={current_pe:.1f}，远高于行业上限{pe_range['high']}{peg_hint}→可以大量卖出"


# ============================================
# 财务指标检查（同之前）
# ============================================

def check_roe_no_leverage(df_annual, config):
    roe_series = get_roe_series(df_annual)
    if roe_series is None or len(roe_series) < 5:
        return False, "ROE数据不足"
    avg_roe = roe_series.mean()
    if avg_roe < config["screener"]["roe_min"]:
        return False, f"ROE均值{avg_roe:.1f}%"
    debt_info = get_debt_info(df_annual)
    if debt_info and debt_info.get("debt_ratio"):
        debt_ratio = debt_info["debt_ratio"]
        if not np.isnan(debt_ratio) and debt_ratio > config["screener"]["debt_ratio_max"]:
            return False, f"ROE{avg_roe:.1f}%但负债率{debt_ratio:.1f}%（高杠杆）"
    return True, f"ROE均值{avg_roe:.1f}%"


def check_debt_health(df_annual, config):
    """
    [旧版] 一刀切负债率检查（保留兼容）。
    新版优先使用 check_debt_health_tiered（按行业分档）。
    """
    debt_info = get_debt_info(df_annual)
    if debt_info is None:
        return False, "负债数据不足"
    debt_ratio = debt_info.get("debt_ratio")
    current_ratio = debt_info.get("current_ratio")
    if debt_ratio is None or np.isnan(debt_ratio) or debt_ratio > config["screener"]["debt_ratio_max"]:
        return False, f"负债率{'%.1f' % debt_ratio if debt_ratio else '?'}%"
    detail = f"负债率{debt_ratio:.1f}%"
    if current_ratio and not np.isnan(current_ratio):
        if current_ratio < config["screener"]["current_ratio_min"]:
            return False, f"流动比率{current_ratio:.2f}偏低"
        detail += f" 流动比率{current_ratio:.2f}"
    return True, detail


# ============================================================
# REQ-174：公司杠杆 4 档行业分档（2026-04-16）
# ============================================================
# 来源：芒格"三 L 理论"（Liquor/Ladies/Leverage）+ A 股实证数据
# 实证依据：
#   白酒/食品龙头负债率 20-30%（茅台、海天）
#   家电龙头 60-62%（美的、格力）
#   房地产央企 70.5%、民企 72.2%
#   建筑央企中位数 75%（中国铁建 79%）
#   金融业 90%+（豁免）
#
# 4 档分档：
#   消费档（白酒/食品/家电/中药/调味品）：健康≤45%, 警告 45-60%, 否决>70%
#   制造档（半导体/电子/机械/医药/医疗器械/汽车）：健康≤65%, 警告 65-75%, 否决>85%
#   基建档（房地产/建筑/交运/港口/航运）：健康≤80%, 否决>85%（行业天然高杠杆）
#   金融档（银行/保险/证券/券商）：完全豁免，不用此规则
# ============================================================

# 4 档阈值表
DEBT_TIER_THRESHOLDS = {
    "consumer": {"healthy": 45, "warning": 60, "reject": 70, "label": "消费档"},
    "manufacturing": {"healthy": 65, "warning": 75, "reject": 85, "label": "制造档"},
    "infrastructure": {"healthy": 80, "warning": 82, "reject": 85, "label": "基建档"},
    "finance": {"healthy": None, "warning": None, "reject": None, "label": "金融档（豁免）"},
}

# 行业 → 档位映射（基于 INDUSTRY_PE 的 type 字段 + 实证调研）
def _get_debt_tier(industry):
    """根据行业返回适用档位"""
    if not industry:
        return "manufacturing"  # 默认中档
    # 金融档（完全豁免）
    for kw in ["银行", "保险", "证券", "券商", "多元金融"]:
        if kw in industry:
            return "finance"
    # 基建档（天然高杠杆）
    for kw in ["房地产", "地产", "房屋建设", "基础建设", "建筑工程",
               "航空", "航运", "港口", "铁路公路", "高速公路",
               "公用事业", "电力", "燃气"]:
        if kw in industry:
            return "infrastructure"
    # 消费档（轻资产高利润）
    for kw in ["白酒", "食品", "饮料", "调味品", "调味发酵", "乳制品",
               "家电", "白色家电", "中药", "免税", "旅游",
               "传媒", "化妆品", "个护"]:
        if kw in industry:
            return "consumer"
    # 其余归制造档
    return "manufacturing"


# ============================================================
# REQ-191：高 ROE 杠杆化警告（2026-04-16）
# ============================================================
# 来源：杜邦分解 + 巴菲特"ROE 质量"理念
# 逻辑：ROE≥20% 但资产负债率过高（按行业档位）→ 警告 ROE 不可持续
# 注意：这是警告不是否决（REQ-174 已处理过高杠杆的硬否决）
# 目的：提醒用户"ROE 是杠杆吹起来的"，不是真正的盈利能力

def check_roe_leverage_quality(df_annual, industry):
    """
    REQ-191：检查 ROE 是否被杠杆"吹起来"
    返回：(has_warning, detail)
    """
    roe_series = get_roe_series(df_annual)
    if roe_series is None or len(roe_series) < 5:
        return False, ""
    avg_roe = float(roe_series.head(5).mean())

    # 只对 ROE≥20% 的公司做此检查（低 ROE 公司不存在"被吹起来"问题）
    if avg_roe < 20:
        return False, ""

    # 金融/基建档豁免（天然高杠杆，ROE 本来就依赖杠杆）
    tier = _get_debt_tier(industry)
    if tier in ("finance", "infrastructure"):
        return False, ""

    debt_info = get_debt_info(df_annual)
    if not debt_info or not debt_info.get("debt_ratio"):
        return False, ""
    debt_ratio = debt_info["debt_ratio"]
    if np.isnan(debt_ratio):
        return False, ""

    # 按档位判断"高 ROE + 高负债"
    # 消费档：ROE≥20% + 负债>50% → 警告（茅台/海天正常是 20-30%）
    # 制造档：ROE≥20% + 负债>65% → 警告（制造业高 ROE 通常靠周期+杠杆）
    tier_leverage_limit = {
        "consumer": 50,
        "manufacturing": 65,
    }
    limit = tier_leverage_limit.get(tier, 60)

    if debt_ratio > limit:
        # 权益乘数近似 = 1 / (1 - debt_ratio/100)
        equity_multiplier = 1 / (1 - debt_ratio / 100) if debt_ratio < 100 else 99
        detail = (
            f"高 ROE 杠杆化警告：ROE {avg_roe:.1f}% + 负债率 {debt_ratio:.1f}%"
            f"（权益乘数约 {equity_multiplier:.1f}x）→ ROE 可能靠杠杆撑起"
        )
        return True, detail

    return False, ""


def check_debt_health_tiered(df_annual, config, industry):
    """
    REQ-174：按行业 4 档检查负债率

    返回：(passed, detail, warning_flag)
      passed=False 表示硬否决
      warning_flag=True 表示达到警告档（持续关注但不否决）
    """
    debt_info = get_debt_info(df_annual)
    if debt_info is None:
        return False, "负债数据不足", False

    debt_ratio = debt_info.get("debt_ratio")
    current_ratio = debt_info.get("current_ratio")

    if debt_ratio is None or np.isnan(debt_ratio):
        return False, "负债率数据缺失", False

    tier = _get_debt_tier(industry)
    thresholds = DEBT_TIER_THRESHOLDS[tier]
    label = thresholds["label"]

    # 金融档豁免
    if tier == "finance":
        detail = f"负债率{debt_ratio:.1f}%（{label}豁免）"
        if current_ratio and not np.isnan(current_ratio):
            detail += f" 流动比率{current_ratio:.2f}"
        return True, detail, False

    # 硬否决
    if debt_ratio > thresholds["reject"]:
        return False, f"负债率{debt_ratio:.1f}%超{label}硬否决线{thresholds['reject']}%", False

    warning_flag = False
    # 警告档（达到但未硬否决）
    if debt_ratio > thresholds["warning"]:
        warning_flag = True
        detail = f"⚠负债率{debt_ratio:.1f}%（{label}警告 >{thresholds['warning']}%）"
    elif debt_ratio > thresholds["healthy"]:
        # 偏高但未到警告
        detail = f"负债率{debt_ratio:.1f}%（{label}偏高，健康线 {thresholds['healthy']}%）"
    else:
        detail = f"负债率{debt_ratio:.1f}%（{label}健康）"

    # 流动比率补充（降级为警告而不否决——家电/建筑行业流动比率本来就低）
    # 原一刀切 1.5 会误伤美的/格力/中建，改为只在极低时警告
    if current_ratio and not np.isnan(current_ratio):
        detail += f" 流动比率{current_ratio:.2f}"
        # 极低（<0.8）视为短期偿付能力风险，作为警告不否决
        if current_ratio < 0.8:
            warning_flag = True
            detail += "⚠极低"

    return True, detail, warning_flag


def check_opm_stable(df_annual, config):
    opm_series = get_opm_series(df_annual)
    if opm_series is None or len(opm_series) < 5:
        return False, "利润率数据不足"
    values = opm_series.values[::-1]
    if len(values) >= 3:
        slope = np.polyfit(np.arange(len(values)), values, 1)[0]
        if slope < -0.5:
            return False, f"利润率下滑（年均降{abs(slope):.1f}个百分点）"
    return True, f"利润率均值{opm_series.mean():.1f}%稳定"


def check_fcf(df_annual, config):
    fcf_series = get_fcf_series(df_annual)
    if fcf_series is None or len(fcf_series) < 3:
        return False, "现金流数据不足"
    recent = fcf_series.head(config["screener"]["fcf_positive_years"])
    positive = (recent > 0).sum()
    if positive < len(recent) * 0.8:
        return False, f"近{len(recent)}年中{len(recent)-positive}年现金流为负"
    return True, f"现金流充足"


def check_gross_margin(df_annual, config):
    col = find_column(df_annual, ["销售毛利率", "毛利率"])
    if col is None:
        return False, "毛利率缺失"
    values = pd.to_numeric(df_annual[col], errors="coerce").dropna()
    if len(values) < 3:
        return False, "毛利率不足"
    avg = values.mean()
    if avg < config["screener"]["gross_margin_min"]:
        return False, f"毛利率{avg:.1f}%"
    return True, f"毛利率{avg:.1f}%"


# ============================================
# 主筛选
# ============================================

def screen_single_stock(code, config, quotes_df):
    result = {
        "code": code, "passed": False, "checks": {},
        "signal": None, "signal_text": "", "pe": None, "price": None,
        "is_10y_king": False, "is_good_quality": False,
        "is_toll_bridge": False,  # 中国国情版 v3 D 规则：过路费生意标签
        "china_v3_risks": [],     # 中国国情版 v3 风险提示列表
        "cashcow_label": None,    # REQ-180：印钞机标签（cashcow_elite / cashcow / None）
        "cashcow_tier": "",       # REQ-180：印钞机档位描述
        "cashcow_detail": "",     # REQ-180：印钞机详情
    }

    df_indicator = get_financial_indicator(code)
    if df_indicator is None:
        return result
    df_annual = extract_annual_data(df_indicator, years=12)  # 取 12 年保证 E 规则 10 年数据
    if df_annual.empty or len(df_annual) < 3:
        return result

    # 行业只查一次，后续复用
    industry = get_stock_industry(code)

    # ---- 第零关：中国国情版 v3 硬否决（REQ-160 造假 + REQ-160E 下水道）----
    # A/B 回测验证：与原模型比，均值 -0.79pp（近乎持平），但逻辑正确性大幅提升
    # 真实世界避雷价值远高于回测（回测池是幸存者偏差）
    try:
        from china_adjustments import (
            check_drain_business,
            check_toll_bridge_business,
            check_cash_loan_double_high,
        )

        # A1. 经营现金流连续 3 年为负（造假/恶化信号）
        ocfs = []
        for _, row in df_annual.head(3).iterrows():
            ocf = row.get("每股经营现金流")
            if ocf is not None and not pd.isna(ocf):
                try:
                    ocfs.append(float(ocf))
                except Exception:
                    pass
        if len(ocfs) >= 3 and all(o < 0 for o in ocfs):
            detail = f"经营现金流连续3年为负（{ocfs[2]:.2f}/{ocfs[1]:.2f}/{ocfs[0]:.2f}元）→ 造假或恶化"
            result["checks"]["v3_fraud"] = {"passed": False, "detail": detail}
            result["china_v3_risks"].append(detail)
            return result

        # A2. 存贷双高（康美药业经典模式，仅对非银/保/证行业有效）
        if not any(k in (industry or "") for k in ["银行", "保险", "证券", "券商"]):
            is_double_high, dh_detail = check_cash_loan_double_high(code)
            if is_double_high:
                detail = (
                    f"存贷双高（货币{dh_detail.get('cash_ratio', 0):.0f}% + "
                    f"借款{dh_detail.get('loan_ratio', 0):.0f}%）→ 康美式造假疑似"
                )
                result["checks"]["v3_fraud"] = {"passed": False, "detail": detail}
                result["china_v3_risks"].append(detail)
                return result

        # E. 下水道生意（10 年 ROE 实证弱）
        is_drain, drain_reasons = check_drain_business(df_annual, industry)
        if is_drain:
            detail = drain_reasons[0] if drain_reasons else "下水道生意"
            result["checks"]["v3_drain"] = {"passed": False, "detail": detail}
            result["china_v3_risks"].append(detail)
            return result

        # D. 过路费生意识别（后续在 ROE 检查里给放宽门槛）
        roe_series = get_roe_series(df_annual)
        roe_avg_5y = None
        if roe_series is not None and len(roe_series) >= 5:
            roe_avg_5y = float(roe_series.head(5).mean())
        div_yield_hint = None  # 股息率在后面拉 quotes_df 时才有
        is_toll, toll_class, toll_reasons = check_toll_bridge_business(
            industry, code, roe_avg_5y or 0, None, div_yield_hint
        )
        if is_toll:
            result["is_toll_bridge"] = True
            result["toll_class"] = toll_class
            result["checks"]["v3_toll_bridge"] = {"passed": True, "detail": toll_reasons[0] if toll_reasons else ""}

        # REQ-180：印钞机标签识别（差异化亮点，不影响通过/否决）
        # 仅对 ROE 5 年均值 ≥20% 的高质量股做此识别（节省 API）
        # 3 年滚动 CapEx/净利 <10% 卓越 / <20% 印钞机 / 重资产用 CapEx/折摊
        if roe_avg_5y and roe_avg_5y >= 20:
            from china_adjustments import check_cashcow_label
            cc_label, cc_tier, cc_detail = check_cashcow_label(code, industry, roe_avg_5y)
            if cc_label:
                result["cashcow_label"] = cc_label
                result["cashcow_tier"] = cc_tier
                result["cashcow_detail"] = cc_detail
                result["checks"]["v3_cashcow"] = {"passed": True, "detail": f"{cc_tier} {cc_detail}"}
    except Exception as e:
        print(f"  {code} 中国国情版 v3 检查异常: {e}")

    # ---- 第一关：基础财务检查 ----
    # 过路费生意（公用事业特许经营）：临时放宽 ROE 门槛到 8%（REQ-164）
    # 巴菲特思路：稳定收益+股息 > 高 ROE 波动
    effective_config = config
    if result.get("is_toll_bridge"):
        import copy
        effective_config = copy.deepcopy(config)
        effective_config["screener"]["roe_min"] = 8

    # REQ-174：负债率改用 4 档行业分档（替代原 check_debt_health）
    def _check_debt_tiered():
        passed, detail, warning = check_debt_health_tiered(df_annual, effective_config, industry)
        if warning:
            result["china_v3_risks"].append(f"杠杆预警：{detail}")
        return passed, detail

    for check_name, check_func in [
        ("roe", lambda: check_roe_no_leverage(df_annual, effective_config)),
        ("debt", _check_debt_tiered),
        ("opm", lambda: check_opm_stable(df_annual, effective_config)),
        ("fcf", lambda: check_fcf(df_annual, effective_config)),
        ("gross_margin", lambda: check_gross_margin(df_annual, effective_config)),
    ]:
        passed, detail = check_func()
        result["checks"][check_name] = {"passed": passed, "detail": detail}
        if not passed:
            return result

    # REQ-191：高 ROE 杠杆化警告（和 174 协同但独立信号）
    # 通过负债率+流动比率检查后，额外做"ROE 是否被杠杆吹起来"的质量提示
    has_roe_lev_warning, roe_lev_detail = check_roe_leverage_quality(df_annual, industry)
    if has_roe_lev_warning:
        result["china_v3_risks"].append(roe_lev_detail)
        result["checks"]["v3_roe_leverage"] = {"passed": True, "detail": roe_lev_detail}

    # ---- 第二关：完整 8 条护城河检查（从 live_rules 同步 backtest_engine 规则）----
    try:
        from live_rules import check_moat_live, check_10_year_king_live, is_good_quality_live
        moat_intact, moat_problems = check_moat_live(df_annual, industry=industry)
        if not moat_intact:
            result["checks"]["moat"] = {"passed": False, "detail": "; ".join(moat_problems[:2])}
            return result
        # 十年王者 + 好公司标签（供后续"合理价格买好公司"使用）
        is_king, king_avg, _ = check_10_year_king_live(df_annual)
        result["is_10y_king"] = is_king
        result["king_avg_roe"] = king_avg
        result["is_good_quality"] = is_good_quality_live(df_annual)
    except Exception as e:
        print(f"  {code} 新规则检查异常: {e}")

    # ---- 第三关：价格 + PE 信号 ----
    if quotes_df is not None and not quotes_df.empty:
        row = quotes_df[quotes_df["代码"] == code]
        if not row.empty:
            row = row.iloc[0]
            price = pd.to_numeric(row.get("最新价"), errors="coerce")
            result["price"] = price

            max_price = config["screener"]["max_price_per_share"]
            if not pd.isna(price) and price > max_price:
                return result

            # 财务全部通过后，才查PE(TTM)（节省API调用）
            pe = None
            ttm_data = get_pe_ttm(code)
            if ttm_data and ttm_data.get("pe_ttm"):
                pe = ttm_data["pe_ttm"]
            else:
                pe = pd.to_numeric(row.get("市盈率-动态"), errors="coerce")
            result["pe"] = pe

            # 行业已在上方查过一次，直接复用
            signal, signal_text = get_pe_signal(pe, industry)

            # 合理价格买好公司（巴菲特 1989）：好公司在合理区间内也可以买入
            if result["is_good_quality"] and signal in ("hold", "buy_watch", "sell_watch"):
                pe_range = match_industry_pe(industry)
                if pe and pe > 0 and pe_range:
                    mid = (pe_range["fair_low"] + pe_range["fair_high"]) / 2
                    tag = "十年王者" if result["is_10y_king"] else "好公司"
                    if pe_range["fair_low"] < pe <= mid:
                        signal = "buy_light"
                        signal_text = f"{tag}合理价（PE={pe:.1f}）→ 轻仓买入"
                    elif mid < pe <= pe_range["fair_high"]:
                        signal = "buy_watch"
                        signal_text = f"{tag}（PE={pe:.1f} 合理偏高）→ 关注买入"

            result["signal"] = signal
            result["signal_text"] = signal_text

    # 提取核心财务指标原始值（前端展示用）
    if not df_annual.empty:
        _latest = df_annual.iloc[0]
        _r = _latest.get("roe")
        if _r is not None and not pd.isna(_r):
            result["roe"] = round(float(_r), 1)
        _g = _latest.get("gross_margin")
        if _g is not None and not pd.isna(_g):
            result["gross_margin"] = round(float(_g), 1)
        _d = _latest.get("debt_ratio")
        if _d is not None and not pd.isna(_d):
            result["debt_ratio"] = round(float(_d), 1)

    result["passed"] = True
    return result


def screen_all_stocks(config):
    print("正在获取A股列表...")
    stocks = get_all_stocks()
    if stocks.empty:
        return []
    print(f"共 {len(stocks)} 只股票")

    # 批量ROE预筛
    candidate_codes = set()
    for date in ["20241231", "20231231"]:
        df = get_batch_roe_data(date=date)
        if df is not None and not df.empty:
            roe_col = None
            for col in df.columns:
                if "净资产收益率" in col:
                    roe_col = col
                    break
            if roe_col:
                df[roe_col] = pd.to_numeric(df[roe_col], errors="coerce")
                filtered = df[df[roe_col] >= 15]
                code_col = None
                for col in df.columns:
                    if "代码" in col or "股票代码" in col:
                        code_col = col
                        break
                if code_col:
                    candidate_codes = set(filtered[code_col].astype(str).tolist())
                    print(f"  ROE≥15%: {len(candidate_codes)} 只")
            break

    if not candidate_codes:
        candidate_codes = set(stocks["code"].tolist())
    candidate_codes &= set(stocks["code"].tolist())

    quotes_df = get_realtime_quotes()

    max_price = config["screener"]["max_price_per_share"]
    if quotes_df is not None and not quotes_df.empty:
        quotes_df["价格_num"] = pd.to_numeric(quotes_df["最新价"], errors="coerce")
        affordable = quotes_df[(quotes_df["价格_num"] > 0) & (quotes_df["价格_num"] <= max_price)]
        candidate_codes &= set(affordable["代码"].tolist())
        print(f"  股价≤{max_price}元: {len(candidate_codes)} 只")

    passed = []
    total = len(candidate_codes)
    for i, code in enumerate(candidate_codes, 1):
        if i % 10 == 0:
            print(f"深度分析: {i}/{total}")
        result = screen_single_stock(code, config, quotes_df)
        if result["passed"]:
            name_row = stocks[stocks["code"] == code]
            result["name"] = name_row.iloc[0]["name"] if not name_row.empty else code
            passed.append(result)
        time.sleep(0.05)  # 最小防限流间隔（原0.3秒，节省约80秒/374只）

    signal_order = {"buy_heavy": 0, "buy_medium": 1, "buy_light": 2, "buy_watch": 3, "hold_keep": 4, "hold": 5, "sell_watch": 6, "sell_light": 7, "sell_medium": 8, "sell_heavy": 9, "true_decline": 10, None: 11}
    passed.sort(key=lambda x: signal_order.get(x.get("signal"), 7))
    print(f"\n候选池: {len(passed)} 只好公司")
    return passed


def check_holdings_sell_signals(holdings, config, market_temp_level=0):
    """
    检查持仓信号：
    1. 自动获取真实行业
    2. 判断真跌/假跌
    3. 真跌→基本面恶化警告
    4. 假跌或判定不清→按PE给关注/适当/中仓/大量卖出信号

    Args:
      market_temp_level: 大盘温度等级 (-2~2)，传给 evaluate_sell_meaningfulness
                        用于判断"整体市场到牛顶 → 主动减仓"
    """
    if not holdings:
        return []
    print("检查持仓信号...")
    quotes_df = get_realtime_quotes()
    signals = []

    for h in holdings:
        code = h["code"]
        name = h.get("name", code)
        if quotes_df is None or quotes_df.empty:
            continue

        row = quotes_df[quotes_df["代码"] == code]
        if row.empty:
            continue
        row = row.iloc[0]

        price = pd.to_numeric(row.get("最新价"), errors="coerce")

        # 1. 自动获取真实行业（多层 fallback）
        # quotes_df 的 stock_zh_a_spot_em 不含行业字段，必须另外走 get_stock_industry。
        # 最终兜底 holdings.json 里用户手填的 category，确保永远不为空。
        industry = get_stock_industry(code, fallback=h.get("category", ""))

        # 2. 获取PE(TTM)
        pe = None
        ttm_data = get_pe_ttm(code)
        if ttm_data and ttm_data.get("pe_ttm"):
            pe = ttm_data["pe_ttm"]
        else:
            pe = pd.to_numeric(row.get("市盈率-动态"), errors="coerce")

        # 3. PE信号
        signal, signal_text = get_pe_signal(pe, industry)

        # 3.5 用 live_rules 做完整的 8 条护城河检查 + 十年王者判定 + 好公司判定
        #     和 backtest_engine.py 的规则同步
        is_king = False
        is_good_quality = False
        king_avg_roe = None
        moat_intact_new = True
        try:
            from live_rules import check_moat_live, check_10_year_king_live, is_good_quality_live
            df_indicator = get_financial_indicator(code)
            if df_indicator is not None:
                df_annual_check = extract_annual_data(df_indicator, years=10)
                if not df_annual_check.empty:
                    moat_intact_new, moat_probs_new = check_moat_live(df_annual_check, industry=industry)
                    is_king, king_avg_roe, _ = check_10_year_king_live(df_annual_check)
                    is_good_quality = is_good_quality_live(df_annual_check)
        except Exception as e:
            moat_probs_new = []

        # 4. 如果是卖出信号，用新的护城河规则判断真跌/假跌
        if signal and "sell" in signal:
            # 优先用新的 check_moat_live（8 条规则）
            if not moat_intact_new:
                signal = "true_decline"
                signal_text = f"护城河松动({'; '.join(moat_probs_new[:2])})，建议卖出"
                print(f"  {name} 护城河松动→{moat_probs_new[0] if moat_probs_new else ''}")
            else:
                # 护城河完好：按档位分级卖出
                # 十年王者/高质量好公司 → 保留（只在最严重时减仓）
                if is_king:
                    # 十年王者：大量卖出也不清仓，只降级为"关注/减仓提示"
                    if signal == "sell_heavy":
                        signal_text = f"十年王者但PE远超行业上限 → 建议减仓30% | {signal_text}"
                    elif signal == "sell_medium":
                        signal_text = f"十年王者但PE明显偏高 → 建议减仓20% | {signal_text}"
                    print(f"  {name} 十年王者·豁免自动清仓")
                else:
                    # 非王者：正常 PE 卖出信号
                    # quotes_df 不含行业字段，这里用大盘整体涨跌幅做代理提示
                    if not quotes_df.empty and "涨跌幅" in quotes_df.columns:
                        market_change = pd.to_numeric(quotes_df["涨跌幅"], errors="coerce").dropna()
                        if len(market_change) > 100 and market_change.mean() < -1:
                            signal_text += "（大盘普跌，市场因素）"
                    print(f"  {name} PE卖出信号: {signal}")

        # 5. 护城河消失止损（芒格：宁可错过也不犯错）
        # 持有后亏损超30%且基本面也在恶化 → 护城河可能消失
        cost_price = h.get("cost", 0)
        if cost_price > 0 and not pd.isna(price) and price > 0:
            holding_pnl = (price / cost_price - 1) * 100
            if holding_pnl <= -30:
                # 亏损超30%，检查基本面（传入真实 PE 和 PB）
                real_pb = pd.to_numeric(row.get("市净率"), errors="coerce") if "市净率" in quotes_df.columns else None
                real_pb = real_pb if real_pb and not pd.isna(real_pb) else None
                is_healthy, problems = check_fundamental_health(code, pe=pe, pb=real_pb)
                if is_healthy is not None and not is_healthy:
                    signal = "true_decline"
                    signal_text = f"亏损{holding_pnl:.0f}%+基本面恶化({','.join(problems[:2])})→护城河可能消失，建议止损"

        # 6. 加仓信号：仅十年王者 / 好公司（严格标准）才允许 PE 偏低时加仓
        #    巴菲特原话："Don't add to losers, don't add to the average. Add only to winners."
        #    芒格原则：对平庸企业的正确动作是持有不加码，等更好的时机
        #    之前的 bug：只看 PE 低就给 buy_add，把云南白药这种 10 年 ROE
        #    均值 13%（未达 15% 门槛）的平庸企业也建议加仓，和关注表
        #    "ROE 限制最高关注"的逻辑互相矛盾。
        if signal and "buy" in signal:
            if is_king:
                signal = "buy_add"
                signal_text = f"十年王者 + PE偏低→可加仓 | {signal_text}"
            elif is_good_quality:
                # 非十年连续但 5 年 ROE 均值 ≥ 20% + 毛利 ≥ 30% 的好公司
                signal = "buy_add"
                signal_text = f"好公司 + PE偏低→可加仓 | {signal_text}"
            else:
                # 平庸企业（ROE 不达标）：降级为持有，不建议加仓
                # 注意：不保留原"可以轻仓买入"文案，避免自相矛盾
                # 原文案是基于PE的，没考虑公司质量，不该拼接
                signal = "hold_keep"
                signal_text = (
                    f"非十年王者/好公司（ROE未达15%连续10年标准）→ "
                    f"虽然PE偏低但不加仓，等ROE真正恢复到15%+再考虑"
                )

        # 7. 持仓股：hold变成"建议持续持有"
        if signal == "hold":
            signal = "hold_keep"
            signal_text += " →建议持续持有"

        # 8. 消费龙头现金流警示（已豁免但需重点关注）
        # 对"高ROE+高毛利但现金流异常"的消费龙头，单独标出"重点关注"
        cf_warning = check_consumer_leader_cash_flow_warning(code)
        if cf_warning:
            signal_text += f" | ⚠重点关注：{cf_warning}"

        # 浮盈评估：三维综合
        #   1. "卖出是否有意义"（防止平本/浮亏时被机械减仓）
        #   2. "是否必须割肉"（基本面恶化 true_decline）
        #   3. "大盘是否到牛顶"（market_temp_level=1/2 时主动减仓提醒）
        from etf_monitor import evaluate_sell_meaningfulness
        pnl_eval = evaluate_sell_meaningfulness(
            cost=cost_price,
            current_price=price if not pd.isna(price) else None,
            signal=signal,
            market_temp_level=market_temp_level,
        )
        # 致命信号/牛顶提醒的建议追加到 signal_text
        if pnl_eval.get("override_signal") and pnl_eval.get("advice"):
            signal_text = f"{signal_text} | {pnl_eval['advice']}"

        # 提取核心财务指标原始值（前端展示用）
        roe_val = None
        gm_val = None
        debt_val = None
        div_yield = 0
        try:
            if df_indicator is not None:
                df_annual_h = extract_annual_data(df_indicator, years=3)
                if not df_annual_h.empty:
                    _latest = df_annual_h.iloc[0]
                    _r = _latest.get("roe")
                    if _r is not None and not pd.isna(_r):
                        roe_val = round(float(_r), 1)
                    _g = _latest.get("gross_margin")
                    if _g is not None and not pd.isna(_g):
                        gm_val = round(float(_g), 1)
                    _d = _latest.get("debt_ratio")
                    if _d is not None and not pd.isna(_d):
                        debt_val = round(float(_d), 1)
            div_yield = get_dividend_yield(code, price if not pd.isna(price) else 0, industry=industry)
        except Exception:
            pass

        signals.append({
            "code": code, "name": name,
            "shares": h.get("shares", 0), "cost": h.get("cost", 0),
            "price": price if not pd.isna(price) else 0,
            "pe": pe if not pd.isna(pe) else 0,
            "signal": signal, "signal_text": signal_text,
            "industry": industry,
            "holding_pnl": holding_pnl if cost_price > 0 and not pd.isna(price) else 0,
            "pnl_pct": pnl_eval.get("pnl_pct"),
            "pnl_label": pnl_eval.get("label"),
            "pnl_advice": pnl_eval.get("advice"),
            "must_sell": pnl_eval.get("must_sell", False),
            "bull_top_alert": pnl_eval.get("bull_top_alert", False),
            "cf_warning": cf_warning,
            "roe": roe_val,
            "gross_margin": gm_val,
            "debt_ratio": debt_val,
            "dividend_yield": div_yield,
        })
        time.sleep(0.05)

    return signals


def check_consumer_leader_cash_flow_warning(code):
    """
    消费龙头现金流警示（多维度校验协助判断真假跌）
    仅对"高ROE + 高毛利 但 现金流异常"的消费龙头返回警示
    用途：持仓股的重点关注提示（已豁免护城河松动规则，但需要额外警惕）

    多维度线索：
      1. 当前ROE是否仍强劲（核心判断）
      2. 毛利率是否稳定（定价权线索）
      3. 营收是否同步下滑（行业周期线索 vs 单体造假线索）
      4. 应收账款周转率是否异常（造假典型特征）
      5. 存货周转率是否异常
    """
    df = get_financial_indicator(code)
    if df is None:
        return None
    df_annual = extract_annual_data(df, years=5)
    if df_annual.empty or len(df_annual) < 2:
        return None

    # 取最新 ROE 和毛利率
    roe_series = get_roe_series(df_annual)
    if roe_series is None or len(roe_series) < 1:
        return None
    latest_roe = roe_series.iloc[0]
    if latest_roe < 15:
        return None  # 不是高 ROE 公司（巴菲特合格线）

    gm_col = find_column(df_annual, ["销售毛利率", "毛利率"])
    if not gm_col:
        return None
    gm = pd.to_numeric(df_annual[gm_col], errors="coerce").dropna()
    if len(gm) < 1 or gm.iloc[0] < 50:
        return None  # 不是高毛利公司

    # 检查现金流 / 净利润比值（近2年）
    fcf = get_fcf_series(df_annual)
    profit_col = find_column(df_annual, ["净利润"])
    if fcf is None or not profit_col or len(fcf) < 2:
        return None
    # 简化：用现金流序列与净利润序列对比（前2年）
    profits = pd.to_numeric(df_annual[profit_col], errors="coerce").dropna()
    if len(profits) < 2:
        return None

    try:
        r0 = float(fcf.iloc[0]) / float(profits.iloc[0]) if profits.iloc[0] > 0 else 0
        r1 = float(fcf.iloc[1]) / float(profits.iloc[1]) if profits.iloc[1] > 0 else 0
    except (ValueError, ZeroDivisionError, TypeError):
        return None

    if not (r0 < 0.3 and r1 < 0.3):
        return None  # 现金流正常，无需警示

    # 已触发：组装多维度状态说明
    lines = [
        f"现金流近2年仅{r1:.0%}、{r0:.0%}（连续异常）",
        f"ROE={latest_roe:.0f}% 毛利={gm.iloc[0]:.0f}%仍强劲 → 豁免护城河规则",
    ]
    # 多维校验 1：ROE 趋势
    if len(roe_series) >= 2:
        drop = float(roe_series.iloc[1] - roe_series.iloc[0])
        if drop >= 5:
            lines.append(f"ROE单年降{drop:.0f}pp→警惕")
        else:
            lines.append(f"ROE稳定")
    # 多维校验 2：毛利率趋势
    if len(gm) >= 2:
        gm_drop = float(gm.iloc[1] - gm.iloc[0])
        if gm_drop >= 5:
            lines.append(f"毛利降{gm_drop:.0f}pp→警惕定价权")
        else:
            lines.append(f"毛利稳定")
    # 多维校验 3：应收账款周转率（造假线索）
    ar_col = find_column(df_annual, ["应收账款周转率"])
    if ar_col:
        ar = pd.to_numeric(df_annual[ar_col], errors="coerce").dropna()
        if len(ar) >= 2:
            if ar.iloc[0] < ar.iloc[1] * 0.7:
                lines.append(f"应收周转恶化→警惕造假")
            else:
                lines.append(f"应收周转正常")
    # 多维校验 4：存货周转率（造假线索）
    inv_col = find_column(df_annual, ["存货周转率"])
    if inv_col:
        inv = pd.to_numeric(df_annual[inv_col], errors="coerce").dropna()
        if len(inv) >= 2:
            if inv.iloc[0] < inv.iloc[1] * 0.7:
                lines.append(f"存货周转恶化→警惕")
    # 多维校验 5：营收是否同步下滑（行业周期线索）
    rev_col = find_column(df_annual, ["营业总收入增长率", "主营业务收入增长率"])
    if rev_col:
        rev = pd.to_numeric(df_annual[rev_col], errors="coerce").dropna()
        if len(rev) >= 1:
            r0_rev = float(rev.iloc[0])
            if r0_rev < -5:
                lines.append(f"营收同步下滑{r0_rev:.0f}%→疑似行业周期")
            elif r0_rev < 0:
                lines.append(f"营收轻微下滑")
            else:
                lines.append(f"营收仍增长{r0_rev:.0f}%→警惕造假")

    return " | ".join(lines)


# ============================================
# 关注表财务健康验证（买入前必须过关）
# ============================================

def check_watchlist_financial_health(code, industry=""):
    """
    对关注表股票做财务健康检查+ROE等级判定
    根据行业复杂度+杠杆率动态调整ROE门槛：
    - 简单生意+低杠杆：ROE 12%可重仓（巴菲特最爱）
    - 复杂生意+高杠杆：ROE 25%才可重仓（必须赚够多）
    - 十年王者豁免当下 ROE 门槛（巴菲特：熊市看历史不看当下）
    """
    df = get_financial_indicator(code)
    if df is None:
        return True, "财务数据不可用", "watch"  # 保守：最高关注

    # 用 10 年数据做十年王者判定（替代原来只看 5 年的局限）
    df_annual_10y = extract_annual_data(df, years=10)
    try:
        from live_rules import check_10_year_king_live
        is_king, king_avg, king_years = check_10_year_king_live(df_annual_10y)
    except Exception:
        is_king = False
        king_avg = None

    # 十年王者：直接放行为 heavy 级别（巴菲特：熊市买王者）
    if is_king:
        return True, f"十年王者(ROE均值{king_avg:.0f}% {king_years}/10年≥15%)", "heavy"

    df_annual = extract_annual_data(df, years=5)
    if df_annual.empty:
        return True, "无年报数据", "watch"  # 保守：最高关注

    warnings = []
    roe_level = "heavy"

    # 获取行业复杂度
    pe_range = match_industry_pe(industry)
    complexity = pe_range.get("complexity", "medium")

    # 基础ROE门槛（按行业复杂度）
    base_thresholds = COMPLEXITY_ROE_ADJUST.get(complexity, COMPLEXITY_ROE_ADJUST["medium"])

    # 再根据杠杆率微调
    roe_series = get_roe_series(df_annual)
    debt_info_for_roe = get_debt_info(df_annual)
    debt_ratio_val = 50
    if debt_info_for_roe and debt_info_for_roe.get("debt_ratio"):
        dr = debt_info_for_roe["debt_ratio"]
        if not np.isnan(dr):
            debt_ratio_val = dr

    # 杠杆调整：低杠杆降2%门槛，高杠杆加5%门槛
    leverage_adj = 0
    if debt_ratio_val < 30:
        leverage_adj = -2  # 低杠杆，放宽
    elif debt_ratio_val > 50:
        leverage_adj = 5   # 高杠杆，加严

    roe_thresholds = {
        "heavy": base_thresholds["heavy"] + leverage_adj,
        "light": base_thresholds["light"] + leverage_adj,
        "watch": base_thresholds["watch"] + leverage_adj,
    }

    complexity_label = {"simple": "简单生意", "medium": "中等复杂", "complex": "复杂生意"}.get(complexity, "")
    if complexity == "complex":
        warnings.append(f"复杂生意(重仓需ROE≥{roe_thresholds['heavy']}%)")

    if roe_series is not None and len(roe_series) >= 2:
        avg_roe = roe_series.mean()
        data_years = len(roe_series)

        # 数据不足8年，自动降一级
        downgrade = 1 if data_years < 8 else 0

        if avg_roe >= roe_thresholds["heavy"]:
            levels = ["heavy", "light", "watch", "watch"]
            roe_level = levels[min(downgrade, len(levels)-1)]
        elif avg_roe >= roe_thresholds["light"]:
            levels = ["light", "watch", "watch"]
            roe_level = levels[min(downgrade, len(levels)-1)]
        elif avg_roe >= roe_thresholds["watch"]:
            roe_level = "watch"
        else:
            roe_level = "none"
            warnings.append(f"ROE={avg_roe:.1f}%过低")

        if data_years < 8:
            warnings.append(f"仅{data_years}年数据")
        if roe_level != "heavy" and roe_level != "none":
            debt_note = f"负债率{debt_ratio_val:.0f}%" if debt_ratio_val < 30 else ""
            warnings.append(f"ROE={avg_roe:.1f}% {debt_note}")
    else:
        roe_level = "watch"
        warnings.append("ROE数据缺失")

    # 1. 负债率检查（>55%警告）
    debt_info = get_debt_info(df_annual)
    if debt_info and debt_info.get("debt_ratio"):
        dr = debt_info["debt_ratio"]
        if not np.isnan(dr) and dr > 55:
            warnings.append(f"负债率{dr:.0f}%偏高")

    # 2. 流动比率检查（<1.0警告）
    if debt_info and debt_info.get("current_ratio"):
        cr = debt_info["current_ratio"]
        if not np.isnan(cr) and cr < 1.0:
            warnings.append(f"流动比率{cr:.2f}偏低")

    # 3. 营业利润率是否下滑
    opm = get_opm_series(df_annual)
    if opm is not None and len(opm) >= 3:
        values = opm.values[::-1]
        slope = np.polyfit(np.arange(len(values)), values, 1)[0]
        if slope < -1.0:
            warnings.append("利润率持续下滑")

    # 4. 现金流检查
    fcf = get_fcf_series(df_annual)
    if fcf is not None and len(fcf) >= 2:
        if (fcf.head(2) <= 0).all():
            warnings.append("现金流连续为负")

    # 4. 现金流检查
    fcf = get_fcf_series(df_annual)
    if fcf is not None and len(fcf) >= 2:
        if (fcf.head(2) <= 0).all():
            warnings.append("现金流连续为负")

    # has_risk 只匹配"明确的风险词"，不能用"负债率"/"流动比率"这种中性词
    # 否则会误判 "ROE=10.2% 负债率26%" 这种纯展示 note（负债率 26% 其实很健康）
    RISK_KEYWORDS = ("偏高", "偏低", "连续为负", "持续下滑", "过高", "过低")
    has_risk = any(any(k in w for k in RISK_KEYWORDS) for w in warnings)
    return not has_risk, "、".join(warnings) if warnings else "", roe_level


# ============================================
# 真假下跌判断
# ============================================

def check_fundamental_health(code, pe=None, pb=None):
    """
    检查公司基本面是否健康（用于区分真假下跌）

    Args:
      pe: 当前 PE(TTM)，用于规则 8 的 ROE-PB 背离检查
      pb: 当前市净率（真实值，优先使用；None 时用 PE×ROE/100 近似）
    """
    df = get_financial_indicator(code)
    if df is None:
        return None, []

    df_annual = extract_annual_data(df, years=5)
    if df_annual.empty or len(df_annual) < 2:
        return None, []

    problems = []  # 基本面问题列表
    healthy = []   # 健康指标列表

    # 1. 营收利润是否连续下滑
    rev_col = find_column(df_annual, ["营业总收入增长率", "主营业务收入增长率"])
    if rev_col:
        rev_growth = pd.to_numeric(df_annual[rev_col], errors="coerce").dropna()
        if len(rev_growth) >= 2:
            recent2 = rev_growth.head(2).values
            if all(v < 0 for v in recent2):
                problems.append(f"营收连续{len([v for v in recent2 if v<0])}年下滑")
            else:
                healthy.append("营收正增长")

    # 2. 净利润是否连续下滑
    profit_col = find_column(df_annual, ["净利润增长率", "净利润同比增长率"])
    if profit_col:
        profit_growth = pd.to_numeric(df_annual[profit_col], errors="coerce").dropna()
        if len(profit_growth) >= 2:
            recent2 = profit_growth.head(2).values
            if all(v < 0 for v in recent2):
                problems.append(f"净利润连续下滑")
            else:
                healthy.append("利润正增长")

    # 3. 毛利率/净利率是否稳定
    gm_col = find_column(df_annual, ["销售毛利率", "毛利率"])
    if gm_col:
        gm = pd.to_numeric(df_annual[gm_col], errors="coerce").dropna()
        if len(gm) >= 3:
            gm_values = gm.values[::-1]
            slope = np.polyfit(np.arange(len(gm_values)), gm_values, 1)[0]
            if slope < -1.0:
                problems.append(f"毛利率持续下降")
            else:
                healthy.append(f"毛利率稳定{gm.iloc[0]:.1f}%")

    # 4. 现金流是否健康
    fcf = get_fcf_series(df_annual)
    if fcf is not None and len(fcf) >= 2:
        recent_fcf = fcf.head(2)
        if (recent_fcf <= 0).all():
            problems.append("现金流连续为负")
        else:
            healthy.append("现金流健康")

    # 5. 应收账款是否暴增
    ar_col = find_column(df_annual, ["应收账款周转率"])
    if ar_col:
        ar = pd.to_numeric(df_annual[ar_col], errors="coerce").dropna()
        if len(ar) >= 2:
            if ar.iloc[0] < ar.iloc[1] * 0.7:
                problems.append("应收账款周转率大幅下降")

    # 6. ROE连续下滑（巴菲特清仓信号）
    # 从20%+掉到<15%且持续2-3年 → 基本面恶化
    #
    # ⚠ 注意方向：df_annual 是 latest-first 排序，
    #   recent_roe[0]=最新年, recent_roe[1]=次新年, recent_roe[2]=最老年
    #   "连续 3 年下滑" 的正确条件是：最新 < 次新 < 最老
    #   即 recent_roe[0] < recent_roe[1] < recent_roe[2]
    #
    # 历史 bug：之前写的 `recent_roe[i] > recent_roe[i+1]`，方向反了，
    # 把"ROE 回升（10→12→13）"误判为"ROE 下滑"，触发错误的观望信号。
    # 对比 live_rules.check_moat_live 规则 3 (r[0]<r[1]<r[2]) 修正。
    roe_series = get_roe_series(df_annual)
    if roe_series is not None and len(roe_series) >= 3:
        recent_roe = roe_series.head(3).values  # [最新, 次新, 最老]
        latest_roe = recent_roe[0]
        oldest_roe = recent_roe[2]
        # 连续 3 年下滑：最新 < 次新 < 最老
        is_declining = recent_roe[0] < recent_roe[1] < recent_roe[2]
        if is_declining:
            if latest_roe < 15:
                problems.append(f"ROE连续下滑至{latest_roe:.1f}%（破15%底线）")
            elif oldest_roe - latest_roe > 5:
                problems.append(f"ROE连续下滑（从{oldest_roe:.1f}%降至{latest_roe:.1f}%）")

    # 7. 高ROE但现金流远低于净利润（虚假ROE）
    # 巴菲特：经营现金流应≥净利润
    if fcf is not None and len(fcf) >= 1:
        profit_col2 = find_column(df_annual, ["净利润增长率"])
        # 如果现金流远低于0但ROE还在20%以上，说明ROE是虚的
        if roe_series is not None and len(roe_series) >= 1:
            latest_roe = roe_series.iloc[0]
            latest_fcf = fcf.iloc[0]
            if latest_roe > 15 and latest_fcf < 0:
                problems.append(f"ROE={latest_roe:.1f}%但现金流为负（虚假ROE）")

    # 8. ROE-PB 背离检查（ROE 下降但 PB 不降 = 死猫反弹/虚假拉升）
    #
    # 原理：健康公司的 PB 应该由 ROE 支撑。
    #   ROE 下降 → 赚钱能力衰退 → PB 应该跟着降
    #   如果 ROE 下降但 PB 反升 → 市场在"拉估值"，不是在"跟业绩"
    #
    # 正式版：优先用真实 PB（stock_zh_a_spot_em 的"市净率"字段），
    #         没有时才用 PE × ROE / 100 近似
    if roe_series is not None and len(roe_series) >= 2:
        latest_roe_8 = float(roe_series.iloc[0])
        prev_roe_8 = float(roe_series.iloc[1])

        if prev_roe_8 > 0 and latest_roe_8 > 0:
            roe_drop = prev_roe_8 - latest_roe_8  # 正值 = ROE 下降

            # 当前 PB：优先用传入的真实值，否则近似
            if pb is not None and pb > 0:
                current_pb = pb
                pb_source = "真实"
            elif pe is not None and pe > 0:
                current_pb = pe * latest_roe_8 / 100
                pb_source = "近似"
            else:
                current_pb = None
                pb_source = ""

            if current_pb is not None and roe_drop >= 3 and current_pb > 1.5:
                # ROE 下降 ≥ 3pp 且 PB 仍高 → 警告
                problems.append(
                    f"ROE-PB背离（ROE从{prev_roe_8:.1f}%降至{latest_roe_8:.1f}%，"
                    f"但PB={current_pb:.2f}仍偏高（{pb_source}），"
                    f"估值拉升可能是假象）"
                )

    is_healthy = len(problems) == 0
    return is_healthy, problems if problems else healthy


def check_decline_signals(stock_list, quotes_df):
    """
    对关注表/持仓中近期下跌的股票进行真假下跌判断
    返回：假跌买入机会 + 真跌卖出警告
    """
    if quotes_df is None or quotes_df.empty:
        return [], []

    false_declines = []  # 假跌（买入机会）
    true_declines = []   # 真跌（卖出警告）

    for stock in stock_list:
        code = stock["code"]
        name = stock.get("name", code)
        category = stock.get("category", "")

        row = quotes_df[quotes_df["代码"] == code]
        if row.empty:
            continue
        row = row.iloc[0]

        # 检查涨跌幅（当日+近期）
        change_pct = pd.to_numeric(row.get("涨跌幅"), errors="coerce")
        price = pd.to_numeric(row.get("最新价"), errors="coerce")

        # 只关注下跌超过3%的股票
        if pd.isna(change_pct) or change_pct > -3:
            continue

        print(f"  {name}({code}) 下跌{change_pct:.1f}%，分析真假...")

        # 检查基本面（传入 PE 和 PB）
        _pb_decline = None
        if "市净率" in quotes_df.columns:
            _pb_val = pd.to_numeric(row.get("市净率"), errors="coerce")
            if not pd.isna(_pb_val):
                _pb_decline = float(_pb_val)
        pe_decline = pd.to_numeric(row.get("市盈率-动态"), errors="coerce")
        pe_decline = float(pe_decline) if not pd.isna(pe_decline) else None
        is_healthy, details = check_fundamental_health(code, pe=pe_decline, pb=_pb_decline)
        if is_healthy is None:
            continue

        # 真实行业（quotes_df 不含行业字段，必须走 get_stock_industry）
        industry = get_stock_industry(code, fallback=category)

        # 同行业涨跌对比：quotes_df 不携带行业列，这里用大盘整体涨跌幅做代理
        # （真要按行业过滤需要另外维护 code→industry 的全市场映射，成本过高，暂缓）
        industry_also_down = False
        if not quotes_df.empty and "涨跌幅" in quotes_df.columns:
            market_change = pd.to_numeric(quotes_df["涨跌幅"], errors="coerce").dropna()
            if len(market_change) > 100:
                industry_also_down = market_change.mean() < -1

        stock_info = {
            "code": code,
            "name": name,
            "category": category,
            "price": price if not pd.isna(price) else 0,
            "change_pct": change_pct,
            "details": details,
            "industry": industry,
            "industry_also_down": industry_also_down,
        }

        if is_healthy:
            # 基本面健康 + 下跌 = 假跌（买入机会）
            reason = "基本面健康"
            if industry_also_down:
                reason += "，同行业普跌（市场原因）"
            reason += "：" + "、".join(details[:3])
            stock_info["signal"] = "false_decline"
            stock_info["signal_text"] = f"假跌{change_pct:.1f}% {reason}→逢低关注"
            false_declines.append(stock_info)
            print(f"    -> 假跌（买入机会）")
        else:
            # 基本面恶化 + 下跌 = 真跌（卖出警告）
            reason = "基本面恶化"
            if not industry_also_down and industry:
                reason += "，同行未跌（公司自身问题）"
            reason += "：" + "、".join(details[:3])
            stock_info["signal"] = "true_decline"
            stock_info["signal_text"] = f"真跌{change_pct:.1f}% {reason}→建议卖出"
            true_declines.append(stock_info)
            print(f"    -> 真跌（卖出警告）")

        time.sleep(0.5)

    return false_declines, true_declines


# ============================================
# 仓位控制（巴菲特：单只不超40%，我们用30%更保守）
# ============================================

def check_position_sizes(holdings, signals_map=None, total_capital=None):
    """
    REQ-189：单股集中度上限分档检查（2026-04-16 重构）

    依据：
      - 巴菲特合伙基金 1964 年美国运通持仓 40%
      - 芒格 Daily Journal 三只股票几乎全仓
      - "一刀切 40% 危险"过于保守，违背集中投资精神

    分档阈值（警告线 / 危险线）：
      1. 十年王者 + 小资金（<100 万）：35% / 45%  ← 最宽松，允许集中
      2. 十年王者 + 大资金（≥100 万）：25% / 35%  ← 中等，防止流动性风险
      3. 普通标的（非王者）：                20% / 30%  ← 最严，质量未证明
      4. 下限（ >15% 仍正常）：不提示     ← 太分散违背集中投资精神

    参数：
      holdings: [{code, name, shares, cost}, ...]
      signals_map: {code: {is_10y_king, ...}} 用于拿质量标签，可选
      total_capital: 总资产（现金+市值），用于判断小资金/大资金，可选

    返回：需要提醒的持仓列表
    """
    if not holdings or len(holdings) < 2:
        return []

    warnings = []
    total_cost = sum(h.get("shares", 0) * h.get("cost", 0) for h in holdings)
    if total_cost <= 0:
        return []

    # 判断资金规模：总市值 <100 万算小资金
    # 如果没有 total_capital 参数，用 total_cost 近似
    is_small_capital = (total_capital or total_cost) < 1_000_000

    signals_map = signals_map or {}

    for h in holdings:
        code = str(h.get("code", ""))
        code_6 = code.zfill(6)
        cost = h.get("shares", 0) * h.get("cost", 0)
        pct = (cost / total_cost) * 100

        # 拿质量标签
        sig = signals_map.get(code) or signals_map.get(code_6) or {}
        is_king = sig.get("is_10y_king", False)

        # 选阈值档位
        if is_king and is_small_capital:
            warn_line, danger_line, tier_label = 35, 45, "十年王者+小资金"
        elif is_king:
            warn_line, danger_line, tier_label = 25, 35, "十年王者+大资金"
        else:
            warn_line, danger_line, tier_label = 20, 30, "普通标的"

        if pct >= danger_line:
            warnings.append({
                "code": h["code"],
                "name": h.get("name", ""),
                "pct": pct,
                "level": "danger",
                "tier": tier_label,
                "text": f"仓位{pct:.1f}% ≥ {danger_line}%（{tier_label}危险线），严重偏重！建议减仓分散",
            })
        elif pct >= warn_line:
            warnings.append({
                "code": h["code"],
                "name": h.get("name", ""),
                "pct": pct,
                "level": "warning",
                "tier": tier_label,
                "text": f"仓位{pct:.1f}% ≥ {warn_line}%（{tier_label}警告线），注意分散",
            })

    return warnings


# ============================================
# 机会成本比较（巴菲特：卖掉便宜的买更便宜的）
# ============================================

def compare_opportunity_cost(holding_signals, watchlist_buy_signals):
    """
    比较持仓 vs 关注表买入机会
    如果关注表有明显更优的买入机会，建议换仓
    返回：换仓建议列表
    """
    if not holding_signals or not watchlist_buy_signals:
        return []

    suggestions = []

    # 找到关注表中最强的买入信号
    buy_rank = {"buy_heavy": 0, "buy_medium": 1, "buy_light": 2, "buy_watch": 3}
    best_buys = sorted(
        [s for s in watchlist_buy_signals if s.get("signal") in buy_rank],
        key=lambda x: buy_rank.get(x.get("signal", ""), 99)
    )

    if not best_buys:
        return []

    # 找持仓中表现最差的（PE偏高或信号为卖出的）
    sell_rank = {"sell_heavy": 0, "sell_medium": 1, "sell_light": 2, "sell_watch": 3, "hold_keep": 4}
    worst_holds = sorted(
        [s for s in holding_signals if s.get("signal") in sell_rank],
        key=lambda x: sell_rank.get(x.get("signal", ""), 99)
    )

    for sell_stock in worst_holds:
        sell_signal = sell_stock.get("signal", "")
        if sell_signal not in ("sell_heavy", "sell_medium", "sell_light", "sell_watch"):
            continue

        for buy_stock in best_buys[:2]:  # 最多推荐2只
            buy_signal = buy_stock.get("signal", "")

            # 只有买入信号比卖出信号更强时才建议换仓
            if buy_rank.get(buy_signal, 99) >= 3:  # buy_watch太弱不建议换
                continue

            # 计算建议卖出比例
            if sell_signal in ("sell_heavy", "sell_medium"):
                sell_ratio = "全部"
            elif sell_signal == "sell_light":
                sell_ratio = "1/2"
            else:
                sell_ratio = "1/3"

            suggestions.append({
                "sell_code": sell_stock["code"],
                "sell_name": sell_stock.get("name", ""),
                "sell_signal": sell_signal,
                "sell_ratio": sell_ratio,
                "buy_code": buy_stock["code"],
                "buy_name": buy_stock.get("name", ""),
                "buy_signal": buy_signal,
                "text": f"建议卖出{sell_stock.get('name','')}{sell_ratio}→买入{buy_stock.get('name','')}",
            })

    return suggestions
