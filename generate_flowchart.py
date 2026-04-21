"""
生成工作流程图PNG + PDF
基于用户 PDF 草图 + 完善版2026-04-21
"""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Polygon
from matplotlib.font_manager import FontProperties
import os

# 中文字体
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False

fig, ax = plt.subplots(figsize=(20, 28))
ax.set_xlim(0, 20)
ax.set_ylim(0, 28)
ax.axis('off')


def box(x, y, w, h, text, color='#E3F2FD', edge='#1976D2', fontsize=10, weight='normal'):
    """矩形流程框"""
    rect = FancyBboxPatch((x - w/2, y - h/2), w, h,
                           boxstyle="round,pad=0.05",
                           edgecolor=edge, facecolor=color, linewidth=1.5)
    ax.add_patch(rect)
    ax.text(x, y, text, ha='center', va='center',
            fontsize=fontsize, fontweight=weight, wrap=True)


def diamond(x, y, w, h, text, color='#FFF3E0', edge='#F57C00', fontsize=10):
    """菱形判定框"""
    poly = Polygon([(x, y + h/2), (x + w/2, y), (x, y - h/2), (x - w/2, y)],
                    closed=True, facecolor=color, edgecolor=edge, linewidth=1.5)
    ax.add_patch(poly)
    ax.text(x, y, text, ha='center', va='center', fontsize=fontsize)


def arrow(x1, y1, x2, y2, label='', color='#444'):
    """箭头"""
    ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                 arrowprops=dict(arrowstyle='->', color=color, lw=1.5))
    if label:
        ax.text((x1 + x2) / 2 + 0.15, (y1 + y2) / 2,
                label, ha='left', fontsize=9, color=color)


def title(x, y, text, fontsize=14, color='#1976D2'):
    ax.text(x, y, text, ha='center', va='center',
            fontsize=fontsize, fontweight='bold', color=color)


# ============== 主标题 ==============
title(10, 27.5, '芒格选股系统 完整工作流程2026-04-21 完善版', fontsize=18, color='#0D47A1')

# ============== 1. 触发段 ==============
box(10, 26.5, 4, 0.7, '[时]每天休市后 (GitHub Actions Cron 触发)', color='#FFE0B2', edge='#E65100', weight='bold')
arrow(10, 26.1, 10, 25.5)

# ============== 2. 休市日校验 ==============
diamond(10, 25.0, 3.5, 1.0, '校验今天\n是否休市日?')
arrow(10, 24.5, 10, 23.7)
ax.text(10.3, 24.1, '否', fontsize=10)

arrow(11.7, 25.0, 18, 25.0, '是', color='#888')
box(18, 25.0, 3, 0.7, '跳过扫描\n保留昨日数据', color='#F5F5F5', edge='#888')

# ============== 3. 6 段全市场扫描 ==============
box(10, 23.0, 6, 1.0,
    ' 分段跑大盘数据 (6 段)\n17/19/21/23/01/03 北京时间\n每段 ~916 只 (尾号 % 6)',
    color='#C8E6C9', edge='#388E3C', weight='bold')
arrow(10, 22.4, 10, 21.8)

# ============== 4. 三个并行记录 ==============
title(10, 21.5, '每段跑完后并行记录', fontsize=11)

box(4, 20.5, 3.5, 0.8, '记录今天\n获取的数据\n→ 成功列表', color='#B3E5FC', edge='#0277BD')
box(10, 20.5, 3.5, 0.8, '记录今天\n遗漏的数据\n→ scan_freshness.json', color='#FFCCBC', edge='#D84315')
box(16, 20.5, 3.5, 0.8, '更新之前\n遗漏数据\n→ 重置 fails=0', color='#D1C4E9', edge='#5E35B1')

arrow(10, 22.4, 4, 20.9)
arrow(10, 22.4, 16, 20.9)

# ============== 5. 段健康判定 ==============
diamond(10, 19.0, 4, 1.2, '段完成率\n< 50% ?')
arrow(10, 20.1, 10, 19.6)

# 否
box(16, 18.0, 3.5, 0.8, ' 段健康\n(完成率 ≥ 98%)', color='#C8E6C9', edge='#388E3C')
arrow(11.5, 18.7, 16, 18.4, '否')

# 黄色
box(16, 17.0, 3.5, 0.8, ' 警告\n(50-98% 漏数入日志)', color='#FFF9C4', edge='#FBC02D')

# 红色
box(4, 18.0, 3.5, 0.8, ' 严重: 警告\n失败股已记录\n等补漏轮处理', color='#FFCDD2', edge='#C62828')
arrow(8.5, 18.7, 4, 18.4, '是')

# ============== 6. 凌晨 4 轮补漏 ==============
arrow(4, 17.5, 4, 16.5)
arrow(16, 17.5, 16, 16.5)
arrow(16, 16.5, 10, 16.0)
arrow(4, 16.5, 10, 16.0)

box(10, 15.5, 8, 1.2,
    ' 凌晨 4 轮补漏 (04/05/06/07 北京)\n跑 fails ≥ 1 的股 (持仓+ETF 优先 / 关注次之 / 候选最后)\n每轮 60 分钟 timeout, 最多 1100 只/轮',
    color='#FFE0B2', edge='#E65100', weight='bold')
arrow(10, 14.9, 10, 14.4)

# 仍有漏跑判定
diamond(10, 13.7, 4, 1.2, '仍有漏跑数据?')

# 是: 重试 3 次后停
box(4, 13.0, 3.5, 1.0,
    '⏸ 重试 3 次后\n标记永久漏跑\n白天 3 轮再补\n(11/14/16)',
    color='#FFCCBC', edge='#D84315')
arrow(8.0, 13.7, 5.7, 13.5, '是')

arrow(10, 13.1, 10, 12.5, '否')

# ============== 7. merge_full + 保存 ==============
box(10, 12.0, 5, 0.8,
    ' merge_full (08:15 北京)\n合并 6 段 + 4-7 轮补漏',
    color='#B3E5FC', edge='#0277BD', weight='bold')
arrow(10, 11.6, 10, 11.0)

box(10, 10.5, 5, 0.7, ' 覆盖并保存 daily_results.json', color='#C8E6C9', edge='#388E3C', weight='bold')
arrow(10, 10.1, 10, 9.6)

# ============== 8. 双保险待补（用户 2026-04-21 简化）==============
box(10, 9.2, 6, 0.8,
    ' 双保险: 上传 GitHub + 本地保存\n→ 校验本次上传/保存是否成功\n[待实现]',
    color='#FFF9C4', edge='#FBC02D')

# ============== 9. 前端 + 微信 ==============
arrow(10, 8.7, 6, 8.0)
arrow(10, 8.7, 14, 8.0)

box(6, 7.5, 4, 0.7, ' 前端 4 个 Tab', color='#E1F5FE', edge='#0288D1', weight='bold')
box(14, 7.5, 4, 0.7, ' 微信推送 (08:55)', color='#E8F5E9', edge='#43A047', weight='bold')

# ============== 10. 4 个 Tab ==============
arrow(6, 7.1, 6, 6.5)
title(10, 6.7, '前端 4 Tab', fontsize=12)

box(2.5, 6.0, 3, 0.6, ' 模型推荐', color='#E3F2FD', edge='#1976D2')
box(6, 6.0, 3, 0.6, ' 持仓管理', color='#E3F2FD', edge='#1976D2')
box(11, 6.0, 4, 0.6, ' 关注表 (4 层)', color='#E3F2FD', edge='#1976D2')
box(15.5, 6.0, 3, 0.6, ' ETF 监测', color='#E3F2FD', edge='#1976D2')

# 关注表 4 层细节
arrow(11, 5.7, 11, 5.3)
title(11, 5.5, '关注表 4 层流转', fontsize=10)

box(11, 4.9, 3.5, 0.6, '模型推荐\n(基本面好+价格不到位)', color='#FFF3E0', edge='#F57C00')
arrow(11, 4.6, 11, 4.2)
ax.text(11.4, 4.4, '[太难]', fontsize=8, color='#666')

box(11, 3.9, 3.5, 0.6, ' 太难表\n(用户标记)', color='#FFCCBC', edge='#D84315')

# 太难表三个出口
arrow(11, 3.6, 7, 3.2)
ax.text(8.8, 3.5, '[好]', fontsize=8, color='#388E3C')
box(7, 2.9, 3, 0.6, ' 我的关注', color='#C8E6C9', edge='#388E3C')

arrow(11, 3.6, 11, 3.2)
ax.text(11.3, 3.5, '[分析中]', fontsize=8, color='#1976D2')
box(11, 2.9, 3, 0.6, ' 置顶', color='#B3E5FC', edge='#0277BD')

arrow(11, 3.6, 15, 3.2)
ax.text(13.0, 3.5, '[坏]', fontsize=8, color='#C62828')
box(15, 2.9, 3.5, 0.6,
    ' 黑名单 1 年\n 数据照跑但不推荐',
    color='#FFCDD2', edge='#C62828')

# 黑名单到期自动恢复
arrow(15, 2.6, 11, 4.7, color='#888')
ax.text(13, 4.0, '1年到期\n自动恢复', fontsize=8, color='#888', ha='center')

# 持仓页 → 周快照
arrow(6, 5.7, 6, 5.3)
title(6, 5.5, '持仓 + 周快照', fontsize=10)

box(6, 4.7, 5.5, 1.2,
    ' 周快照系统（长期归因分析）\n记录: 持股 + 交易明细(时间+类型)\n     + 当时模型推荐 + 归因标签\n     + 错过机会 + 违背操作',
    color='#FFF9C4', edge='#FBC02D')
arrow(6, 4.3, 4, 3.8)
arrow(6, 4.3, 8, 3.8)

box(4, 3.5, 2.5, 0.5, ' 上传 GitHub', color='#C8E6C9', edge='#388E3C')
box(8, 3.5, 2.5, 0.5, ' 本地保存\n[待补]', color='#FFE0B2', edge='#E65100')

arrow(4, 3.2, 6, 2.8)
arrow(8, 3.2, 6, 2.8)

box(6, 2.5, 5, 0.6,
    ' 校验本次上传/保存成功 [待补]',
    color='#FFF9C4', edge='#FBC02D')

arrow(6, 2.2, 6, 1.7)
diamond(6, 1.4, 3.5, 0.7, '样本量充足?')
arrow(7.7, 1.4, 12, 1.4, '是')
box(12, 1.4, 3, 0.6, ' 模型自校验+升级\n[TODO-003 等数据]',
    color='#D1C4E9', edge='#5E35B1')

arrow(4.3, 1.4, 1.5, 1.4, '否')
box(1.5, 1.4, 2.5, 0.6, '⏳ 等待积累', color='#F5F5F5', edge='#888')

# ============== 微信侧 ==============
arrow(14, 7.1, 14, 6.5)
box(14, 6.2, 5, 0.6,
    ' 含 freshness 报警 + 推送结果回执',
    color='#E8F5E9', edge='#43A047')

# ============== 底部图例 ==============
ax.text(10, 0.5, ' = 待实现 /  = 设计完善要点',
        ha='center', fontsize=11, color='#666')
ax.text(10, 0.2, '生成于 2026-04-21基于用户 PDF 草图',
        ha='center', fontsize=9, color='#999')

# 保存为 PNG + PDF
out_dir = os.path.dirname(os.path.abspath(__file__))
png_path = os.path.join(out_dir, '工作流程图_完善版.png')
pdf_path = os.path.join(out_dir, '工作流程图_完善版.pdf')
plt.tight_layout()
plt.savefig(png_path, dpi=120, bbox_inches='tight', facecolor='white')
plt.savefig(pdf_path, format='pdf', bbox_inches='tight', facecolor='white')
print(f'PNG saved: {png_path}')
print(f'PDF saved: {pdf_path}')
