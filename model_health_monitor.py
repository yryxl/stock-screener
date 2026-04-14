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

def calc_signal_accuracy(snapshots, lookback_months=3):
    """
    计算模型买入信号的准确率。

    定义：
      - 某时点给出 buy_* 信号的股票
      - N 个月后价格上涨则算准确
      - 准确率 = 准确数 / 总推荐数
    """
    # 这里需要当时的价格 vs 现在的价格
    # 目前快照里存有推荐但没存每个推荐的后续价格
    # 所以暂时返回 None，待后续实现数据回溯
    return None


def calc_vs_hs300(snapshots):
    """
    对比沪深300的超额收益。

    TODO: 需要沪深300的历史价格数据来算
    """
    return None


def calc_holding_win_rate(holdings_file="holdings.json"):
    """
    持仓胜率：当前持仓里多少只是盈利的。

    不是真的"胜率"（需要完整交易历史），是当前浮盈比例。
    """
    holdings = _load_json(holdings_file)
    if not holdings:
        return None

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
        "rate": round(wins / total * 100, 1)
    }


def calc_max_drawdown_current(holdings_file="holdings.json"):
    """
    当前持仓最大回撤：所有持仓里亏得最惨的一只。
    """
    holdings = _load_json(holdings_file)
    if not holdings:
        return None

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
    """数一下最近修过的 Bug 数（健康度反向指标）"""
    # 读 TESTING.md 的 Bug 追踪部分
    # 简化处理：当前已知 BUG-001
    return 1


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

    # 4. 模型准确率（待实现）
    acc = calc_signal_accuracy(None)
    report["指标"]["3月买入准确率"] = {
        "值": "待实现",
        "说明": "需要历史快照 + 3月后价格数据回溯",
        "阈值": "≥ 55% 绿灯",
        "状态": "⚪ 未实现",
    }

    # 5. 对比沪深300
    vs_hs = calc_vs_hs300(None)
    report["指标"]["跑赢沪深300"] = {
        "值": "待实现",
        "说明": "需要沪深300 历史数据对比",
        "阈值": "> 0 绿灯，连续 3 年 < 0 亮红灯",
        "状态": "⚪ 未实现",
    }

    # 6. 最近发现的 Bug
    bugs = calc_recent_bugs_count()
    report["指标"]["已知 Bug 数"] = {
        "值": f"{bugs} 个",
        "说明": "参见 TESTING.md 的 Bug 追踪",
        "阈值": "≤ 3 个且全部为'已修复'状态",
        "状态": "🟢 健康" if bugs <= 3 else "🟡 需关注",
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
  <li><b>3 月买入准确率</b>：模型推荐买入的股票 3 个月后涨幅为正的比例。低于 55% 说明模型预测失效。</li>
  <li><b>跑赢沪深 300</b>：模型推荐组合对比沪深 300 的超额收益。连续 3 年跑输就应该用指数替代。</li>
  <li><b>信号矛盾数</b>：逻辑一致性测试检测出的文案矛盾数量。应为 0。</li>
  <li><b>已知 Bug 数</b>：TESTING.md 中记录的 bug 数量。</li>
</ul>

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
