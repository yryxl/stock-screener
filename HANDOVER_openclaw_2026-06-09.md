# Handover：OpenClaw 接手记录（2026-06-09）

## 接手机制说明

原 Claude Code 在 `G:\Claude Code\选股\` 下工作，于 2026-04-24 停止。
OpenClaw（当前 AI）于 2026-06-09 接手，工作目录 `C:\Users\Administrator\.openclaw\workspace-dev`。

## 接手后已做的工作

### 1. 全面体检（2026-06-09 17:00-17:30）
发现的核心问题：
- 本地代码落后 GitHub 479 个提交 → 已 `git pull`
- `watchlist_model.json` 为空 → 分期段扫描上线后 auto_add_to_watchlist 未被调用
- patch_round 418 只过期股候选为 0 → ROE预筛与 code_filter 冲突
- ETF 信号全部 sell_heavy 但浮盈<5%，被 pnl_override 压住（设计如此）
- 持仓缺 peak_price/target_price
- user_cash 7 周没更新（¥11,304.25 来自4月20日）

### 2. BUG-045 修复（已推送）
```
screener.py:   当 code_filter 已指定目标集合时跳过 ROE 批量预筛
main.py:       merge_full 阶段调用 auto_add_to_watchlist 填充模型推荐表
.gitignore:    添加日志zip/临时脚本/备份文件模式
backtest_autorun.py: 佣金费率万2.5→千3+最低5元（4月已改未提交）
```

### 3. BUG-046 价值投资强化（已推送）
```
screener.py:   rr10要求回报率变硬门槛（超出安全边际>100%降2级，>50%降1级）
etf_monitor.py: check_market_bubble_alert() 全市场泡沫检查
main.py:       在 _inject_etf_monitor 中集成泡沫警报
app.py:        ETF监测页顶部显示红色/黄色泡沫警报
screener.py:   管理层评分约束（<40 hold，40-59降1级）
```

### 4. BUG-APP-UI 前端修复（已推送）
```
app.py: scan_freshness数据缺失时兜底显示"暂无成功扫描记录"
app.py: 集中度警告从模型推荐页移入持仓管理页
app.py: 关注按钮已有st.success+st.rerun（Streamlit框架限制消息一闪而过）
```

### 5. 云南白药 2025 年报分析（已告知用户）
结论：所有持有条件达标，ROE=13.02%，不加仓不割肉，拿着等2026年报。

## 当前系统状态

### 数据新鲜度
- 最新数据：2026-06-09
- 大盘温度：偏冷（15.8%分位）→ 加仓时机
- ETF 泡沫警报：沪深300/中证500同时进入泡沫区（红色警报）

### 持仓
| 股票 | 成本 | 现价 | 浮盈 | 信号 |
|------|------|------|------|------|
| 沪深300ETF华夏 | ¥4.915 | ¥5.025 | +2.2% | 泡沫区·持有不动 |
| 中证500ETF | ¥8.502 | ¥8.270 | -2.7% | 泡沫区·持有不动 |
| 红利低波ETF | ¥1.221 | ¥1.164 | -4.7% | 偏热·持有不动 |
| 云南白药 | ¥56.40 | ¥49.42 | -12.4% | hold_keep（等ROE回15%）|
| 泸州老窖 | ¥89.95 | ¥84.45 | -6.1% | buy_add（十年王者）|

### 现金：¥5,579.52（建议更新 user_cash.json）

## 尚未修复的问题

### BUG-UI-1 [High]：云南白药成本价不一致
- 持仓卡片显示 holdings.json 的 cost=56.4
- 交易明细区域显示 transaction_log 的加权均价 ¥54.817
- 原因：transaction_log 记录多笔交易后算的均价 vs holdings.json 单一成本
- 修复思路：以 holdings.json 为准，让前端统一读这源

### BUG-UI-2 [Low]：泸州老窖仓位百分比差0.2pp
- 模型推荐页 38.9%（分母=持仓市值）
- 持仓管理页 39.1%（分母=总资产含现金）
- 二者口径不同，加个标注即可

### BUG-UI-6 [Low]：ETF 推荐买不起
- 现金 ¥5,579 时不推万元级 ETF
- 修复：在推荐逻辑加个 `price <= available_cash` 过滤

### BUG-UI-7 [Low]：回测页股票名混淆
- 历史回测页显示"厦门金色的海豚"等混淆名
- 影响：用户不知道推的是哪只股
- 修复：恢复实际股票名称

## 关键联络信息
- GitHub: yryxl/stock-screener（main branch）
- Streamlit: https://yryxlstock.streamlit.app/
- 微信推送：已通过 GitHub Secrets 配置

## 注意事项

### 工作模式
1. 用户说中文，坚持全中文交流（无英文代码术语）
2. 所有操作要留痕，写文件到 `G:\Claude Code\选股\bug-reports\` 或相应目录
3. 核心原则：宁可错过不可犯错
4. 修改代码后提交+推送，确保 GitHub Actions 用到的代码最新

### 项目文件结构
```
G:\Claude Code\选股\
├── stock_screener\          # 主项目代码
│   ├── main.py             # 入口，处理各种 mode
│   ├── screener.py         # 选股引擎
│   ├── app.py              # Streamlit 前端
│   ├── etf_monitor.py      # ETF 估值监测
│   ├── china_adjustments.py # 中国市场特有规则
│   ├── data_fetcher.py     # 数据采集
│   ├── watchlist_manager.py # 关注表4层管理
│   └── .github\workflows\  # 定时任务（每xx分钟跑一次）
├── 年报分析skill\           # AI年报分析技能
├── bug-reports\            # 本 AI 创建的bug报告目录
└── .claude\                # Claude Code 配置（已废弃）
```
