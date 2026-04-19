# 项目交接文档

这是一份**自包含**的交接文档。无论你是**新的 AI 助手**、**换了电脑**、还是**换了账号**，只要读完这一份加上 `MODEL_RULES.md`，就能无缝接手本项目。

---

## 一、项目概述

**项目名称**：A 股选股与回测系统（stock_screener）

**项目目标**：按巴菲特/芒格价值投资理念，构建一个"选股模型 + 回测验证 + ETF 监测"系统。用户需要：
1. **实时选股模型**：每天扫描关注的股票，给出买入/卖出/持有等多档信号
2. **回测模型**：用 25 年历史数据验证选股模型的准确性
3. **ETF 估值监测**：持仓 ETF 的跟踪指数 PE 分位 + 股债利差判定
4. **浮盈三维评估**：防止机械减仓 / 必须割肉 / 牛顶提醒

**项目性质**：这**不是**一个追求高收益的量化交易系统，而是一个**辅助价值投资决策**的工具。信号准确度和"宁可错过不可犯错"才是核心。

---

## 二、用户画像（必读）

**用户角色**：普通散户投资者，不是专业量化交易员。

**沟通偏好**：
- **汇报一律用中文**，所有英文代码术语都必须翻译（`sell_heavy` → 大量卖出，`buy_light` → 轻仓买入 等）
- 即使在修复说明、规则解释、举例里也不能出现英文代码术语
- **已经两次因为这个被用户提醒过**，必须严格遵守

**核心原则**（用户反复强调）：
1. **宁可错过好股票，也不能买错**（芒格原话）
2. **净资产收益率是核心**（巴菲特 1979 年致股东信）
3. **长期持有，买后除非明确信号不动**
4. **宽基 ETF 不是类固收**，仍计入股票仓位

---

## 三、项目结构

```
G:/Claude Code/ask/stock_screener/
├── app.py                   # Streamlit 前端界面（4 个 tab）
├── screener.py              # 实时选股模型（前端用）
├── live_rules.py            # 纯规则函数（从 backtest_engine 镜像）
├── backtest_engine.py       # 回测评估引擎（信号生成+护城河检查）
├── backtest_collector.py    # 历史数据采集脚本（支持 --new-only 增量）
├── backtest_autorun.py      # 回测主程序（买卖逻辑+多档本金+path_c策略）
├── backtest_page.py         # 前端回测页面（互动式历史回测）
├── etf_monitor.py           # ETF 估值监测模块（双通道数据源）
├── market_temperature.py    # 实时市场温度计（沪深300 PE 分位）
├── data_fetcher.py          # 数据获取（含 get_stock_industry 缓存）
├── scorer.py                # 评分器（5维度打分）
├── main.py                  # 主入口（每日 Cron 调用）
├── notifier.py              # 微信推送
├── import_csindex_xls.py    # 中证官网 XLS 估值导入脚本
│
├── backtest_stocks.json     # 股票池定义（90 只）
├── etf_index_map.json       # ETF 代码 → 跟踪指数映射表
├── holdings.json            # 当前真实持仓
├── watchlist.json           # 旧关注列表（已废弃，保留备份；2026-04-19 已迁移到下方 4 表）
├── watchlist_model.json     # 📊 模型推荐（每日扫描自动加：基本面好+价格不到位）
├── watchlist_toohard.json   # 🤔 太难表（用户标记"看不懂"，含 analysis_status）
├── watchlist_my.json        # ⭐ 我的关注（用户精选 + 太难表[好]转入）
├── blacklist.json           # 🚫 黑名单（太难表[坏]转入，1 年自动到期解除）
├── daily_results.json       # 每日扫描结果（含 ETF 信号+浮盈评估）
├── stock_industry_cache.json # 个股行业缓存
│
├── config.yaml              # 配置（微信推送等）
├── MODEL_RULES.md           # 模型完整规则清单
├── HANDOVER.md              # ★ 本文档
│
├── backtest_data/
│   ├── raw_S01.json ~ raw_S90.json   # 90 只股票的原始数据
│   ├── monthly/2001-01.json ~ 2025-12.json  # 月度快照（300 个）
│   └── etf_valuation/                # ETF 跟踪指数估值历史
│       ├── 000300.json               # 沪深300（乐股网全历史 5000+ 条）
│       ├── H30269.json               # 红利低波（反推 1200+ 条）
│       ├── 000015.json               # 上证红利（反推 1200+ 条）
│       └── ...
│
├── backtest_games/          # 用户回测操作记录（自动 push 到 GitHub）
├── backtest_compare_4modes.json  # 4 策略模式对比数据
├── backtest_init_quality.json    # 6 种初始质量对比数据
│
├── run_random_init_compare.py    # 随机半路接管回测脚本
├── run_init_quality_compare.py   # 6 种初始质量对比脚本
│
├── snapshots/               # 每周扫描快照
└── .github/workflows/daily_screen.yml  # GitHub Actions 每日自动运行
```

---

## 四、回测策略模式（path_c，当前默认）

`backtest_autorun.py` 的 `STRATEGY_MODE` 支持 4 种模式：

| 模式 | 规则 | 均值（¥100万） |
|---|---|---|
| baseline | 原版4规则 | +105.0% |
| path_a | 取消牛顶减仓 | +111.9% |
| path_b | 大底加仓+暂停卖出 | +124.6% |
| **path_c** | **A+B 同时启用** | **+133.9%** |

**path_c 两条规则**：
1. **取消"市场极热统一减仓 25%"**：牛市不主动卖出，跟上涨幅
2. **大底加仓**：市场温度=-2（沪深300 PE 历史15%分位以下）时买入预算×2 + 跳过所有 PE 类卖出

**巴菲特 1957 letter 对标**：
> "I would consider a year in which we declined 15% and the Average 30% to be much superior to a year when both we and the Average advanced 20%."

**git tag**：`baseline-2026-04-11` 指向修改前的 commit，可随时回滚对比。

---

## 五、ETF 监测模块

### 数据源双通道
- **宽基**（沪深300/上证50/中证500）：乐股网 `stock_index_pe_lg`，15-21 年全历史
- **策略/行业**（红利低波/上证红利）：中证官网 + 反推法一次性补齐 5 年历史

### 反推法（PE / 收盘价 比值反推）
- 用 indicator.xls（20 条真 PE）+ perf.xlsx（5 年收盘价）
- 验证 PE/close 比值变异系数 < 0.05%，反推误差 < 0.1%
- 反推值标记 `source="csindex_xls_derived"`，akshare 真值优先保留
- 导入脚本：`import_csindex_xls.py`

### 浮盈三维评估（etf_monitor.evaluate_sell_meaningfulness）

| 优先级 | 触发条件 | 动作 |
|---|---|---|
| Level 1 致命 | signal=true_decline/moat_broken | must_sell=True（即使割肉） |
| Level 2 牛顶 | 大盘温度=2 + 浮盈≥10% | bull_top_alert=True |
| Level 3 预警 | 大盘温度=1 + 浮盈≥30% | 准备减仓 |
| Level 4 估值 | 单股 sell_* 信号 | 按浮盈区间判定 |

### ⚠ 重要提醒
- **宽基 ETF 不是类固收**（写死在代码注释+前端 warning 里）
- 2008 年标普500跌37%、2015 年沪深300半年跌43%
- `stock_zh_a_spot_em` **不返回行业字段**，行业必须用 `get_stock_industry`

---

## 六、已知 bug 修复记录（重要经验）

### latest-first 序列方向
年报数据是 latest-first 排序。"连续下滑"的正确条件是 `values[0] < values[1] < values[2]`（最新<次新<最老）。写反会把"回升"误判为"下滑"。已在 `check_fundamental_health` 规则 6 和 `check_moat_live` 规则 3 修复过。

### 个股行业获取
`ak.stock_zh_a_spot_em` 不返回"所属行业"字段。必须用 `data_fetcher.get_stock_industry(code)` → `ak.stock_individual_info_em`，带本地持久化缓存 `stock_industry_cache.json`。

### 持仓加仓门槛
buy_add 信号必须过 `is_king`（十年王者）或 `is_good_quality_live`（5年ROE≥20%+毛利≥30%）门槛。不能只看 PE 低就给加仓建议。巴菲特："Don't add to the average. Add only to winners."

### Streamlit HTML 渲染
`st.markdown` 里不能用 `f"""多行HTML"""`（4格缩进会被 Markdown 当成代码块）。必须用 `f''` 单行字符串拼接。

### Streamlit widget key 覆盖 session_state
`st.number_input(key="xxx")` 在 rerun 时会用 widget 旧值覆盖 session_state。回测页的前进/后退/播放按钮因此全部失效过。解决：改成纯展示 + 独立按钮，不用 widget 绑定 session_state。

---

## 七、GitHub Actions 自动化

### 每日 Cron 时间表（UTC）
- 18:30 / 01:00 / 04:00 / 09:00 / 10:00

### 自动执行的任务
1. 按时段判断运行模式（full/send_ai/holdings/watchlist）
2. 运行 `main.py --mode xxx --force`
3. curl ping Streamlit 防止休眠
4. 提交 daily_results.json / watchlist.json / snapshots/ / backtest_games/ 到 GitHub

### Token 权限限制
Personal Access Token 没有 `workflow` scope，无法通过 git push 修改 `.github/workflows/` 目录。修改 workflow 文件必须在 GitHub 网页上操作。

---

## 八、前端结构（app.py 4 个 tab）

| Tab | 功能 |
|---|---|
| 模型推荐 | 全市场扫描结果（每周一自动更新） |
| 持仓管理 | 6只持仓 + 组合分类四色卡片 + 浮盈评估 + 三类提醒 |
| 关注表（4 层流转） | 📊 模型推荐 / 🤔 太难表 / ⭐ 我的关注 / 🚫 黑名单（TODO-047）<br>用户操作按钮：[太难][好][坏][分析中][取消]，黑名单 1 年自动到期 |
| ETF 监测 | 5只ETF的指数PE/分位/温度/浮盈/买卖信号 |

### 回测页（backtest_page.py）
- 互动式历史回测：前进/后退/播放/暂停
- 虚拟买入/卖出 + ⭐加入关注表
- 💾下载到本地 / ☁️保存到云端（backtest_games/）

---

## 八点五、AI Skill（独立备份目录）

**重要**：项目用了一个 Claude Code skill（news_screen — AI 舆情筛股 v2），
为了便于换电脑/换账号/换 AI 时迁移，**额外维护了一份独立备份**：

```
G:/Claude Code/ask/选股skill/
├── HANDOVER.md              ← 接手指南（新 AI 必读）
├── README.md                ← skill 详细说明 + 同步规则
└── news_screen/SKILL.md     ← 6 层搜索 + 5 防幻觉
```

**修改 skill 必须双向同步**：
- 项目内：`stock_screener/.claude/skills/news_screen/SKILL.md`（Claude Code 实际加载）
- 外部备份：`G:/Claude Code/ask/选股skill/news_screen/SKILL.md`

**触发话术**（在 Claude Code 里说类似话即触发）：
- "帮我用 AI 筛 600519、000858"
- "AI 浑水式查 X 数据真实性"
- "X 公司管理层怎么样"

详见 `选股skill/HANDOVER.md` 完整接手指南。

---

## 九、Claude 记忆系统

记忆文件在 `C:\Users\Administrator\.claude\projects\G--Claude-Code-ask\memory\`：

| 文件 | 内容 |
|---|---|
| feedback_chinese_only.md | 禁用英文代码术语 |
| project_principles.md | 宁可错过不犯错、ROE 15%合格20%卓越 |
| project_etf_module.md | ETF 监测双通道数据源 |
| feedback_etf_risk.md | 宽基ETF不是类固收 |
| project_stock_industry_source.md | 行业字段必须用 stock_individual_info_em |
| feedback_latest_first_direction.md | latest-first "连续下滑" 是 [0]<[1]<[2] |
| feedback_only_winners_add.md | buy_add 必须过 is_king 或 is_good_quality |
| handover_location.md | HANDOVER.md 位置 |

**如果换了 AI/账号**：记忆不会跟过来，但本 HANDOVER.md 已包含所有必要信息。

---

## 十、如何运行

### 采集全部 90 只股票的历史数据
```bash
python backtest_collector.py           # 全量（约 30 分钟）
python backtest_collector.py --new-only # 增量（只拉缺失的）
```

### 跑回测
```bash
python backtest_autorun.py                    # 默认 3 个随机起点
python backtest_autorun.py --suite 10         # 10 个随机起点
python backtest_autorun.py 2019 11            # 指定起点
python run_init_quality_compare.py            # 6 种初始质量对比
python run_random_init_compare.py             # 随机半路接管对比
```

### 导入 ETF 历史估值
```bash
# 1. 从中证官网下载 indicator.xls + perf.xlsx
# 2. 放到 backtest_data/etf_valuation_import/
python import_csindex_xls.py
```

### 启动前端
```bash
streamlit run app.py
```

### 每日运行（GitHub Actions 自动）
```bash
python main.py --mode all --force
```

---

## 十一、工作流程（AI 必读）

### 接到新任务时
1. 先看 HANDOVER.md 和 MODEL_RULES.md
2. 读相关代码（不要凭猜测改）
3. 小步改动，每次改后跑回测验证
4. 汇报时用中文，不用英文代码术语
5. 让用户决定方向，不擅自扩大范围

### 4 档本金
固定为 1万 / 10万 / 50万 / 100万（用户明确要求，不要改）

### 禁止做的事
- ❌ 汇报时用英文代码术语
- ❌ 擅自改默认本金数额
- ❌ 擅自删除股票池的股票
- ❌ 修改规则时不验证就提交
- ❌ 绕过护城河检查的硬门槛
- ❌ 把宽基 ETF 从权益仓位里剔除

---

## 十二、git 历史重要节点

| 提交 | 内容 |
|---|---|
| baseline-2026-04-11 (tag) | 精简版 D（4规则）作为 baseline |
| a0ac533 | path_c 转正为默认策略 |
| 4320375 | 浮盈三维防线 + ETF 实时价格 |
| 924c864 | 策略 ETF 历史估值反推补齐 |
| 7183af3 | 修复持仓行业错判（get_stock_industry） |
| 8999488 | 修复 ROE 方向反的 bug |
| 434269d | 修复加仓信号误判（加入 is_king 门槛） |
| c485411 | 回测模型精简修正版 + 股池扩到 90 只 |
| 2dd3463 | 同步规则到正式版（live_rules.py） |
| 220d0f4 | 温度计升级：5维度综合判定 |

---

## 结语

这个项目的核心不是技术，而是**用代码实现巴菲特/芒格的投资智慧**。每一条规则都可以追溯到巴菲特原话或芒格的观点。当你改动规则时，要问自己：

> "巴菲特会这么做吗？芒格会怎么说？"

如果答案模糊，宁可保守（宁可错过不可犯错）。

**用户的回测操作记录存在 `backtest_games/` 目录**。新 AI 接手后可以读取这些记录，用巴菲特/芒格视角分析用户的操作进步方向。

**祝你接手顺利。**
