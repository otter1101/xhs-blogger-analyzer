---
name: blogger-distiller
description: >
  Use when the user wants to analyze or distill a Xiaohongshu blogger/account, benchmark a target creator, or diagnose their own content strategy.
  Trigger on requests such as “拆解博主”“蒸馏博主”“分析小红书博主”“诊断我的小红书账号”“对标账号”“内容策略分析”“小红书账号分析”.
---

# 博主蒸馏器

> ⚠️ **使用前必读**：本工具仅供学习研究使用，通过 TikHub 公开 REST API 获取公开数据（不模拟登录、不注入 Cookie）。评论者身份默认脱敏（读者1 / 读者2 / 作者），评论正文保留用于研究。完整条款见 [DISCLAIMER.md](./DISCLAIMER.md) · 安全策略见 [SECURITY.md](./SECURITY.md)。

## 你是什么

自动化的小红书博主蒸馏工具。**输入一个博主名字，输出两样最终产物：**

1. **HTML 蒸馏报告** — 给人看。浏览器打开，快速理解这个博主的人设、认知层、策略层和内容层。
2. **创作 Skill 文件夹** — 给 AI 用。安装后说"用 XX 风格写一篇笔记"，AI 立刻知道怎么写。

模式 A 用来拆解对标博主（学 TA），模式 B 用来诊断自己的账号（看自己）。

核心理念：**脚本保下限，AI 冲上限。** 脚本负责数据采集和确定性分析，AI 负责蒸馏洞察和生成最终产物。

---

## 能力范围

采集目标博主笔记数据（支持 30 / 50 / 80 三档），三层蒸馏产出：

### 三层蒸馏结构

| 层级 | 回答什么 | 举例 |
|------|---------|------|
|  **认知层** | TA 怎么想？ | 核心信念 / 观点张力 / 价值立场 / 思维模式 |
|  **策略层** | TA 怎么运营？ | 系列规划 / 蹭热点方式 / 运营习惯 / 发布节奏 |
|  **内容层** | TA 怎么写？ | 标题公式 / 开头模板 / CTA / 视觉风格 / 标签策略 |

### 产出物一：HTML 蒸馏报告（10 个模块）

1.  一眼看清（摘要卡片）
2. 人设拆解
3. 认知层：TA 怎么想
4. 策略层：TA 怎么运营
5. TOP10 爆款拆解
6. 内容公式速查
7. 选题灵感 TOP15
8. 数据面板（基础展开，详细折叠）
9. 发展趋势（附置信度标注）
10. 核心结论

### 产出物二：创作 Skill 文件夹

- 模式 A：`{博主名}_创作指南.skill/SKILL.md`
- 模式 B：`{用户名}_创作基因.skill/SKILL.md`
- 8 大章节：使用说明 → 认知层 → 策略层 → 内容层 → 创作禁区 → 对比示例 → 选题灵感 → 局限性+自检清单

### 分工

**脚本做 30%**（保下限）：
- 环境检查、TikHub Token 验证、数据采集
- 统计分析（11种标题模式、6类CTA、藏赞比、发布频率）
- 认知层粗提取（观点句候选、思维模式统计、价值词）
- 数据底稿 + AI 蒸馏任务生成

**AI 做 70%**（冲上限）：
- 生成 HTML 蒸馏报告
- 生成创作 Skill 文件夹
- 抽取信念、张力、框架、创作禁区、对比示例
- 因果分析、个性化建议、金句总结

---

## 前置要求

- Python 3.10+（Skill 会自动检测，如未安装会提示）
- TikHub API Token（注册地址: https://user.tikhub.io）
- 网络连接（用于访问 TikHub API: api.tikhub.io）
- **不需要**本地桌面环境，云端/无头服务器也可以运行

### Token 获取与存储

**⚠️ 首次运行时，必须在进入 Phase 0.5 前提醒用户：**

> 本工具需要 TikHub API Token 才能运行。如果你还没有，请按以下步骤操作：
> 1. 访问 https://user.tikhub.io 注册账号
> 2. 充值（按量付费即可）
> 3. **在控制台 → API 权限中，一键勾选全部小红书（xiaohongshu）相关端点**（开得越全，自动容错能力越强）
> 4. 生成 API Token

**密钥存储：** 用户提供 Token 后，系统会自动保存到 `~/.xiaohongshu/tikhub_config.json`，下次运行无需重复输入。Token 三级加载优先级：

1. 环境变量 `TIKHUB_API_TOKEN`
2. 配置文件 `~/.xiaohongshu/tikhub_config.json`（自动保存）
3. 交互式输入（首次使用时引导，输入后自动保存到配置文件）

设置方式（三选一）：
- 环境变量: `set TIKHUB_API_TOKEN=你的token`（Windows）/ `export TIKHUB_API_TOKEN=你的token`（macOS/Linux）
- 配置文件: 首次运行 `check_env.py` 时会交互式引导，自动保存
- 命令行参数: `python run.py "博主名" --token 你的token`

### 代理设置

如需通过代理访问 TikHub API，设置环境变量：

```bash
# Windows
$env:HTTP_PROXY="http://127.0.0.1:7890"
$env:HTTPS_PROXY="http://127.0.0.1:7890"

# macOS/Linux
export HTTP_PROXY="http://127.0.0.1:7890"
export HTTPS_PROXY="http://127.0.0.1:7890"
```

---

## 执行流程

### Phase 0: 环境自动准备

运行 `python scripts/check_env.py`

自动检查并修复以下依赖：

1. **Python 版本** — 检测 Python 3.10+
2. **python-docx** — 检测到未安装时自动 `pip install`
3. **TikHub API Token** — 检测 Token 是否设置且有效
   - 已设置 → 验证连通性，显示额度信息
   - 未设置 → 交互式引导：提示注册 → 输入 Token → **自动保存到 `~/.xiaohongshu/tikhub_config.json`**

> 💡 **额度提示**：每次完整蒸馏约消耗 ¥1～8（取决于笔记数量），可在 https://user.tikhub.io 查看剩余额度。

### Phase 0.5: 前置交互

必须先展示以下完整交互文案，等用户回答后再继续：

```text
─────────────────────────────────────
欢迎使用博主蒸馏器！

请选择分析模式：

   A — 拆解对标博主
     采集 TA 的笔记 → 提炼内容公式和思维方式
     → 生成「TA的名字_创作指南.skill/」
     以后写内容时加载它，相当于随时在线的内容教练

  B — 诊断我的账号
     采集你的笔记 → 找到内容基因和增长瓶颈
     → 生成「你的名字_创作基因.skill/」
     让 AI 写出的内容像你自己写的，无缝嵌入创作工作流

   C — 对标 + 借鉴（暂未开放）

请输入 A 或 B：

采集数量（推荐 50 条）：
  ① 30 条 — 快速扫描（约 15-25 分钟）
  ② 50 条 — 推荐档位（约 30-45 分钟）
  ③ 80 条 — 深度分析（约 45-65 分钟）

每 10 条自动存盘，中断了下次继续。
─────────────────────────────────────
```

记录两个变量供后续流程使用：

- `user_mode`：`A` 或 `B`
- `max_notes`：`30` / `50` / `80`

### Phase 1: 数据采集

运行 `python scripts/crawl_blogger.py <博主名> -o ./data --max-notes <max_notes>`

**⚠️ 重要约束（不得违反）：**
- 必须逐条调用 `fetch_note_detail` 获取笔记正文。仅有标题和互动数字的列表数据不足以做深度分析，正文、评论、标签都只能从 detail 接口获得。
- 不得自行编写脚本替代 `scripts/crawl_blogger.py`，必须调用现有脚本。
- 不得修改 `--max-notes` 参数的值，必须沿用用户在 Phase 0.5 选定的数量。

**⚠️ 端点全部失败时的处理：**
如果采集过程中出现"所有端点均失败"错误（尤其是 HTTP 402/403），**必须立即暂停并提醒用户**：

> ⚠️ 所有 API 端点均返回失败。最常见的原因是 **TikHub 控制台的 API 权限未全部开通**。
> 请登录 https://user.tikhub.io，进入控制台 → API 权限，**一键勾选全部小红书相关端点**，然后重新运行。
> 如果权限已全部开通，请检查账户余额是否充足。

自动完成：

1. **搜索定位博主**（首选 `search_users` 精准匹配 → 兜底 `search_notes` 交叉定位）
2. **获取主页信息** — 粉丝数、获赞数、笔记数、简介（`fetch_user_info`）
3. **获取主页笔记列表** — 分页获取用户全部笔记（`fetch_user_notes`）
4. **多关键词搜索补充** — 默认使用通用后缀（教程 / 推荐 / 分享 / 测评 / 攻略 / 合集），用户可通过 `--keywords` 指定领域词（`search_notes`）
5. **逐条获取笔记详情** — TikHub API 限速自适应，自动调节间隔（`fetch_note_detail`）
6. **checkpoint 断点恢复** — 每 10 条自动存盘

输出文件（JSON）：

- `{博主名}_profile.json` — 主页信息
- `{博主名}_notes_list.json` — 笔记列表（按赞数排序）
- `{博主名}_notes_details.json` — 全量笔记详情（含评论）

### Phase 2: 数据分析 + 认知层提取

运行 `python scripts/analyze.py ./data/<博主名>_notes_details.json -o ./data`

自动完成：

1. **数据清洗** — 解析 JSON，提取标题 / 正文 / 互动数据 / 评论 / 标签
2. **内容分类** — 基于笔记标签和高频关键词动态聚类，不预设任何领域
3. **标签统计** — 提取所有 `#` 话题标签，按频次排序 TOP20
4. **TOP10 + 评论洞察** — 高赞前 10 条的详情 + 热评精选
5. **认知层粗提取** — 观点句候选 / 高频价值词 / 写作结构统计
6. **[可选] 对比分析** — 自己 vs 目标博主的数据差异

输出文件：

- `{博主名}_analysis.json` — 结构化分析数据（含完整笔记列表、分类、观点句候选、高频价值词等）

### Phase 3: 蒸馏 + 产出物生成

#### Step A：生成数据底稿和 AI 蒸馏任务

运行：

```bash
python scripts/deep_analyze.py ./data/<博主名>_analysis.json "<博主名>" \
  -o ./output --details ./data/<博主名>_notes_details.json --mode <user_mode>
```

脚本自动完成：

1. **基础统计面板** — 均赞 / 均藏 / 均评 / 爆款率 / 视频 vs 图文 / 藏赞比
2. **标题模式识别** — 11 种标题策略的使用比例和示例
3. **内容结构分析** — 正文长度分布、列表率、小标题率
4. **CTA 提取**
5. **Emoji 视觉分析**
6. **发布频率**
7. **发展趋势数据**
8. **观点句候选 / 高频价值词 / 写作结构**
9. **TOP10 数据包**
10. **AI 蒸馏任务**

脚本产出：

- `{博主名}_数据底稿.md`
- `{博主名}_AI蒸馏任务.md`

#### Step B：AI 读取蒸馏任务，生成最终产物

AI 必须读取 `AI蒸馏任务.md`，生成以下最终交付物：

1. **HTML 报告**
   - 文件名：`{博主名}_蒸馏报告.html`
   - 技术要求：单文件 HTML，手写 CSS（禁止 Tailwind CDN），Google Fonts 引入 Space Mono + Noto Serif SC
   - 设计风格：Archive Terminal（工业档案感）；底色 #CEC9C0，主强调色 #8A3926，正文 #1A1211
   - 无圆角、无阴影、无白色卡片；模块1/8/10 为砖红色反转背景
   - 三个动效：滚动 fadeInUp / 数字 counter / 分割线 draw-in（原生 JS）
   - 折叠面板用 `<details><summary>` 原生 HTML；响应式，移动端断点 768px
   - 字号系统：标签/元数据层 11-13px，正文内容层 14-16px，统计大数字 20px（详见 AI蒸馏任务.md 字号系统表）
   - 详细视觉规格见 `AI蒸馏任务.md` 的"技术要求"章节

2. **Skill 文件夹**
   - 模式 A：`{博主名}_创作指南.skill/SKILL.md`
   - 模式 B：`{用户名}_创作基因.skill/SKILL.md`

**⚠️ 关键契约：**
- 最终 Skill 不是单个 `.skill.md` 文件
- 最终 Skill 是一个可安装的文件夹
- 文件夹中至少必须有 `SKILL.md`

### Phase 4: 质量检查

运行校验时，最终产物应按以下口径验收：

- `{博主名}_蒸馏报告.html`
- `{博主名}_创作指南.skill/SKILL.md`

模式 B 时，将第二项替换为：

- `{用户名}_创作基因.skill/SKILL.md`

如果最终产物缺失、为空、或 AI 仍输出成单个 `.skill.md` 文件，都视为不合格。

---

## TikHub API 调用协议

使用 HTTP REST API，Bearer Token 认证：

```python
from scripts.utils.tikhub_client import TikHubClient

client = TikHubClient()  # 自动从环境变量/配置文件读取 Token
data = client.search_notes("博主名")
```

### 可用端点

| 方法 | 用途 | 关键参数 |
|------|------|---------|
| `search_users(keyword)` | 搜索用户（精准匹配博主） | `keyword` |
| `search_notes(keyword)` | 搜索笔记 | `keyword`, `page`, `sort` |
| `fetch_user_info(user_id)` | 获取用户主页信息 | `user_id` |
| `fetch_user_notes(user_id)` | 获取用户笔记列表 | `user_id`, `cursor` |
| `fetch_note_detail(note_id)` | 获取笔记详情+评论 | `note_id` |

### TikHub 使用注意

- Token 需在 https://user.tikhub.io 注册获取并充值
- **权限不足（403）**：Token 的 scope 未勾选全部 `xiaohongshu` 相关端点。解决方法：登录 TikHub 控制台 → API 权限，一键勾选全部小红书端点
- **余额不足（402）**：账户余额耗尽。解决方法：登录 TikHub 控制台充值
- **所有端点均失败**：最常见原因是权限未全部开通或余额不足。请优先检查这两项
- 429 限速：客户端内置 RPS 自适应限速（自动检测账户套餐），一般无需手动处理
- 请求间隔由客户端自动管理（基于账户 RPS 限制 × 0.7 安全系数）
- **密钥存储**：用户输入的 Token 会自动保存到 `~/.xiaohongshu/tikhub_config.json`，下次运行自动读取，无需重复输入

---

## 文件结构

```text
blogger-distiller/
├── SKILL.md                  # 你现在看的这个文件
├── run.py                    # 一键运行入口（串联 Phase 0→4）
├── install.py                # 自动安装脚本
├── scripts/
│   ├── check_env.py          # Phase 0: 环境自动准备（TikHub Token 检查）
│   ├── crawl_blogger.py      # Phase 1: 数据采集（TikHub API）
│   ├── analyze.py            # Phase 2: 数据分析 + 认知层粗提取
│   ├── deep_analyze.py       # Phase 3: 数据底稿 + AI 蒸馏任务
│   ├── verify.py             # Phase 4: 数据校验模块
│   └── utils/
│       ├── tikhub_client.py  # TikHub REST API 客户端（限速+多端点降级）
│       ├── endpoint_router.py # 端点池路由 + 自动降级引擎
│       ├── endpoints.json    # 端点池配置（4组×7类 = 28 个端点）
│       ├── adapters.py       # 响应数据归一化适配器
│       ├── common.py         # 共用工具函数
│       └── quality.py        # 数据质量检查工具
└── references/
    └── 产出物质量标杆.md
```

---

## 使用方式

### 自然语言触发（推荐）

直接对 AI 说：

```text
拆解博主 <目标博主名>
```

AI 必须先执行 Phase 0.5 前置交互，再继续后面的流程。

### 一键运行

```bash
cd blogger-distiller/
python run.py "<博主名>"
```

运行后必须先完成：

1. 模式 A / B 选择
2. 数量 30 / 50 / 80 选择

然后再进入采集、分析、蒸馏。

### 手动分步执行

```bash
cd blogger-distiller/

# Phase 0: 环境自动准备（检查 Python + python-docx + TikHub Token）
python scripts/check_env.py

# Phase 1: 采集博主数据
python scripts/crawl_blogger.py "<博主名>" -o ./data --max-notes 50

# Phase 2: 数据分析
python scripts/analyze.py ./data/<博主名>_notes_details.json -o ./data

# Phase 3 Step A: 生成数据底稿和 AI 蒸馏任务
python scripts/deep_analyze.py ./data/<博主名>_analysis.json "<博主名>" \
  -o ./output --details ./data/<博主名>_notes_details.json --mode A
```

**注意：**
- `crawl_blogger.py` 和 `analyze.py` 不要自行改写，直接调用现有脚本。
- `deep_analyze.py` 只负责生成数据底稿和 AI 蒸馏任务；最终 HTML 和 Skill 文件夹由宿主 AI 继续完成。

---

## 多平台兼容性

| 平台 | 本机运行 | HTTP API | Python | 文件读写 | 测试状态 |
|------|---------|----------|--------|---------|---------|
| CodeBuddy (WorkBuddy) | ✅ | ✅ | ✅ | ✅ | ✅ 已验证 |
| Claude Code | ✅ | ✅ | ✅ | ✅ | ✅ 已验证 |
| OpenClaw (本地) | ✅ | ✅ | ✅ | ✅ | 待测试 |
| OpenClaw (云端) | ✅ | ✅ | ✅ | ✅ | 待测试（不再需要桌面环境）|
| Codex | ✅ | ✅ | ✅ | ✅ | ✅ 已验证 |

### 核心原则

1. 一份 `SKILL.md` 兼容 WorkBuddy / Claude Code / OpenClaw / Codex
2. 工具函数提取到 `utils/common.py` 共用
3. 使用标准库（`urllib`）避免外部依赖
4. Token 三级加载（环境变量 → 配置文件 → 交互输入），无需桌面环境

---

## 参考文档

- `references/产出物质量标杆.md` — 可作为产出结构和质量上限参考；若与当前 HTML / Skill 文件夹契约冲突，以本文件和操作手册为准
