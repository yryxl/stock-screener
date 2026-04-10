"""
回测引擎 - 用历史月度数据跑模型，输出买卖信号
复用 screener.py 的行业PE区间和评估逻辑
"""

import json
import os
import random
import string

from screener import match_industry_pe, COMPLEXITY_ROE_ADJUST

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ===== 策略开关 =====
# 巴菲特/芒格核心理念：不参与周期股
# "时间是优秀企业的朋友，平庸企业的敌人" —— 周期股盈利大幅波动，
# 股价跟随大宗商品/行业周期起伏，不符合"长期持有优质企业"的逻辑
# 设为 False 则所有周期股信号降级为"继续观望"，既不买入也不操作
CYCLE_STOCKS_ENABLED = False

# 30只回测股票的行业映射（手工标注）
# 行业字符串会经 match_industry_pe 模糊匹配到 INDUSTRY_PE 的对应条目
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

# 原始数据缓存（financial_data历史序列，用于护城河趋势检查）
_raw_cache = {}


def load_raw_data(sid):
    """加载某只股票的完整raw数据（含多年财务序列），带缓存"""
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


def check_moat(sid, year, month):
    """
    护城河趋势检查（筛选清单第一关）
    根据股票所属行业自动分发到不同规则：
      - 周期股（cycle）：ROE 本就波动，不用"ROE下滑"判松动，只看"连续亏损/营收暴跌"
      - 非周期股：ROE/毛利率/营收/负债率综合判断
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
    reports = get_annual_reports_before(sid, year, month, lookback_years=5)
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
    # 豁免：跌后 ROE 仍 ≥15% 属于"合格公司"的正常波动
    # 依据：巴菲特 1979 年致股东信及伯克希尔自身 KPI —— 15% 是"合格底线"，20% 是"卓越"
    # 宁可错过原则下，用宽松的 15% 避免误杀合格公司（如五粮液 2014 年跌到 15.4%）
    # 真正恶化的股票（万科跌到 9%）照样会被抓出来
    if len(roe_list) >= 2:
        drop = roe_list[1] - roe_list[0]
        if drop >= 6 and roe_list[0] < 15:
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


def load_month_data(year, month):
    """加载某月的历史快照"""
    path = os.path.join(SCRIPT_DIR, "backtest_data", "monthly", f"{year}-{month:02d}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def load_stock_list():
    """加载股票列表（含真实信息，不暴露给前端）"""
    path = os.path.join(SCRIPT_DIR, "backtest_stocks.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    stocks = {}
    for cat, items in data["categories"].items():
        for item in items:
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


def generate_anonymous_map(stock_ids, seed=None):
    """
    生成匿名编号映射（每次重置随机不同）
    S01 → "K7", S02 → "M3" 等
    """
    if seed is not None:
        random.seed(seed)
    letters = list(string.ascii_uppercase)
    random.shuffle(letters)
    digits = list(range(1, 100))
    random.shuffle(digits)

    mapping = {}
    for i, sid in enumerate(sorted(stock_ids)):
        letter = letters[i % len(letters)]
        digit = digits[i % len(digits)]
        mapping[sid] = f"{letter}{digit:02d}"
    return mapping


def _roe_historical_avg(sid, year, month, lookback_years=7):
    """返回股票最近 N 年可见年报的 ROE 平均值（用于周期股相对高低判定）"""
    reports = get_annual_reports_before(sid, year, month, lookback_years=lookback_years)
    roes = [r.get("roe") for r in reports if r.get("roe") is not None]
    if len(roes) < 3:
        return None
    return sum(roes) / len(roes)


def evaluate_cycle_stock(stock_data, sid, year, month, pe_range):
    """
    周期股评分（反向规则）
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
    用模型逻辑评估单只股票
    输入：某月的股票数据（price, pe_ttm, roe, etc）+ sid/年月（用于护城河趋势检查）
    输出：信号 + 评分 + 复杂度
    """
    pe = stock_data.get("pe_ttm")
    roe = stock_data.get("roe")
    debt_ratio = stock_data.get("debt_ratio")
    gross_margin = stock_data.get("gross_margin")
    div_yield = stock_data.get("dividend_yield", 0)
    price = stock_data.get("price", 0)

    # 先拿到行业 PE 区间
    pe_range = match_industry_pe(industry_hint)
    complexity = pe_range.get("complexity", "medium")
    is_cycle = pe_range.get("type") == "cycle"
    high_leverage = pe_range.get("high_leverage", False)

    result = {
        "signal": "hold",
        "signal_text": "数据不足",
        "score": 0,
        "complexity": complexity,
        "is_cycle": is_cycle,
    }

    # 没有价格的（已退市/未上市）
    if not price or price <= 0:
        result["signal"] = "delisted"
        result["signal_text"] = "该证券已停止交易"
        return result

    # 周期股：按巴菲特/芒格理念直接过滤
    if is_cycle:
        if not CYCLE_STOCKS_ENABLED:
            # 周期股直接降级为观望：既不买入也不触发自动卖出
            result["signal"] = "hold"
            result["signal_text"] = "周期股·不参与（巴菲特/芒格：不投大宗商品周期）"
            result["score"] = 0
            return result
        # === 以下为周期股详细判定（已禁用，保留代码以便未来启用）===
        cycle_result = evaluate_cycle_stock(stock_data, sid, year, month, pe_range)
        cycle_result["complexity"] = complexity
        cycle_result["is_cycle"] = True
        if "buy" in cycle_result.get("signal", "") and sid and year and month:
            is_intact, probs = check_moat(sid, year, month)
            if not is_intact:
                cycle_result["signal"] = "hold"
                cycle_result["signal_text"] = f"周期股·{'; '.join(probs[:2])}"
        return cycle_result

    # PE信号（非周期股逻辑，pe_range / complexity / high_leverage 已在开头准备）
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

        # ROE检查（限制买入信号上限）
        if "buy" in signal and roe is not None:
            base_thresh = COMPLEXITY_ROE_ADJUST.get(complexity, COMPLEXITY_ROE_ADJUST["medium"])
            # 高杠杆行业（银行/保险/券商/地产）不做杠杆惩罚 —— 高负债率是行业常态
            leverage_adj = 0
            if not high_leverage:
                if debt_ratio and debt_ratio < 30:
                    leverage_adj = -2
                elif debt_ratio and debt_ratio > 50:
                    leverage_adj = 5
            roe_heavy = base_thresh["heavy"] + leverage_adj
            roe_light = base_thresh["light"] + leverage_adj
            roe_watch = base_thresh["watch"] + leverage_adj

            if roe < roe_watch:
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

        # 财务风险检查
        # 高杠杆行业豁免"负债率>70%"和"毛利率<15%"两条规则
        # —— 银行/保险负债率天然90%+；银行无毛利率概念；地产薄毛利是常态
        if "buy" in result["signal"] and not high_leverage:
            if debt_ratio and debt_ratio > 70:
                result["signal"] = "hold"
                result["signal_text"] += f" 负债率{debt_ratio:.0f}%过高"
            if gross_margin and gross_margin < 15:
                result["signal"] = "hold"
                result["signal_text"] += f" 毛利率{gross_margin:.0f}%过低"

        # 筛选第一关：护城河趋势检查（基于多年财务序列）
        # 只要趋势显示护城河松动，买入信号一律降级为hold，无论PE多低
        if "buy" in result["signal"] and sid and year and month:
            is_intact, moat_problems = check_moat(sid, year, month)
            if not is_intact:
                result["signal"] = "hold"
                result["signal_text"] = f"护城河松动：{'; '.join(moat_problems[:2])}"

        # 筛选第二关：盈利下降趋势检查（避免"价值陷阱"式买入）
        # 避免"卖对买错"：原因是只看当月PE便宜，没看净资产收益率是否在走下坡
        # 宁可错过一些"真便宜"，也不买"看起来便宜但趋势向下"的公司
        if "buy" in result["signal"] and sid and year and month:
            reports = get_annual_reports_before(sid, year, month, lookback_years=4)
            roes = [r.get("roe") for r in reports[:3] if r.get("roe") is not None]
            if len(roes) >= 3:
                # 近3年ROE是否单调下降
                monotonic_down = roes[0] < roes[1] < roes[2]
                if monotonic_down:
                    drop_pp = roes[2] - roes[0]  # 总跌幅
                    # 下降超过3个百分点就降级
                    if drop_pp >= 3:
                        old_signal = result["signal"]
                        if old_signal == "buy_heavy":
                            result["signal"] = "buy_light"  # 重仓降到轻仓
                        elif old_signal == "buy_medium":
                            result["signal"] = "buy_light"  # 中仓降到轻仓
                        elif old_signal == "buy_light":
                            result["signal"] = "hold"       # 轻仓降到观望
                        if old_signal != result["signal"]:
                            result["signal_text"] += (
                                f" 但ROE 3年连降"
                                f"（{roes[2]:.0f}→{roes[1]:.0f}→{roes[0]:.0f}%）降级"
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


def get_month_signals(year, month, anon_map=None, industry_map=None):
    """
    获取某月所有股票的模型信号（匿名化）
    返回：{匿名编号: {price, pe, signal, signal_text, score, events}}
    """
    data = load_month_data(year, month)
    if not data:
        return {}

    events = load_events()
    month_str = f"{year}-{month:02d}"
    stocks = data.get("stocks", {})

    if anon_map is None:
        anon_map = {sid: sid for sid in stocks}
    if industry_map is None:
        industry_map = {}

    results = {}
    for sid, sdata in stocks.items():
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
            "events": stock_events,
        }

    return results
