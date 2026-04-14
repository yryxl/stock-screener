# 🔄 交接流程文档（HANDOVER_PROCESS）

**作用**：换电脑、换 AI 助手、换 GitHub 账号时的**标准操作手册**。照着做不会漏东西。

**维护规则**：每次实际做过一次换机/换 AI，回来更新本文档，记录遇到的坑。

---

## 📑 目录

- [场景 1：换新电脑（同一账号）](#场景-1换新电脑同一账号)
- [场景 2：换新 AI 助手](#场景-2换新-ai-助手)
- [场景 3：换 GitHub 账号](#场景-3换-github-账号)
- [场景 4：全部换新（新电脑+新账号+新AI）](#场景-4全部换新)
- [附录：关键密钥/配置清单](#附录关键密钥配置清单)

---

## 场景 1：换新电脑（同一账号）

### Step 1：准备工作

| 项目 | 新电脑是否需要重装 |
|------|-----|
| Python 3.11+ | ✅ 必装 |
| Git | ✅ 必装 |
| Claude Code / AI 工具 | ✅ 必装 |
| Streamlit | 由 `pip install -r requirements.txt` 带上 |
| akshare | 由 `pip install -r requirements.txt` 带上 |

### Step 2：克隆项目

```bash
cd "G:\Claude Code\ask\"   # 或自定义目录
git clone https://github.com/yryxl/stock-screener.git stock_screener
cd stock_screener
```

### Step 3：恢复 Git 配置

```bash
# 旧电脑上先看下配置
git config --get user.name
git config --get user.email
git config --get credential.helper

# 新电脑上配回去
git config --global user.name "yryxl"
git config --global user.email "你的邮箱"
```

### Step 4：恢复 GitHub Token

**这是最容易忘的一步。** GitHub Token 必须有两个权限：
- ✅ `repo` - 代码推送
- ✅ `workflow` - 修改 `.github/workflows/` 文件（修 daily_screen.yml 等）

**步骤：**
1. 去 https://github.com/settings/tokens
2. 点击 token "stock-screener" 编辑
3. 勾选 `repo` 和 `workflow`
4. 保存（会显示新 token，要立即复制）
5. 新电脑首次 push 时，用户名填 `yryxl`，密码填 token

**详细步骤见 REQ-141 的历史记录**（GitHub 拒绝 workflow 文件 push 时的处理）。

### Step 5：安装依赖

```bash
cd stock_screener
pip install -r requirements.txt
```

### Step 6：验证测试

```bash
# 运行全部测试，确认环境正常
python tests/test_signal_consistency.py

# 预期：Layer 1-3 大部分通过（少数因数据未更新会失败，正常）
```

### Step 7：恢复 Streamlit Cloud 部署

- **前端无需重新部署**：Streamlit Cloud 绑定 GitHub 仓库，只要 main 分支有代码就会自动运行
- **仅需确认**：https://yryxlstock.streamlit.app/ 可访问

### Step 8：恢复微信推送

- **config.yaml 里的 WeChat 参数是秘密**，git 中被 sed 替换为 `YOUR_*` 占位符
- GitHub Actions 里注入真实值：Settings → Secrets → 确认以下 Secret 存在：
  - `WX_APPID`
  - `WX_APPSECRET`
  - `WX_OPENID`
  - `WX_TEMPLATE_ID`
- 本地跑 `main.py` 不走推送（没注入），不影响

---

## 场景 2：换新 AI 助手

### Step 1：新 AI 首次对话必读的文档（按顺序）

```
1. docs/HANDOVER.md              # 项目概述、用户画像、项目结构
2. docs/REQUIREMENTS.md          # 完整需求演进史（本文档）
3. docs/TESTING.md               # 测试框架和已验证约束
4. MODEL_RULES.md                # 规则详细定义
5. docs/HANDOVER_PROCESS.md      # 本文档（了解换人流程）
```

### Step 2：明确告诉新 AI 的关键约定

**必读 3 条**（来自 HANDOVER.md）：
1. **中文汇报**：不说 `buy_heavy`，说"重仓买入"
2. **宁可错过不犯错**：用户的核心原则
3. **ROE 15% 是门槛**：巴菲特铁律

**必读规则**：
- 每次改动前先查 REQUIREMENTS.md 避免重复
- 改完立即更新 REQUIREMENTS.md
- 新增功能必须加对应测试到 TESTING.md

### Step 3：新 AI 的"开局指令"建议

可以把这段直接发给新 AI：

```
我继续之前的股票选股系统项目。请先阅读以下文档了解项目：
1. docs/HANDOVER.md 了解项目性质
2. docs/REQUIREMENTS.md 了解所有历史决策（142+条需求）
3. docs/TESTING.md 了解测试约束
4. MODEL_RULES.md 了解模型规则

关键约定：
- 中文汇报（不用英文代码术语）
- 宁可错过不犯错（这是芒格原则）
- ROE 15% 是买入底线

每次修改必须：
1. 先查 REQUIREMENTS.md 避免重复
2. 修改后更新 REQUIREMENTS.md
3. 在 TESTING.md 添加对应测试
4. 跑测试确认无逻辑矛盾

当前待办：查 REQUIREMENTS.md 的"待办需求"章节
```

### Step 4：验证新 AI 理解到位

给新 AI 出一道题：
```
用户问："云南白药 ROE 10.5% 要不要买？"
```

好答案应该包含：
- 提到 ROE < 15% 未达门槛
- 提到需要查是"恢复中"还是"衰退中"的趋势
- 提到不应向下摊平
- 不应简单照搬"长期持有"

如果新 AI 的回答没有这些层次，它可能没读懂 REQUIREMENTS.md，**重新让它读一遍**。

---

## 场景 3：换 GitHub 账号

### Step 1：备份当前仓库

```bash
# 确保所有改动都已推送
cd stock_screener
git status  # 应该是 clean
git log --oneline -5  # 记下最新 commit
```

### Step 2：在新账号创建仓库

1. 登录新 GitHub 账号
2. 新建 repo `stock-screener`（和旧的同名）
3. **不要**初始化 README（避免首次 push 冲突）

### Step 3：修改远程地址

```bash
# 查当前远程
git remote -v
# 改成新账号的
git remote set-url origin https://github.com/新用户名/stock-screener.git
```

### Step 4：创建新 Token

- 新账号 → Settings → Developer settings → Personal access tokens → Tokens (classic)
- Generate new token
- **勾选 `repo` + `workflow` 两个权限**
- 命名 `stock-screener`
- 保存

### Step 5：首次 push

```bash
git push -u origin main
# 用户名：新账号
# 密码：新 token
```

### Step 6：恢复 GitHub Actions Secrets

Secrets 不能跨仓库自动迁移，需要重新配：

1. 新仓库 → Settings → Secrets and variables → Actions
2. 重新添加：
   - `WX_APPID`
   - `WX_APPSECRET`
   - `WX_OPENID`
   - `WX_TEMPLATE_ID`
3. 值从旧账号的 Secrets 抄过来（旧账号 Secret 页看不到值，需要从原始来源翻出来）

### Step 7：重新部署 Streamlit Cloud

- 访问 https://share.streamlit.io/
- 用新账号登录
- New app → 选新仓库 → 主文件 `app.py`
- 部署完会给一个新 URL（旧 URL 不能保留）

### Step 8：更新 keep_alive.yml

- 文件里的 URL 改为新 Streamlit URL
- 推送到新仓库

### Step 9：收尾

- 把新 Streamlit URL 告诉用户
- 关闭/删除旧仓库（等新的确认可用再删）

---

## 场景 4：全部换新

结合场景 1 + 场景 3 + 场景 2。

**建议顺序：**
1. 先换 GitHub 账号（场景 3）
2. 再换电脑（场景 1 - clone 新账号的仓库）
3. 最后换 AI（场景 2）

**原因**：代码在仓库里是主要资产，先把仓库迁好，其他环境都可以从仓库重建。

---

## 附录：关键密钥/配置清单

### 必须备份的敏感信息

| 项目 | 位置 | 备注 |
|------|------|------|
| GitHub Token | GitHub Settings → Tokens | 有效期内的 token value 备份 |
| 微信公众平台 AppID | 微信测试号管理后台 | `WX_APPID` |
| 微信公众平台 AppSecret | 同上 | `WX_APPSECRET` |
| 微信用户 OpenID | 同上 | `WX_OPENID` |
| 微信消息模板 ID | 同上 | `WX_TEMPLATE_ID` |
| GitHub Actions Secrets | Repo → Settings → Secrets | 同步上面 4 个 |

### config.yaml 模板（重建用）

```yaml
wechat:
  appid: YOUR_APPID             # Actions 会 sed 替换
  appsecret: YOUR_APPSECRET
  openid: YOUR_OPENID
  template_id: YOUR_TEMPLATE_ID

screener:
  max_price_per_share: 500      # 最高股价限制
```

### 项目结构（最小文件清单）

```
stock_screener/
├── app.py                      # Streamlit 主入口
├── screener.py                 # 实时选股模型
├── live_rules.py               # 规则函数
├── backtest_engine.py          # 回测引擎
├── backtest_page.py            # 回测前端
├── etf_monitor.py              # ETF 监测
├── market_temperature.py       # 市场温度计
├── main.py                     # 主流程入口
├── notifier.py                 # 微信推送
├── data_fetcher.py             # 数据获取
├── scorer.py                   # 评分器
├── snapshot.py                 # 快照系统
│
├── holdings.json               # 用户持仓
├── watchlist.json              # 用户关注表
├── etf_index_map.json          # ETF 映射
├── etf_pool_30.json            # ETF 30支池
├── config.yaml                 # 配置（秘密会被替换）
├── requirements.txt            # Python 依赖
│
├── .github/workflows/
│   ├── daily_screen.yml        # 每日选股
│   └── keep_alive.yml          # 防休眠
│
├── docs/
│   ├── REQUIREMENTS.md         # 需求文档（主）
│   ├── TESTING.md              # 测试文档
│   └── HANDOVER_PROCESS.md     # 本文档
│
├── backtest_data/              # 回测历史数据
│   ├── raw_S*.json
│   ├── monthly/
│   └── etf_valuation/
│
├── backtest_games/             # 用户回测游戏存档
├── snapshots/                  # 每周快照
├── tests/
│   └── test_signal_consistency.py
│
├── HANDOVER.md                 # 项目总交接
└── MODEL_RULES.md              # 规则详述
```

### 关键 URL 清单

| 项目 | URL | 用途 |
|------|-----|------|
| Streamlit 前端 | https://yryxlstock.streamlit.app/ | 用户日常访问 |
| GitHub 仓库 | https://github.com/yryxl/stock-screener | 代码托管 |
| GitHub Actions | https://github.com/yryxl/stock-screener/actions | 定时任务 |
| Streamlit Cloud | https://share.streamlit.io/ | 前端部署管理 |
| 微信测试号 | https://mp.weixin.qq.com/debug/cgi-bin/sandbox | 推送调试 |

---

## 紧急问题处理 FAQ

### Q1：GitHub push 被拒绝，提示 workflow scope
- 去 https://github.com/settings/tokens 编辑 token
- 勾选 `workflow` 权限
- 保存后重试 push
- 详见 REQ-141 的讨论历史

### Q2：Actions 跑完数据没推回 GitHub
- 查看 Actions 运行日志的"提交每日结果"步骤
- 如果有错误（通常是冲突），说明之前修复（REQ-133）已被回退
- 重新应用 `da335fc` 的修复

### Q3：Streamlit 前端显示旧数据
- 点"🔄 刷新数据"按钮立即更新（REQ-109）
- 或等 10 分钟自动刷新
- 如果还不行，检查 daily_results.json 在 GitHub 上是否真的更新了

### Q4：前端和微信消息不一致
- 两者读不同数据源：前端读 daily_results.json，微信读 main.py 实时计算
- 确认最新一次 Actions 已完成并推送了 daily_results.json
- 检查 notifier.py 的去重逻辑（REQ-108）

### Q5：信号文案自相矛盾（如"不加仓+可买入"）
- 跑 `python tests/test_signal_consistency.py` 定位问题
- 问题多数在 screener.py 的 signal_text 拼接逻辑
- 参考 REQ-013 修复方案
- 新增的矛盾对补充到 CONTRADICTORY_PAIRS 列表

### Q6：模型把好公司错判为平庸
- 原因：当前只有"十年王者/好公司/平庸"3档，没有"恢复中公司"档
- 这是已知缺陷，REQ-TODO-004 待实施
- 暂时手动分析（参考云南白药案例）

---

## 最后一条提醒

**本文档本身也要维护。**

- 每次换机/换 AI/换账号后：如果遇到新的坑，立即补充到"紧急问题处理 FAQ"
- 每次添加新的密钥/服务：更新"关键密钥配置清单"
- 每次结构大变：更新"项目结构"示意图

**只要本文档保持最新，就算换了 5 台电脑 / 10 个 AI，项目也不会迷路。**
