"""
回测引擎 - 用历史月度数据跑模型，输出买卖信号
复用 screener.py 的行业 PE 区间和评估逻辑

文件结构（按逻辑分层）：
  1. 策略开关与股票行业映射
  2. 数据加载层    - 月度快照、原始数据、回购、指数 PE、年报序列
  3. 指标工具层    - ROE 历史均值、十年王者、好公司判定、回购加分
  4. 宏观温度计    - 沪深 300 指数 PE 历史分位
  5. 护城河检查    - 分周期股/非周期股两套规则，含消费龙头豁免
  6. 股票评分      - 周期股反向评分、非周期股主流程
  7. 月度信号入口  - get_month_signals()
"""

import json
import os
import random
import string

from screener import match_industry_pe, COMPLEXITY_ROE_ADJUST

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ============================================================
# 1. 策略开关
# ============================================================
# 巴菲特/芒格核心理念：不参与大宗商品周期股
# "时间是优秀企业的朋友，平庸企业的敌人"
# 设为 False 时，所有周期股信号从 get_month_signals 的输出中过滤
# 保留开关以便未来重新启用（evaluate_cycle_stock 代码仍然可用）
CYCLE_STOCKS_ENABLED = False

# 中国国情版 v3 规则开关（REQ-160~169）
# 详见 docs/REQUIREMENTS.md 第十六节
# 默认开启；关闭时用于 A/B 对比验证（check_china_v3_rules 返回空结果）
CHINA_V3_ENABLED = True

# 股票池行业映射（手工标注，经 match_industry_pe 模糊匹配到 INDUSTRY_PE）
# 70 只股票按上市时间 + 类型分布：
#   S01-S30：初始 30 只（6 个类别）
#   S31-S50：多样化扩充（好公司/烂公司/周期股/退市股）
#   S51-S58：低价高分红蓝筹（小资金友好）
#   S59-S70：低价多样化（覆盖更多行业/上市时间）
STOCK_INDUSTRY = {
    "S01": "白酒",          # 贵州茅台
    "S02": "白酒",          # 五粮液
    "S03": "中药",          # 云南白药
    "S04": "医药",          # 恒瑞医药
    "S05": "家电",          # 格力电器
    "S06": "石油",          # 中国石油
    "S07": "新能源",        # ST华锐（风电）
    "S08": "面板",          # 京东方A
    "S09": "电子",          # 保千里
    "S10": "生物制品",      # 长生生物
    "S11": "保险",          # 中国平安
    "S12": "银行",          # 兴业银行
    "S13": "地产",          # 万科A
    "S14": "免税",          # 中国中免
    "S15": "银行",          # 招商银行
    "S16": "证券",          # 东方财富
    "S17": "锂电",          # 宁德时代
    "S18": "消费电子",      # 立讯精密
    "S19": "稀土",          # 北方稀土
    "S20": "新能源",        # 比亚迪
    "S21": "互联网",        # 乐视退
    "S22": "互联网",        # 暴风退
    "S23": "电子",          # 欧菲退
    "S24": "医药",          # ST康美
    "S25": "养殖",          # 雏鹰退
    "S26": "调味品",        # 海天味业
    "S27": "医疗器械",      # 迈瑞医疗
    "S28": "半导体",        # 中芯国际
    "S29": "软件",          # 华大九天
    "S30": "通信",          # 中国移动
    # 第二批新增
    "S31": "乳制品",        # 伊利股份
    "S32": "家电",          # 海尔智家
    "S33": "中药",          # 片仔癀
    "S34": "医药",          # 爱尔眼科
    "S35": "地产",          # 华夏幸福（地产暴雷）
    "S36": "建筑",          # 中国铁建
    "S37": "交通运输",      # 海航控股（航空）
    "S38": "银行",          # 工商银行
    "S39": "汽车零部件",    # 上汽集团（当作汽车）
    "S40": "交通运输",      # 上海机场
    "S41": "轨道交通",      # 中国中车
    "S42": "汽车玻璃",      # 福耀玻璃
    "S43": "机械制造",      # 三一重工
    "S44": "养殖",          # 牧原股份
    "S45": "锂电",          # 赣锋锂业
    "S46": "锂电",          # 天齐锂业
    "S47": "电子",          # 康得新（新材料/膜）
    "S48": "养殖",          # 獐子岛
    "S49": "食品饮料",      # 金龙鱼
    "S50": "医疗器械",      # 联影医疗
    # 第三批新增：低价高分红蓝筹（小资金友好）
    "S51": "铁路公路",      # 大秦铁路
    "S52": "银行",          # 农业银行
    "S53": "银行",          # 中国银行
    "S54": "银行",          # 建设银行
    "S55": "石油",          # 中国石化
    "S56": "钢铁",          # 宝钢股份
    "S57": "电力",          # 华能国际
    "S58": "煤炭",          # 陕西煤业
    # 第四批新增：低价多样化
    "S59": "电力",          # 长江电力（水电）
    "S60": "交通运输",      # 皖通高速
    "S61": "电力",          # 国投电力
    "S62": "证券",          # 中信证券
    "S63": "证券",          # 华泰证券
    "S64": "银行",          # 交通银行
    "S65": "银行",          # 民生银行
    "S66": "机械制造",      # 中联重科
    "S67": "电子制造",      # 工业富联
    "S68": "通信",          # 中国电信
    "S69": "电力",          # 中国广核（核电算电力）
    "S70": "新能源",        # 三峡能源
}

# ============================================================
# 2. 数据加载层
# ============================================================
# 所有文件 I/O + 模块级缓存都在这里
# 模块级缓存避免重复读盘（月度回测会反复查同一个文件）
_raw_cache = {}           # 股票原始数据（financial_data 历史序列）
_buybacks_cache = None    # 全股票池的历史回购记录
_hs300_pe_cache = None    # 沪深 300 指数 PE 历史（宽基温度计用）


def _load_hs300_pe():
    """懒加载沪深 300 指数 PE 历史数据（数据加载层）"""
    global _hs300_pe_cache
    if _hs300_pe_cache is not None:
        return _hs300_pe_cache
    path = os.path.join(SCRIPT_DIR, "backtest_data", "hs300_pe.json")
    if not os.path.exists(path):
        _hs300_pe_cache = {}
        return _hs300_pe_cache
    with open(path, "r", encoding="utf-8") as f:
        _hs300_pe_cache = json.load(f)
    return _hs300_pe_cache


# ============================================================
# 4. 宏观温度计（多维度综合）
# ============================================================
# 巴菲特/芒格："别人贪婪时我恐惧，别人恐惧时我贪婪"
#
# 5 个维度综合投票（每个给 -2~+2 档温度）：
#   1. 沪深300 PE 中位数 历史分位
#   2. 沪深300 PB 中位数 历史分位
#   3. 巴菲特指标（总市值/GDP）历史分位
#   4. 代表股票池 PE 中位数 历史分位（老白马横截面）
#   5. 绝对阈值兜底（当历史样本不足时）
#
# 依据：单一 PE 指标对"中等幅度顶底"不敏感，2007 大顶/2008 大底因
#       历史样本不足失效。多维度综合后准确率显著提升。

# 缓存
_hs300_pb_cache = None
_buffett_idx_cache = None


def _load_hs300_pb():
    """懒加载沪深 300 PB 历史数据"""
    global _hs300_pb_cache
    if _hs300_pb_cache is not None:
        return _hs300_pb_cache
    path = os.path.join(SCRIPT_DIR, "backtest_data", "hs300_pb.json")
    if not os.path.exists(path):
        _hs300_pb_cache = {}
        return _hs300_pb_cache
    with open(path, "r", encoding="utf-8") as f:
        _hs300_pb_cache = json.load(f)
    return _hs300_pb_cache


def _load_buffett_index():
    """懒加载巴菲特指标（总市值/GDP）历史数据"""
    global _buffett_idx_cache
    if _buffett_idx_cache is not None:
        return _buffett_idx_cache
    path = os.path.join(SCRIPT_DIR, "backtest_data", "buffett_index.json")
    if not os.path.exists(path):
        _buffett_idx_cache = {}
        return _buffett_idx_cache
    with open(path, "r", encoding="utf-8") as f:
        _buffett_idx_cache = json.load(f)
    return _buffett_idx_cache


def _percentile_to_temperature(current, history_values):
    """
    把"当前值在历史分位"转成温度档位
    - 历史 85% 以上 → 极热 (+2)
    - 70% ~ 85% → 偏热 (+1)
    - 30% ~ 70% → 正常 (0)
    - 15% ~ 30% → 偏冷 (-1)
    - 15% 以下 → 极冷 (-2)
    样本不足时返回 None（表示"无判定"）
    """
    if current is None or not history_values or len(history_values) < 36:
        return None
    below = sum(1 for v in history_values if v < current)
    pct = below / len(history_values)
    if pct >= 0.85:
        return 2
    if pct >= 0.70:
        return 1
    if pct <= 0.15:
        return -2
    if pct <= 0.30:
        return -1
    return 0


def _absolute_threshold_temperature(pe_median, pb_median):
    """
    绝对阈值兜底（当历史样本不足时使用）
    沪深 300 中位数 PE/PB 的经验阈值（基于 20 年观察）
      PE 中位数 ≥ 50 → +2（2007/2015 级别）
      PE 中位数 ≥ 35 → +1
      PE 中位数 ≤ 15 → -2
      PE 中位数 ≤ 20 → -1
    """
    votes = []
    if pe_median is not None:
        if pe_median >= 50:
            votes.append(2)
        elif pe_median >= 35:
            votes.append(1)
        elif pe_median <= 15:
            votes.append(-2)
        elif pe_median <= 20:
            votes.append(-1)
        else:
            votes.append(0)
    if pb_median is not None:
        if pb_median >= 5:
            votes.append(2)
        elif pb_median >= 3.5:
            votes.append(1)
        elif pb_median <= 1.8:
            votes.append(-2)
        elif pb_median <= 2.3:
            votes.append(-1)
        else:
            votes.append(0)
    if not votes:
        return None
    return round(sum(votes) / len(votes))


def _get_stock_pool_pe_temperature(year, month, lookback_years=10):
    """
    代表股票池 PE 分位温度（横截面验证）
    用全股票池（70 只）当月的 PE 中位数，和过去 N 年的历史比较
    """
    data = load_month_data(year, month)
    if not data:
        return None
    stocks = data.get("stocks", {})
    pes = [s.get("pe_ttm") for s in stocks.values() if s.get("pe_ttm") and s.get("pe_ttm") > 0]
    if len(pes) < 10:
        return None
    pes.sort()
    current_median = pes[len(pes) // 2]

    # 历史窗口
    history = []
    for back in range(1, lookback_years * 12 + 1):
        by, bm = year, month - back
        while bm <= 0:
            bm += 12
            by -= 1
        if by < 2001:
            break
        hdata = load_month_data(by, bm)
        if not hdata:
            continue
        hpes = [s.get("pe_ttm") for s in hdata.get("stocks", {}).values()
                if s.get("pe_ttm") and s.get("pe_ttm") > 0]
        if len(hpes) >= 10:
            hpes.sort()
            history.append(hpes[len(hpes) // 2])

    return _percentile_to_temperature(current_median, history)


def get_composite_market_temperature(year, month, lookback_years=10):
    """
    多维度综合市场温度计（主入口）
    返回 (level, details) —— level 是最终温度 -2~+2，details 是各维度详情
    """
    pe_data = _load_hs300_pe()
    pb_data = _load_hs300_pb()
    buf_data = _load_buffett_index()
    target = f"{year}-{month:02d}"

    votes = []
    details = {}

    # 维度 1：沪深300 PE 中位数 历史分位
    current_pe_med = (pe_data.get(target) or {}).get("pe_median")
    pe_hist = []
    cutoff = f"{year - lookback_years}-{month:02d}"
    for m, d in pe_data.items():
        if cutoff <= m < target and d.get("pe_median") is not None:
            pe_hist.append(d["pe_median"])
    v_pe = _percentile_to_temperature(current_pe_med, pe_hist)
    details["pe"] = {"value": current_pe_med, "temp": v_pe, "samples": len(pe_hist)}
    if v_pe is not None:
        votes.append(v_pe)

    # 维度 2：沪深300 PB 中位数 历史分位
    current_pb_med = (pb_data.get(target) or {}).get("pb_median")
    pb_hist = []
    for m, d in pb_data.items():
        if cutoff <= m < target and d.get("pb_median") is not None:
            pb_hist.append(d["pb_median"])
    v_pb = _percentile_to_temperature(current_pb_med, pb_hist)
    details["pb"] = {"value": current_pb_med, "temp": v_pb, "samples": len(pb_hist)}
    if v_pb is not None:
        votes.append(v_pb)

    # 维度 3：巴菲特指标（直接用数据提供的 10 年分位）
    buf = buf_data.get(target) or {}
    buf_pct = buf.get("pct_10y")
    v_buf = None
    if buf_pct is not None:
        # 巴菲特指标的分位已经是 0-1，直接映射
        if buf_pct >= 0.85:
            v_buf = 2
        elif buf_pct >= 0.70:
            v_buf = 1
        elif buf_pct <= 0.15:
            v_buf = -2
        elif buf_pct <= 0.30:
            v_buf = -1
        else:
            v_buf = 0
        votes.append(v_buf)
    details["buffett"] = {"value": buf_pct, "temp": v_buf}

    # 维度 4：股票池横截面（70 只股票的 PE 中位数历史分位）
    v_pool = _get_stock_pool_pe_temperature(year, month, lookback_years)
    details["pool"] = {"temp": v_pool}
    if v_pool is not None:
        votes.append(v_pool)

    # 维度 5：绝对阈值兜底（无论样本多少都算一次）
    v_abs = _absolute_threshold_temperature(current_pe_med, current_pb_med)
    details["absolute"] = {"temp": v_abs}
    if v_abs is not None:
        votes.append(v_abs)

    # 综合：平均后四舍五入到最近档位
    if not votes:
        return 0, details
    avg = sum(votes) / len(votes)
    if avg >= 1.5:
        final = 2
    elif avg >= 0.5:
        final = 1
    elif avg <= -1.5:
        final = -2
    elif avg <= -0.5:
        final = -1
    else:
        final = 0
    details["avg"] = round(avg, 2)
    details["final"] = final
    details["votes"] = votes
    return final, details


# 旧接口：保留签名以向后兼容，内部改为调用综合温度计
def get_hs300_temperature(year, month, lookback_years=10):
    """
    [已升级为多维度综合温度计] 保留原函数名以向后兼容。
    现在内部调用 get_composite_market_temperature 返回 5 维综合温度。
    """
    level, _ = get_composite_market_temperature(year, month, lookback_years)
    return level


# 原始单维度 PE 温度计（仅保留不用，仅供调试）
def _get_hs300_pe_only_temperature(year, month, lookback_years=10):
    """
    沪深 300 指数温度计（基于中位数市盈率的历史分位）
    这是真正的"宽基指数温度计"，反映全市场的估值水平
    不同于"股票池温度计"（仅基于 70 只股票）

    温度分级：
      极热 (+2): 历史 85% 分位以上 —— 如 2007-10 大顶、2015-06 杠杆牛顶
      偏热 (+1): 70% 分位以上
      正常 (0):  30%~70% 分位
      偏冷 (-1): 30% 分位以下
      极冷 (-2): 15% 分位以下 —— 如 2008-11 大底、2018-12 熊底

    依据：巴菲特/芒格"别人贪婪时我恐惧，别人恐惧时我贪婪"
    """
    data = _load_hs300_pe()
    if not data:
        return 0
    target = f"{year}-{month:02d}"
    if target not in data:
        return 0
    current = data[target].get("pe_median")
    if current is None:
        return 0

    # 取过去 lookback_years 年的历史
    cutoff = f"{year - lookback_years}-{month:02d}"
    history = [
        d["pe_median"]
        for m, d in data.items()
        if cutoff <= m < target and d.get("pe_median") is not None
    ]
    if len(history) < 60:  # 至少 5 年历史
        return 0

    sorted_hist = sorted(history)
    n = len(sorted_hist)
    pct_85 = sorted_hist[int(n * 0.85)]
    pct_70 = sorted_hist[int(n * 0.70)]
    pct_30 = sorted_hist[int(n * 0.30)]
    pct_15 = sorted_hist[int(n * 0.15)]

    if current >= pct_85:
        return 2
    if current >= pct_70:
        return 1
    if current <= pct_15:
        return -2
    if current <= pct_30:
        return -1
    return 0


def _load_buybacks():
    """懒加载全股票池的回购历史（数据加载层）"""
    global _buybacks_cache
    if _buybacks_cache is not None:
        return _buybacks_cache
    path = os.path.join(SCRIPT_DIR, "backtest_data", "buybacks.json")
    if not os.path.exists(path):
        _buybacks_cache = {}
        return _buybacks_cache
    with open(path, "r", encoding="utf-8") as f:
        # 文件按 sid 组织：{"S01": [{"start_date", "status", "amount", ...}]}
        _buybacks_cache = json.load(f)
    return _buybacks_cache


# ============================================================
# 3. 指标工具层（部分）—— 回购加分
# ============================================================
# 其余工具（_roe_historical_avg、_get_recent_roe、check_10_year_king、
# is_good_quality_company）见下方的"指标工具层"段落

def get_buyback_score(sid, year, month, lookback_years=5):
    """
    计算某股票在某时点的"回购加分"
    往前看 N 年，计算已完成的回购金额总和，分级加分：
      ≥50 亿 → +15（高加分，巴菲特最爱）
      ≥10 亿 → +8
      ≥1 亿  → +3
      否则   → 0
    """
    buybacks = _load_buybacks()
    records = buybacks.get(sid) or []
    if not records:
        return 0, 0  # (score, total_amount_yi)

    cutoff_y = year - lookback_years
    total = 0.0
    for r in records:
        if "完成" not in str(r.get("status", "")):
            continue
        date_str = str(r.get("notice_date") or r.get("start_date") or "")[:7]
        if len(date_str) < 7:
            continue
        try:
            ry, rm = int(date_str[:4]), int(date_str[5:7])
        except ValueError:
            continue
        # 只累计 cutoff_y ~ year-month 之间的回购
        if (ry, rm) > (year, month):
            continue
        if ry < cutoff_y:
            continue
        amount = r.get("amount")
        if amount is None or (isinstance(amount, float) and amount != amount):
            continue
        total += float(amount)

    total_yi = total / 1e8  # 转亿元
    if total_yi >= 50:
        score = 15
    elif total_yi >= 10:
        score = 8
    elif total_yi >= 1:
        score = 3
    else:
        score = 0
    return score, total_yi


def load_raw_data(sid):
    """加载某股票的完整原始数据（含多年财务序列），带缓存（数据加载层）"""
    if sid in _raw_cache:
        return _raw_cache[sid]
    path = os.path.join(SCRIPT_DIR, "backtest_data", f"raw_{sid}.json")
    if not os.path.exists(path):
        _raw_cache[sid] = None
        return None
    with open(path, "r", encoding="utf-8") as f:
        _raw_cache[sid] = json.load(f)
    return _raw_cache[sid]


def get_annual_reports_before(sid, year, month, lookback_years=5):
    """
    获取某月"可见"的年报序列（严格避免未来函数）
    年报披露延后 -> 保守规则：当前 year-month 最多只能看到 year-1 的年报，
    且必须在该年报披露期（次年4月）之后。
    返回按日期降序排列的最近N年年报 [最新, 次新, ...]
    """
    raw = load_raw_data(sid)
    if not raw:
        return []
    fd = raw.get("financial_data") or []
    # 只取年报（12月数据）
    annuals = [r for r in fd if str(r.get("date", ""))[5:7] == "12"]
    if not annuals:
        return []
    # 延迟规则：2024-04 可以看到 2023-12 的年报；2024-03 只能看到 2022-12
    if month >= 4:
        max_year = year - 1
    else:
        max_year = year - 2
    visible = [r for r in annuals if int(str(r["date"])[:4]) <= max_year]
    visible.sort(key=lambda r: str(r["date"]), reverse=True)
    return visible[:lookback_years]


# ============================================================
# 5. 护城河检查层
# ============================================================
# 筛选清单"第一关"——任一规则触发则买入降级为 hold、持仓触发卖出
# 对外统一入口 check_moat()，内部按行业分发：
#   - 周期股（type=cycle）→ check_moat_cycle：只看"连续亏损/营收暴跌"
#   - 非周期股 → check_moat_normal：8 条规则综合判断
# 另外 get_cash_flow_warnings() 给"消费龙头被豁免时"输出重点关注警示

def check_moat(sid, year, month):
    """
    护城河趋势检查（筛选第一关）
    按行业分发到 cycle / normal 两套规则
    返回 (is_intact, problems) —— intact=True 表示护城河完好
    """
    industry = STOCK_INDUSTRY.get(sid, "")
    pe_range = match_industry_pe(industry)
    if pe_range.get("type") == "cycle":
        return check_moat_cycle(sid, year, month)
    return check_moat_normal(sid, year, month)


def check_moat_normal(sid, year, month):
    """
    非周期股的护城河检查
    数据不足时返回 (True, []) —— 疑罪从无
    """
    # 取 6 年数据确保规则 8（5 年趋势检查）有充足样本
    reports = get_annual_reports_before(sid, year, month, lookback_years=6)
    if len(reports) < 2:
        return True, []

    problems = []

    # 提取指标序列（最新在前）
    roe_list = [r.get("roe") for r in reports if r.get("roe") is not None]
    gm_list = [r.get("gross_margin") for r in reports if r.get("gross_margin") is not None]
    rev_list = [r.get("revenue_growth") for r in reports if r.get("revenue_growth") is not None]
    debt_list = [r.get("debt_ratio") for r in reports if r.get("debt_ratio") is not None]
    # 经营现金流/每股收益 比值序列（巴菲特核心：现金流应≥净利润）
    cash_ratio_list = []
    for r in reports:
        eps = r.get("eps")
        ocf = r.get("ocf_per_share")
        if eps is not None and ocf is not None and eps > 0:
            cash_ratio_list.append(ocf / eps)

    # 规则1：最新亏损（ROE<0）→ 直接松动
    if roe_list and roe_list[0] < 0:
        problems.append(f"最新ROE={roe_list[0]:.1f}%（亏损）")

    # 规则2：ROE 单年暴跌 ≥6 个百分点
    # 豁免1：跌后 ROE 仍 ≥15% 属于"合格公司"的正常波动
    # 豁免2：十年王者的单年冲击豁免（巴菲特"王者暂时摔跤"理念）
    #       熊市/危机时王者 ROE 短期下滑是常态，不应判松动
    # 依据：巴菲特 1979 年致股东信及伯克希尔自身 KPI —— 15% 是"合格底线"，20% 是"卓越"
    # 真正恶化的股票（万科跌到 9% 且已不是十年王者）照样会被抓出来
    if len(roe_list) >= 2:
        drop = roe_list[1] - roe_list[0]
        if drop >= 6 and roe_list[0] < 15:
            # 检查是否为十年王者（豁免暂时冲击）
            is_king, _, _ = check_10_year_king(sid, year, month)
            if not is_king:
                problems.append(f"ROE单年暴跌{drop:.1f}pp（{roe_list[1]:.1f}%→{roe_list[0]:.1f}%）")

    # 规则3：ROE 连续3年下滑 且 最新<15%
    if len(roe_list) >= 3:
        r = roe_list[:3]
        if r[0] < r[1] < r[2] and r[0] < 15:
            problems.append(f"ROE连续3年下滑至{r[0]:.1f}%（<15%底线）")

    # 规则4：毛利率连续3年下滑 且 累计跌幅 ≥5 个百分点
    if len(gm_list) >= 3:
        g = gm_list[:3]
        if g[0] < g[1] < g[2] and (g[2] - g[0]) >= 5:
            problems.append(f"毛利率连续3年下滑（{g[2]:.1f}%→{g[0]:.1f}%）")

    # 规则5：连续2年营收负增长 + ROE 同时跌破 15%（双重证据）
    # 依据：ROE 是核心指标，营收是辅助。营收短期下滑如果不影响盈利能力，
    # 说明是行业周期冲击而非护城河消失（如五粮液 2014 年三公消费冲击）
    # 只有"营收连降 + ROE 也跌破合格线"才算真正恶化
    if len(rev_list) >= 2 and len(roe_list) >= 1:
        if rev_list[0] < 0 and rev_list[1] < 0 and roe_list[0] < 15:
            problems.append(
                f"营收连续2年负增长（{rev_list[1]:.1f}%, {rev_list[0]:.1f}%）"
                f"+ ROE仅{roe_list[0]:.1f}%"
            )

    # 规则6：负债率升 + ROE 同时恶化（双重证据才算松动）
    # 单纯负债率升不算松动 —— 家电/地产等行业高负债率是常态
    if len(debt_list) >= 3 and len(roe_list) >= 3:
        d = debt_list[:3]
        r = roe_list[:3]
        debt_rising = d[0] - d[2] > 10 and d[0] > 70
        roe_falling = r[0] < r[2] and r[0] < 15
        if debt_rising and roe_falling:
            problems.append(
                f"负债率3年升{d[0]-d[2]:.1f}pp至{d[0]:.1f}% + ROE跌至{r[0]:.1f}%（双重恶化）"
            )

    # 规则8：ROE 长期单调下降 且 跌破"卓越线"（"温水煮青蛙"式护城河缩小）
    # 近5年 ROE 连续下降 + 累计跌幅 ≥ 10pp + 最新 < 20%（巴菲特卓越线） → 松动
    # 三个条件缺一不可：
    #   1) 单调下降 —— 证明是趋势性恶化而非波动
    #   2) 累计 ≥10pp —— 幅度够大才有统计意义
    #   3) 最新 <20% —— 从卓越级跌到合格级才算护城河"缩小"
    # 豁免案例（不触发）：
    #   - 茅台 2017：45→39→32→26→24%（累计降21pp 但最新24%仍≥20%，规模扩大后自然正常化）
    #   - 某公司 30→27→24→21→20（累计降10pp 但最新20%刚好在卓越线，算稳定）
    # 触发案例：
    #   - 平安 2024：24→20→13→10→10%（累计降15pp + 最新10% <20%）
    if len(roe_list) >= 5:
        r = roe_list[:5]
        monotone_down = r[0] < r[1] < r[2] < r[3] < r[4]
        total_drop = r[4] - r[0]
        if monotone_down and total_drop >= 10 and r[0] < 20:
            problems.append(
                f"ROE近5年持续下降且跌破卓越线（{r[4]:.0f}→{r[3]:.0f}→{r[2]:.0f}→{r[1]:.0f}→{r[0]:.0f}%，"
                f"累计降{total_drop:.0f}pp，最新仅{r[0]:.0f}%）→ 护城河在缩小"
            )

    # 规则7：盈利质量恶化（巴菲特核心 —— 现金流不会骗人）
    # 经营现金流与每股收益的比值反映"账面利润多少变成了真金白银"
    # 连续2年比值<0.3 → 盈利严重虚化（典型：万科2021-2022，比值从0.18跌到0.12）
    # 豁免条件：
    #   A. 银行/保险/证券：经营现金流含存款/保费/客户保证金波动
    #   B. 消费龙头豁免：ROE 仍 ≥ 20% 且 毛利率 ≥ 50%（白酒等）
    #      —— 五粮液2013-2014现金流差是三公消费限制导致的经销商去库存+延期付款，
    #         不是财务造假。这类"高ROE+高毛利"的消费龙头，护城河（品牌+定价权）
    #         没受损，短期现金流异常应豁免
    industry = STOCK_INDUSTRY.get(sid, "")
    skip_cash_check_bank = any(k in industry for k in ["银行", "保险", "证券", "券商"])
    # 消费龙头判定：最新 ROE ≥ 15%（巴菲特合格线）且 毛利率 ≥ 50%
    # 高毛利 50% 是关键过滤器 —— 康美、万科等都没这么高的毛利
    # 15% 对应巴菲特"合格公司"下限，避免误杀五粮液这类暂时跌破 20% 的龙头
    is_consumer_leader = (
        len(roe_list) >= 1
        and len(gm_list) >= 1
        and roe_list[0] >= 15
        and gm_list[0] >= 50
    )
    if not skip_cash_check_bank and not is_consumer_leader and len(cash_ratio_list) >= 2:
        latest = cash_ratio_list[0]
        prev = cash_ratio_list[1]
        if latest < 0.3 and prev < 0.3:
            problems.append(
                f"盈利质量恶化：近2年经营现金流仅为净利润的"
                f"{prev:.0%}、{latest:.0%}（账面盈利未变现）"
            )
        elif latest < 0.2 and prev < 0.5:
            problems.append(
                f"盈利快速虚化：经营现金流覆盖率仅{latest:.0%}（账面利润未收到现金）"
            )

    return len(problems) == 0, problems


def get_cash_flow_warnings(sid, year, month):
    """
    消费龙头现金流警示（已豁免但需重点关注）
    返回 warnings 列表（字符串），空列表=无警示

    背景：消费龙头（ROE≥20% 且 毛利率≥50%）的现金流连续2年<30% 时，
    按护城河规则会触发松动。但按巴菲特/芒格理念，高ROE+高毛利的消费龙头
    的现金流短期异常通常是行业周期扰动（如白酒塑化剂+三公消费），而非
    真正的财务造假。因此规则7对消费龙头做了豁免。

    豁免带来的风险：如果确实是造假或真恶化，豁免会导致未能及时识别。
    因此对已豁免的持仓股票，应该输出警示，用多维度线索协助判断：
      1. 毛利率是否仍稳定（定价权没丢）
      2. ROE 是否仍保持高位
      3. 净利润是否持续为正（而非极端虚高但崩塌在即）
      4. 营收是否同步下滑（行业性证据）
    """
    reports = get_annual_reports_before(sid, year, month, lookback_years=5)
    if len(reports) < 2:
        return []

    roe_list = [r.get("roe") for r in reports if r.get("roe") is not None]
    gm_list = [r.get("gross_margin") for r in reports if r.get("gross_margin") is not None]
    rev_list = [r.get("revenue_growth") for r in reports if r.get("revenue_growth") is not None]
    cash_ratio_list = []
    for r in reports:
        eps = r.get("eps")
        ocf = r.get("ocf_per_share")
        if eps is not None and ocf is not None and eps > 0:
            cash_ratio_list.append(ocf / eps)

    industry = STOCK_INDUSTRY.get(sid, "")
    skip_bank = any(k in industry for k in ["银行", "保险", "证券", "券商"])
    if skip_bank or len(cash_ratio_list) < 2 or len(roe_list) < 1 or len(gm_list) < 1:
        return []

    # 只对被豁免的消费龙头产生警示（阈值与 check_moat_normal 保持一致）
    is_consumer_leader = roe_list[0] >= 15 and gm_list[0] >= 50
    latest = cash_ratio_list[0]
    prev = cash_ratio_list[1]
    cash_flow_bad = (latest < 0.3 and prev < 0.3) or (latest < 0.2 and prev < 0.5)

    if not (is_consumer_leader and cash_flow_bad):
        return []

    # 触发警示：多维度状态说明
    warnings = []
    status_lines = [
        f"消费龙头现金流异常（连续2年仅 {prev:.0%}、{latest:.0%}）已豁免",
        f"但需重点关注：ROE {roe_list[0]:.0f}% 毛利 {gm_list[0]:.0f}% 仍强劲",
    ]
    # 多维校验 1：ROE 是否稳定（非急剧下滑）
    if len(roe_list) >= 2:
        roe_drop = roe_list[1] - roe_list[0]
        if roe_drop >= 5:
            status_lines.append(f"ROE单年跌{roe_drop:.0f}pp 需警惕")
        else:
            status_lines.append(f"ROE稳定（降{roe_drop:.1f}pp）")
    # 多维校验 2：毛利率是否稳定
    if len(gm_list) >= 2:
        gm_drop = gm_list[1] - gm_list[0]
        if gm_drop >= 5:
            status_lines.append(f"毛利率降{gm_drop:.0f}pp 需警惕")
        else:
            status_lines.append(f"毛利率稳定（降{gm_drop:.1f}pp）")
    # 多维校验 3：营收同步下滑（行业周期证据）
    if len(rev_list) >= 1:
        if rev_list[0] < -5:
            status_lines.append(f"营收同步下滑{rev_list[0]:.0f}%→疑似行业周期扰动")
        elif rev_list[0] < 0:
            status_lines.append(f"营收轻微下滑{rev_list[0]:.0f}%")
        else:
            status_lines.append(f"营收仍增长{rev_list[0]:.0f}%→警惕造假可能")

    warnings.append(" | ".join(status_lines))
    return warnings


def check_moat_cycle(sid, year, month):
    """
    周期股的"基本面崩塌"检查
    周期股 ROE/营收本来就波动剧烈，不能用"连续下滑"判松动
    只在下列情况才认为真的完蛋：
      1. 连续 3 年亏损（ROE < 0）
      2. 营收连续 3 年大幅萎缩（累计跌幅 > 30%）
      3. 最新年报 ROE < 0 且营收也负增长（底部恶化而非周期性低谷）
    """
    reports = get_annual_reports_before(sid, year, month, lookback_years=5)
    if len(reports) < 3:
        return True, []

    problems = []
    roe_list = [r.get("roe") for r in reports[:3]]
    rev_list = [r.get("revenue_growth") for r in reports[:3]]

    # 规则1：连续 3 年亏损
    if all(r is not None and r < 0 for r in roe_list):
        problems.append(f"连续3年亏损（ROE全负）")

    # 规则2：营收连续 3 年大幅萎缩（每年>-10%）
    if all(r is not None and r < -10 for r in rev_list):
        cum = sum(rev_list)
        problems.append(f"营收连续3年暴跌（累计{cum:.0f}%）")

    return len(problems) == 0, problems


# ============================================================
# 2. 数据加载层（续）—— 月度快照与股票列表
# ============================================================

def load_month_data(year, month):
    """加载某月的历史快照（数据加载层）"""
    path = os.path.join(SCRIPT_DIR, "backtest_data", "monthly", f"{year}-{month:02d}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def load_stock_list(subset_ids=None):
    """
    加载股票列表（含真实信息，不暴露给前端）

    Args:
      subset_ids: 可选的股票 ID 列表（如 ['S01', 'S05', ...]）
                  传入时只返回这个子集，用于多批次不重叠抽样回测
                  None 或空 list 时返回全部股票
    """
    path = os.path.join(SCRIPT_DIR, "backtest_stocks.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    stocks = {}
    subset_set = set(subset_ids) if subset_ids else None
    for cat, items in data["categories"].items():
        for item in items:
            if subset_set and item["id"] not in subset_set:
                continue
            stocks[item["id"]] = {
                "code": item["code"],
                "name": item["name"],
                "category": cat,
            }
    return stocks


def load_events():
    """加载脱敏事件"""
    path = os.path.join(SCRIPT_DIR, "backtest_events.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("events", {})
    return {}


# 中文匿名命名词库（地名+形容词+名词，组合 27000 种，远超 90 只股票）
_ANON_PLACES = [
    "成都", "杭州", "苏州", "昆明", "西安", "长沙", "厦门", "青岛",
    "南京", "武汉", "深圳", "重庆", "大连", "桂林", "丽江", "拉萨",
    "敦煌", "三亚", "洛阳", "泉州", "济南", "贵阳", "兰州", "太原",
    "福州", "合肥", "石家庄", "银川", "海口", "珠海",
]
_ANON_ADJS = [
    "漂亮的", "勇敢的", "快乐的", "聪明的", "温柔的", "神秘的",
    "金色的", "银色的", "飞翔的", "闪亮的", "安静的", "热情的",
    "优雅的", "机灵的", "敏捷的", "沉稳的", "活泼的", "灵动的",
    "坚韧的", "清澈的", "明亮的", "温暖的", "凉爽的", "幸运的",
    "自由的", "勤劳的", "善良的", "高贵的", "纯净的", "古老的",
]
_ANON_NOUNS = [
    "蝴蝶", "白鹤", "雪豹", "海豚", "凤凰", "麒麟", "熊猫", "孔雀",
    "老虎", "白鹭", "仙鹤", "青龙", "玉兔", "金鱼", "银狐", "猎鹰",
    "白马", "灵猫", "锦鲤", "翠鸟", "星辰", "明月", "流云", "春风",
    "晴雪", "翡翠", "琥珀", "水晶", "珍珠", "碧玉",
]


def generate_anonymous_map(stock_ids, seed=None):
    """
    生成匿名名称映射（每次重置随机不同）

    命名方式："地名 + 形容词 + 名词"
    示例：S01 → "成都漂亮的蝴蝶", S02 → "杭州勇敢的白鹤"

    保证同一局内没有重复名称（30×30×30 = 27000 种组合，远超 90 只股票）
    """
    rng = random.Random(seed)
    places = list(_ANON_PLACES)
    adjs = list(_ANON_ADJS)
    nouns = list(_ANON_NOUNS)
    rng.shuffle(places)
    rng.shuffle(adjs)
    rng.shuffle(nouns)

    used = set()
    mapping = {}
    for sid in sorted(stock_ids):
        # 生成唯一名称
        for _ in range(1000):
            p = rng.choice(places)
            a = rng.choice(adjs)
            n = rng.choice(nouns)
            name = f"{p}{a}{n}"
            if name not in used:
                used.add(name)
                mapping[sid] = name
                break
        else:
            # 极端情况：fallback 到编号
            mapping[sid] = f"股票{sid}"
    return mapping


# ============================================================
# 3. 指标工具层
# ============================================================
# 财务指标计算 + 好公司判定
# - _roe_historical_avg: ROE 多年均值（周期股和好公司判定用）
# - _get_recent_roe / _get_recent_gm: 数据兜底（当月缺失时用历史）
# - check_10_year_king: 十年王者（巴菲特熊市选股核心）
# - is_good_quality_company: 好公司判定（合理价格买入规则用）

def _roe_historical_avg(sid, year, month, lookback_years=7):
    """返回最近 N 年可见年报的 ROE 平均值（周期股相对高低判定）"""
    reports = get_annual_reports_before(sid, year, month, lookback_years=lookback_years)
    roes = [r.get("roe") for r in reports if r.get("roe") is not None]
    if len(roes) < 3:
        return None
    return sum(roes) / len(roes)


def _get_recent_roe(sid, year, month):
    """
    当月快照 ROE 缺失时的兜底：从最近可见年报取最新有效 ROE
    用于早期数据不完整时，避免因"数据缺失"误杀买入机会
    """
    if not sid or not year or not month:
        return None
    reports = get_annual_reports_before(sid, year, month, lookback_years=3)
    for r in reports:
        roe = r.get("roe")
        if roe is not None:
            return roe
    return None


def _get_recent_gm(sid, year, month):
    """毛利率兜底：取最近可见年报的毛利率"""
    if not sid or not year or not month:
        return None
    reports = get_annual_reports_before(sid, year, month, lookback_years=3)
    for r in reports:
        gm = r.get("gross_margin")
        if gm is not None:
            return gm
    return None


def check_10_year_king(sid, year, month):
    """
    "十年王者"判定（巴菲特/芒格核心理念）

    巴菲特选股的硬规则："连续 10 年 ROE ≥ 15%" 是他筛选优质公司的起点。
    熊市时他从不放宽这个"历史记录"要求，只放宽"当下业绩"要求。

    依据：
    - 巴菲特 1979 年致股东信："判断公司经营好坏的主要依据是净资产收益率"
    - 搜索资料原文："连续5-10年平均ROE低于15%的企业全部排除"
    - 2008年金融危机时买高盛/通用电气/富国银行，都是"危机前连续10年高ROE"

    定义（4 个必要条件，严格防止万科式误判）：
      1. 近 10 年 ROE 均值 ≥ 15%（合格线）
      2. 近 10 年中至少 7 年 ROE ≥ 15%（排除"偶尔冲高"的公司）
      3. 最近 2 年 ROE 没有连续低于 10%（排除"王者已死"的公司）
      4. 最新 1 年 ROE 不为负（排除亏损股）

    返回：(是否王者, 近10年ROE均值, 历史王者年数)
    """
    reports = get_annual_reports_before(sid, year, month, lookback_years=11)
    roes = [r.get("roe") for r in reports if r.get("roe") is not None]

    if len(roes) < 7:  # 至少要 7 年数据才能判定
        return False, None, 0

    recent_10 = roes[:10]  # 最新在前
    avg_10y = sum(recent_10) / len(recent_10)

    # 条件 1：10 年均值 ≥ 15%
    if avg_10y < 15:
        return False, avg_10y, 0

    # 条件 2：10 年中至少 7 年 ROE ≥ 15%
    years_above = sum(1 for r in recent_10 if r >= 15)
    if years_above < 7:
        return False, avg_10y, years_above

    # 条件 3：最近 2 年 ROE 没有连续低于 10%（王者已死排除）
    if len(recent_10) >= 2:
        if recent_10[0] < 10 and recent_10[1] < 10:
            return False, avg_10y, years_above

    # 条件 4：最新 1 年 ROE 不为负
    if recent_10[0] < 0:
        return False, avg_10y, years_above

    return True, avg_10y, years_above


def check_china_v3_rules(sid, year, month, industry):
    """
    中国国情版 v3 规则（回测用，只走纯年报序列规则）
    详见 docs/REQUIREMENTS.md REQ-160~169

    概念对齐（和原模型的 complexity/type 标签对应）：
      - "跑步机型" ≡ 原 complexity=complex（复杂生意：重资产、持续烧钱）
      - "冲浪者型" ≡ 原 type=cycle（周期型生意：业绩随商品/技术周期大幅波动）
      - "过路费生意" ≡ 原 type=utility（公用事业：稳定低收益、特许经营）

    v3 的增量价值：在标签体系之上，基于 5 年 ROE 实证表现做"硬否决/降级/放宽"：
      A. 经营现金流连续 3 年为负（造假信号，所有行业硬否决）
      B. 跑步机实证恶化：complexity=complex + 5 年 ROE 均值 <10% → 硬否决
      C. 冲浪者实证恶化：type=cycle 或 complexity=complex + ROE 波动大/亏损 → 降级
      D. 过路费稳定收益：type=utility + 5 年 ROE 均值 ≥ 8% 且方差 <10pp → 放宽门槛

    注意：
      - type=cycle 已由 CYCLE_STOCKS_ENABLED=False 默认全部过滤，C 主要覆盖
        complexity=complex 但非 cycle 的行业（如半导体/军工/光伏）
      - 不接入回测（需要实时数据）：存贷双高/异常货币资金/商誉/北向资金/ST名称

    返回：
      {
        "hard_reject": bool,      # 硬否决（A/B 触发）
        "downgrade": bool,        # 降级（C 触发）
        "is_toll_bridge": bool,   # 过路费（D 触发，放宽 ROE 门槛）
        "reasons": [str],          # 触发原因列表
      }
    """
    result = {"hard_reject": False, "downgrade": False,
              "is_toll_bridge": False, "reasons": []}

    # A/B 对比开关：关闭时直接返回空结果
    if not CHINA_V3_ENABLED:
        return result

    reports = get_annual_reports_before(sid, year, month, lookback_years=6)
    if len(reports) < 3:
        return result

    roes = [r.get("roe") for r in reports if r.get("roe") is not None]
    ocfs = [r.get("ocf_per_share") for r in reports if r.get("ocf_per_share") is not None]

    # 获取行业标签（复用现有 complexity/type 体系，避免概念重复）
    pe_range = match_industry_pe(industry or "")
    complexity = pe_range.get("complexity", "medium")
    industry_type = pe_range.get("type", "")

    # -------- A. 经营现金流连续为负（造假信号，适用所有行业）--------
    # 康美药业等典型案例。连续 3 年 OCF 为负 → 造假或业务恶化
    if len(ocfs) >= 3 and all(o < 0 for o in ocfs[:3]):
        result["hard_reject"] = True
        result["reasons"].append(
            f"经营现金流连续3年为负（{ocfs[2]:.2f}/{ocfs[1]:.2f}/{ocfs[0]:.2f}元）→ 造假或恶化"
        )
        return result  # 提前返回

    # -------- B. 跑步机实证恶化（原"复杂生意"+ROE 长期偏低）--------
    # 原模型已用 complexity=complex 把 ROE 门槛提到 25/20/15
    # v3 增量：即使 PE 低、护城河过关，只要 5 年 ROE 均值 <10% → 硬否决
    # 典型：京东方A（5年均值 7.7%），中铝/宝钢（均被周期股过滤）
    if complexity == "complex" and len(roes) >= 5:
        avg_roe_5y = sum(roes[:5]) / 5
        if avg_roe_5y < 10:
            result["hard_reject"] = True
            result["reasons"].append(
                f"跑步机型（复杂生意）：{industry}行业 5年ROE均值仅{avg_roe_5y:.1f}%"
                f"（资本黑洞，芒格不碰）"
            )
            return result

    # -------- C. 冲浪者实证恶化（原"周期/复杂生意"+波动大）--------
    # type=cycle 已被 CYCLE_STOCKS_ENABLED 默认过滤（看不到这里）
    # 所以 C 实际主要作用于 complexity=complex 但非 cycle（半导体/军工/光伏/面板等）
    # 三条件任一触发：
    #   1. 5年 ROE 峰谷差 ≥ 15pp（真正的周期波动）
    #   2. 5 年有亏损年（技术/产能失控）
    #   3. 5年 ROE 均值 <10%（技术路线追赶乏力）
    is_surfer_candidate = (industry_type == "cycle") or (complexity == "complex")
    if is_surfer_candidate and len(roes) >= 5:
        max_roe = max(roes[:5])
        min_roe = min(roes[:5])
        avg_roe_5y = sum(roes[:5]) / 5
        has_loss = any(r < 0 for r in roes[:5])
        volatility = max_roe - min_roe
        if volatility >= 15 or has_loss or avg_roe_5y < 10:
            result["downgrade"] = True
            if avg_roe_5y < 10 and volatility < 15 and not has_loss:
                result["reasons"].append(
                    f"冲浪者型（周期/复杂生意）：{industry}行业 5年ROE均值仅{avg_roe_5y:.1f}%"
                    f"（技术追赶乏力）"
                )
            else:
                result["reasons"].append(
                    f"冲浪者型（周期/复杂生意）：{industry}行业 5年ROE波动{volatility:.0f}pp"
                    f"（峰{max_roe:.0f}%/谷{min_roe:.0f}%）"
                )

    # -------- D. 过路费生意（原"公用事业"+稳定 ROE）--------
    # 复用 type=utility 标签（电力/公用事业/交通运输/铁路/铁路公路/高速）
    # 条件：5 年 ROE 均值 ≥ 8% 且 max-min < 10pp（稳定）
    # 触发后放宽 ROE 门槛到 8%（巴菲特思路：稳定收益＋股息>高 ROE）
    if industry_type == "utility" and len(roes) >= 5:
        avg_roe = sum(roes[:5]) / 5
        max_roe = max(roes[:5])
        min_roe = min(roes[:5])
        if avg_roe >= 8 and (max_roe - min_roe) < 10:
            result["is_toll_bridge"] = True
            result["reasons"].append(
                f"过路费生意（公用事业）：{industry}行业 5年ROE均值{avg_roe:.1f}%"
                f"稳定（{min_roe:.1f}%~{max_roe:.1f}%）"
            )

    return result


def is_good_quality_company(sid, year, month,
                             roe_threshold=20.0, gm_threshold=30.0):
    """
    判定是否为"好公司"（用于"合理价格买好公司"规则）
    两种情况下都视为好公司：
      A. 十年王者（近10年ROE均值≥15%+近期未崩塌）—— 巴菲特核心标准
      B. 近5年ROE均值≥20%+毛利率≥30% —— 更严格的"卓越"标准
    任一满足即可触发"合理价格买入"
    """
    if not sid or not year or not month:
        return False
    # 先查十年王者（优先级高）
    is_king, _, _ = check_10_year_king(sid, year, month)
    if is_king:
        return True
    # 再查 5 年卓越标准
    roe_avg = _roe_historical_avg(sid, year, month, lookback_years=5)
    if not roe_avg or roe_avg < roe_threshold:
        return False
    gm = _get_recent_gm(sid, year, month)
    if not gm or gm < gm_threshold:
        return False
    return True


# ============================================================
# 6. 股票评分层
# ============================================================
# 核心决策函数。evaluate_stock 是主入口，根据行业类型分发到
# evaluate_cycle_stock（周期股反向规则，当前默认禁用）或非周期股主流程

def evaluate_cycle_stock(stock_data, sid, year, month, pe_range):
    """
    周期股评分（反向规则，当前默认禁用）
    核心思想：周期股的 ROE 和 PE 都跟随行业周期起伏，高低都是正常的
      - ROE 远高于历史均值 → 周期顶部 → 卖出
      - ROE 远低于历史均值 → 周期底部 → 买入（此时 PE 往往高或负）
      - 亏损反而是最好的买点（黎明前最黑暗）
    评分：周期股风险高，评分上限降低到 25（非周期股上限 50）
         这样排序时周期股会排在简单生意后面
    """
    pe = stock_data.get("pe_ttm")
    roe = stock_data.get("roe")
    price = stock_data.get("price", 0) or 0

    result = {"signal": "hold", "signal_text": "周期股数据不足", "score": 10}
    if price <= 0:
        return {"signal": "delisted", "signal_text": "已停止交易", "score": 0}

    avg_roe = _roe_historical_avg(sid, year, month) if sid and year and month else None

    # 周期股的"顶部"和"底部"都需要 ROE 与 PE 双重确认：
    #   真顶部 = ROE 远高于均值 + PE 偏低（盈利暴增导致估值便宜陷阱）
    #   真底部 = ROE 远低于均值 + PE 偏高或亏损（盈利崩塌导致估值看起来贵）
    # 单独 ROE 高低不足以判断 —— 比如石油公司 ROE 从 2% 涨到 7% 只是常态回归，不是顶部
    if avg_roe is not None and avg_roe > 0 and roe is not None:
        ratio = roe / avg_roe
        pe_low_zone = pe is not None and pe > 0 and pe <= pe_range["fair_low"]
        pe_high_zone = pe is None or pe <= 0 or pe >= pe_range["fair_high"]

        if roe < 0:
            # 亏损：周期最底部（最佳买点）
            result["signal"] = "buy_heavy"
            result["signal_text"] = f"周期股·亏损底部（ROE={roe:.1f}% 历史均值{avg_roe:.1f}%）→ 重仓买入"
            result["score"] = 20
        elif ratio >= 2.0 and pe_low_zone:
            # 真顶部：ROE 达到历史均值的2倍以上 且 PE 偏低
            # 阈值从 1.6 放宽到 2.0：宁可错过顶部最后涨幅，也不过早卖出
            result["signal"] = "sell_heavy"
            result["signal_text"] = (
                f"周期股·顶部（ROE={roe:.1f}% 达均值{avg_roe:.1f}%的{ratio:.1f}倍 + PE={pe:.1f}低）→ 大量卖出"
            )
            result["score"] = 5
        elif ratio >= 1.5 and pe_low_zone:
            result["signal"] = "sell_medium"
            result["signal_text"] = (
                f"周期股·高位（ROE={roe:.1f}%是均值{ratio:.1f}倍+PE={pe:.1f}低）→ 适当卖出"
            )
            result["score"] = 8
        elif ratio < 0.5 and pe_high_zone:
            # 真底部：ROE 远低于均值 且 PE 偏高（或亏损）
            result["signal"] = "buy_heavy"
            result["signal_text"] = (
                f"周期股·底部（ROE={roe:.1f}% 远低于均值{avg_roe:.1f}%）→ 重仓买入"
            )
            result["score"] = 22
        elif ratio < 0.7 and pe_high_zone:
            result["signal"] = "buy_medium"
            result["signal_text"] = (
                f"周期股·低位（ROE={roe:.1f}% 低于均值{avg_roe:.1f}%）→ 中仓买入"
            )
            result["score"] = 18
        elif ratio < 0.9 and pe_high_zone:
            result["signal"] = "buy_light"
            result["signal_text"] = (
                f"周期股·偏低（ROE={roe:.1f}% 略低于均值{avg_roe:.1f}%）→ 轻仓买入"
            )
            result["score"] = 14
        else:
            result["signal"] = "hold"
            result["signal_text"] = (
                f"周期股·中性（ROE={roe:.1f}% 均值{avg_roe:.1f}% PE={pe}）"
            )
            result["score"] = 10
        return result

    # 没有历史 ROE 数据，退而用 PE 反向判断
    if pe is not None and pe > 0:
        if pe > pe_range["high"] * 1.5:
            result["signal"] = "buy_heavy"
            result["signal_text"] = f"周期股·PE={pe:.1f} 极高（可能底部反转）→ 重仓买入"
            result["score"] = 15
        elif pe > pe_range["high"]:
            result["signal"] = "buy_medium"
            result["signal_text"] = f"周期股·PE={pe:.1f} 偏高（可能周期低位）"
            result["score"] = 12
        elif pe < pe_range["low"]:
            result["signal"] = "sell_heavy"
            result["signal_text"] = f"周期股·PE={pe:.1f} 极低（周期顶部）→ 大量卖出"
            result["score"] = 5
        elif pe < pe_range["fair_low"]:
            result["signal"] = "sell_medium"
            result["signal_text"] = f"周期股·PE={pe:.1f} 偏低（周期高位）"
            result["score"] = 8
    elif pe is not None and pe < 0:
        # 亏损状态（PE 为负）
        result["signal"] = "buy_heavy"
        result["signal_text"] = f"周期股·亏损状态（PE={pe:.1f}）→ 重仓买入"
        result["score"] = 18

    return result


def evaluate_stock(stock_data, industry_hint="", sid=None, year=None, month=None):
    """
    评估单只股票，输出信号 + 评分 + 辅助标签
    流程：
      1. 行业定位 → 回购/王者预判
      2. 退市检查 → 周期股分支 → PE 信号主流程
      3. ROE 门槛（十年王者豁免）→ 财务风险 → 合理价格买好公司
      4. 护城河检查 → ROE 下降趋势检查 → 简单评分
    """
    pe = stock_data.get("pe_ttm")
    roe = stock_data.get("roe")
    debt_ratio = stock_data.get("debt_ratio")
    gross_margin = stock_data.get("gross_margin")
    div_yield = stock_data.get("dividend_yield", 0)
    price = stock_data.get("price", 0)

    # 行业定位
    pe_range = match_industry_pe(industry_hint)
    complexity = pe_range.get("complexity", "medium")
    is_cycle = pe_range.get("type") == "cycle"
    high_leverage = pe_range.get("high_leverage", False)

    # 回购加分 + 十年王者预判（两者都依赖历史数据）
    buyback_score, buyback_yi = (0, 0.0)
    is_king_flag = False
    king_avg_roe = None
    if sid and year and month:
        buyback_score, buyback_yi = get_buyback_score(sid, year, month)
        is_king_flag, king_avg_roe, _ = check_10_year_king(sid, year, month)

    # 中国国情版 v3 规则检查（纯年报序列部分）
    china_v3 = {"hard_reject": False, "downgrade": False,
                "is_toll_bridge": False, "reasons": []}
    if sid and year and month:
        china_v3 = check_china_v3_rules(sid, year, month, industry_hint)

    result = {
        "signal": "hold",
        "signal_text": "数据不足",
        "score": 0,
        "complexity": complexity,
        "is_cycle": is_cycle,
        "buyback_score": buyback_score,
        "buyback_yi": buyback_yi,
        "is_10y_king": is_king_flag,
        "king_avg_roe": king_avg_roe,
        "china_v3_reasons": china_v3["reasons"],
        "is_toll_bridge": china_v3["is_toll_bridge"],
    }

    # ---- 退市检查 ----
    if not price or price <= 0:
        result["signal"] = "delisted"
        result["signal_text"] = "该证券已停止交易"
        return result

    # ---- 中国国情版 v3 硬否决（经营现金流连续为负 / 跑步机型）----
    # 比护城河检查更早拦截，避免造假股/资本黑洞进入后续流程
    if china_v3["hard_reject"]:
        result["signal"] = "hold"
        result["signal_text"] = f"中国国情排除：{china_v3['reasons'][0]}"
        result["score"] = 0
        return result

    # ---- 周期股分支 ----
    # 默认禁用（CYCLE_STOCKS_ENABLED=False），直接降级为观望
    # 保留 evaluate_cycle_stock 代码以便未来重启
    if is_cycle:
        if not CYCLE_STOCKS_ENABLED:
            result["signal"] = "hold"
            result["signal_text"] = "周期股·不参与（巴菲特/芒格理念）"
            result["score"] = 0
            return result
        cycle_result = evaluate_cycle_stock(stock_data, sid, year, month, pe_range)
        cycle_result["complexity"] = complexity
        cycle_result["is_cycle"] = True
        if "buy" in cycle_result.get("signal", "") and sid and year and month:
            is_intact, probs = check_moat(sid, year, month)
            if not is_intact:
                cycle_result["signal"] = "hold"
                cycle_result["signal_text"] = f"周期股·{'; '.join(probs[:2])}"
        return cycle_result

    # ---- PE 信号主流程（非周期股）----
    if pe and pe > 0:

        if pe <= pe_range["low"]:
            signal = "buy_heavy"
            signal_text = f"PE={pe:.1f}，远低于行业底部{pe_range['low']}"
        elif pe <= (pe_range["low"] + pe_range["fair_low"]) / 2:
            signal = "buy_medium"
            signal_text = f"PE={pe:.1f}，明显低于合理区间"
        elif pe <= pe_range["fair_low"]:
            signal = "buy_light"
            signal_text = f"PE={pe:.1f}，低于合理区间{pe_range['fair_low']}-{pe_range['fair_high']}"
        elif pe <= pe_range["fair_high"]:
            mid = (pe_range["fair_low"] + pe_range["fair_high"]) / 2
            if pe <= mid * 0.9:
                signal = "buy_watch"
                signal_text = f"PE={pe:.1f}，合理偏低"
            elif pe >= mid * 1.1:
                signal = "sell_watch"
                signal_text = f"PE={pe:.1f}，合理偏高"
            else:
                signal = "hold"
                signal_text = f"PE={pe:.1f}，合理区间"
        elif pe <= (pe_range["fair_high"] + pe_range["high"]) / 2:
            signal = "sell_light"
            signal_text = f"PE={pe:.1f}，偏高"
        elif pe <= pe_range["high"]:
            signal = "sell_medium"
            signal_text = f"PE={pe:.1f}，明显偏高"
        else:
            signal = "sell_heavy"
            signal_text = f"PE={pe:.1f}，远超行业上限{pe_range['high']}"

        result["signal"] = signal
        result["signal_text"] = signal_text

        # ---- 数据兜底：当月快照缺失时用最近年报 ----
        effective_roe = roe if roe is not None else _get_recent_roe(sid, year, month)
        effective_gm = gross_margin if gross_margin is not None else _get_recent_gm(sid, year, month)
        effective_debt = debt_ratio

        # ---- ROE 门槛检查（十年王者 / 过路费豁免）----
        # 非王者按行业复杂度 + 杠杆调整后的阈值降级
        # 王者完全豁免（护城河检查在后面兜底）
        # 过路费生意（铁路/港口/电力/高速/燃气等）：ROE 8% 算合格（巴菲特思路：稳定比高更重要）
        if "buy" in signal and effective_roe is not None and not is_king_flag:
            roe = effective_roe
            base_thresh = COMPLEXITY_ROE_ADJUST.get(complexity, COMPLEXITY_ROE_ADJUST["medium"])
            leverage_adj = 0
            if not high_leverage:
                if debt_ratio and debt_ratio < 30:
                    leverage_adj = -2
                elif debt_ratio and debt_ratio > 50:
                    leverage_adj = 5
            roe_heavy = base_thresh["heavy"] + leverage_adj
            roe_light = base_thresh["light"] + leverage_adj
            roe_watch = base_thresh["watch"] + leverage_adj

            # 过路费豁免：ROE 稳定 ≥ 8% 直接当合格，跳过下面的不达标降级
            if china_v3["is_toll_bridge"] and roe >= 8:
                pass  # 过路费生意放行，不对 ROE 降级
            elif roe < roe_watch:
                result["signal"] = "hold"
                result["signal_text"] += f" 但ROE={roe:.1f}%不达标"
            elif roe < roe_light:
                if signal in ("buy_heavy", "buy_medium", "buy_light"):
                    result["signal"] = "buy_watch"
                    result["signal_text"] += f" (ROE={roe:.1f}%限制)"
            elif roe < roe_heavy:
                if signal in ("buy_heavy", "buy_medium"):
                    result["signal"] = "buy_light"
                    result["signal_text"] += f" (ROE={roe:.1f}%限制)"

        # ---- 财务风险检查 ----
        # 高杠杆行业（银行/保险/券商/地产）豁免负债率和毛利率检查
        if "buy" in result["signal"] and not high_leverage:
            if effective_debt and effective_debt > 70:
                result["signal"] = "hold"
                result["signal_text"] += f" 负债率{effective_debt:.0f}%过高"
            if effective_gm and effective_gm < 15:
                result["signal"] = "hold"
                result["signal_text"] += f" 毛利率{effective_gm:.0f}%过低"

        # ---- 冲浪者降级（周期/复杂生意实证波动大）----
        # 对应原模型 type=cycle 或 complexity=complex 且 ROE 实证波动大
        # 巴菲特/芒格理念：不买自己看不懂的技术迭代、依赖商品周期的行业
        # 宁可错过，不犯错：买信号直接降为 buy_watch 或 hold
        if china_v3["downgrade"] and "buy" in result["signal"]:
            if result["signal"] in ("buy_heavy", "buy_medium"):
                result["signal"] = "buy_watch"
                result["signal_text"] += " 但冲浪者（周期/复杂生意）降级观望"
            elif result["signal"] == "buy_light":
                result["signal"] = "hold"
                result["signal_text"] = china_v3["reasons"][-1]

        # ---- 合理价格买好公司（巴菲特 1989 年致股东信）----
        # 好公司在合理区间内也可以买入，不必等极端低估
        if result["signal"] in ("hold", "buy_watch", "sell_watch") and pe and pe > 0:
            if is_good_quality_company(sid, year, month):
                mid = (pe_range["fair_low"] + pe_range["fair_high"]) / 2
                if pe <= pe_range["fair_low"]:
                    pass  # 已被 PE 主流程处理
                elif pe <= mid:
                    result["signal"] = "buy_light"
                    result["signal_text"] = (
                        f"好公司合理价（PE={pe:.1f} 合理区间偏低）→ 轻仓买入"
                    )
                elif pe <= pe_range["fair_high"]:
                    result["signal"] = "buy_watch"
                    result["signal_text"] = (
                        f"好公司（PE={pe:.1f} 合理区间偏高）→ 关注买入"
                    )

        # ---- 护城河趋势检查（兜底）----
        # 任一规则触发就降级为 hold，无论 PE 多低
        if "buy" in result["signal"] and sid and year and month:
            is_intact, moat_problems = check_moat(sid, year, month)
            if not is_intact:
                result["signal"] = "hold"
                result["signal_text"] = f"护城河松动：{'; '.join(moat_problems[:2])}"

        # ---- ROE 下降趋势检查（防价值陷阱）----
        # 近 3 年 ROE 单调下降且累计跌幅 ≥3pp → 降级
        if "buy" in result["signal"] and sid and year and month:
            reports = get_annual_reports_before(sid, year, month, lookback_years=4)
            roes = [r.get("roe") for r in reports[:3] if r.get("roe") is not None]
            if len(roes) >= 3 and roes[0] < roes[1] < roes[2]:
                drop_pp = roes[2] - roes[0]
                if drop_pp >= 3:
                    old_signal = result["signal"]
                    if old_signal == "buy_heavy":
                        result["signal"] = "buy_light"
                    elif old_signal == "buy_medium":
                        result["signal"] = "buy_light"
                    elif old_signal == "buy_light":
                        result["signal"] = "hold"
                    if old_signal != result["signal"]:
                        result["signal_text"] += (
                            f" 但ROE 3年连降"
                            f"（{roes[2]:.0f}→{roes[1]:.0f}→{roes[0]:.0f}%）降级"
                        )

        # ---- ROE-PB 背离检查（ROE 下降 + PB 不降 = 估值拉升假象）----
        # 健康：高 ROE 支撑高 PB → ROE 降 → PB 也应降
        # 危险：ROE 降但 PB 升 → 市场在炒估值，不是跟业绩
        # 实证：万科 2025（ROE=-21.8%，PB 短暂反弹后暴跌）
        #       格力 2019（ROE 37→33%，PB 2.9→4.1，后续 ROE 继续降到 19%）
        # 用 PE(TTM) × ROE / 100 近似算 PB
        if "buy" in result["signal"] and sid and year and month and pe and pe > 0:
            reports_pb = get_annual_reports_before(sid, year, month, lookback_years=3)
            roes_pb = [r.get("roe") for r in reports_pb[:2] if r.get("roe") is not None]
            if len(roes_pb) >= 2 and roes_pb[0] > 0 and roes_pb[1] > 0:
                # 当前 PB 近似值
                current_pb = pe * roes_pb[0] / 100
                # 上一年 PB 近似值（用上一年 ROE + 当月 PE 的同期估算）
                # 简化：用上一年 ROE 跌幅判断 + 当前 PB 是否"不降"
                roe_drop_pb = roes_pb[1] - roes_pb[0]  # 正值 = ROE 下降
                if roe_drop_pb >= 5 and current_pb > 1.5:
                    # ROE 下降 ≥5pp 但 PB 仍在较高位 → 警告
                    result["signal"] = "hold"
                    result["signal_text"] = (
                        f"ROE-PB背离（ROE从{roes_pb[1]:.1f}%降至{roes_pb[0]:.1f}%"
                        f"但PB≈{current_pb:.1f}偏高，估值拉升可能是假象）"
                    )

    # 简单评分（展示用）
    score = 0
    if roe and roe >= 20: score += 8
    elif roe and roe >= 15: score += 6
    elif roe and roe >= 10: score += 4
    if debt_ratio and debt_ratio < 40: score += 6
    elif debt_ratio and debt_ratio < 55: score += 4
    if gross_margin and gross_margin >= 40: score += 6
    elif gross_margin and gross_margin >= 25: score += 4
    if div_yield and div_yield >= 4: score += 6
    elif div_yield and div_yield >= 2: score += 4
    if pe and pe > 0:
        pe_range = match_industry_pe(industry_hint)
        if pe <= pe_range["fair_low"]: score += 8
        elif pe <= pe_range["fair_high"]: score += 5
    result["score"] = min(score, 50)

    return result


# ============================================================
# 7. 月度信号入口
# ============================================================

def get_month_signals(year, month, anon_map=None, industry_map=None, subset_ids=None):
    """
    获取某月全股票池的模型信号（匿名化输出）
    周期股会被直接过滤（CYCLE_STOCKS_ENABLED=False 时）

    Args:
      subset_ids: 可选的股票 ID 集合（用于多批次不重叠抽样回测）
                  None 表示用全部股票，传入列表/集合时只返回这批
    返回：{匿名编号: {price, pe_ttm, signal, signal_text, score, ...}}
    """
    data = load_month_data(year, month)
    if not data:
        return {}

    events = load_events()
    month_str = f"{year}-{month:02d}"
    stocks = data.get("stocks", {})
    subset_set = set(subset_ids) if subset_ids else None

    if anon_map is None:
        anon_map = {sid: sid for sid in stocks}
    if industry_map is None:
        industry_map = {}

    results = {}
    for sid, sdata in stocks.items():
        # 多批次抽样：不在子集中的直接跳过
        if subset_set and sid not in subset_set:
            continue
        anon_id = anon_map.get(sid, sid)
        # 优先用外部传入的 industry_map，否则回退到 STOCK_INDUSTRY 默认映射
        industry = industry_map.get(sid) or STOCK_INDUSTRY.get(sid, "")

        # 评估信号（含护城河趋势检查）
        eval_result = evaluate_stock(sdata, industry_hint=industry, sid=sid, year=year, month=month)

        # 周期股直接从推荐中过滤（巴菲特/芒格不参与周期股）
        # 不返回任何信号 —— 用户的选股清单里根本不会出现周期股
        if eval_result.get("is_cycle") and not CYCLE_STOCKS_ENABLED:
            continue

        # 获取当月事件
        stock_events = []
        for evt in events.get(sid, []):
            if evt.get("date", "") == month_str:
                stock_events.append(evt)

        # 行业 PE 区间（给回测的"个股 PE 硬否决"规则用）
        pe_range = match_industry_pe(industry) if industry else {}

        results[anon_id] = {
            "sid": sid,  # 内部ID，不暴露给前端
            "price": sdata.get("price", 0),
            "pe_ttm": sdata.get("pe_ttm"),
            "roe": sdata.get("roe"),
            "debt_ratio": sdata.get("debt_ratio"),
            "gross_margin": sdata.get("gross_margin"),
            "dividend_yield": sdata.get("dividend_yield", 0),
            "change_pct": sdata.get("change_pct", 0),
            "signal": eval_result["signal"],
            "signal_text": eval_result["signal_text"],
            "score": eval_result["score"],
            "complexity": eval_result.get("complexity", "medium"),
            "is_cycle": eval_result.get("is_cycle", False),
            "buyback_score": eval_result.get("buyback_score", 0),
            "buyback_yi": eval_result.get("buyback_yi", 0),
            "is_10y_king": eval_result.get("is_10y_king", False),
            "king_avg_roe": eval_result.get("king_avg_roe"),
            "china_v3_reasons": eval_result.get("china_v3_reasons", []),
            "is_toll_bridge": eval_result.get("is_toll_bridge", False),
            "events": stock_events,
            # 行业 PE 区间（供回测 backtest_autorun 的个股 PE 硬否决规则使用）
            "industry": industry,
            "pe_fair_high": pe_range.get("fair_high"),
        }

    return results
