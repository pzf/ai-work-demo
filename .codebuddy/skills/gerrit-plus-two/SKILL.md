---
name: gerrit-plus-two
description: 领域SE对指定Gerrit Change进行Code-Review +2，添加SE评审comment，自动完成Confluence checklist确认，并当Verified +1已就绪时自动Submit。触发词包括："gerrit +2"、"code review +2"、"SE +2"、"加2"、"审核+2"、或任何要求对Gerrit进行Code-Review +2并合并的场景。
---

# Gerrit Code-Review +2（领域SE专用）

## 概述

此技能用于**领域SE**对指定Gerrit Change进行 **Code-Review +2** 操作，并自动完成所有关联动作：
1. **Code-Review +2** — 给予 `Code-Review +2` 标签（独立投票请求）
2. **评审Comment** — 以独立评论请求添加格式为 `领域SE_{MODULE}_代码review_OK_{SE_NAME}` 的SE评审comment
3. **Confluence Checklist确认** — 若commit信息中存在 `codereview_Report`，自动将对应Confluence页面所有checklist项标记为完成
4. **自动Submit** — 若 `Verified +1` 已存在，自动执行Submit合入代码
5. **状态反馈** — 输出本次code review的整体review merge状态

---

## 前置条件

### ~/.gerrit_env（推荐，敏感信息统一存放）

所有敏感配置统一放在 `~/.gerrit_env`，三个 Skill 共用此文件。使用前加载：`source ~/.gerrit_env`

```bash
# Gerrit 认证（必填）
export GERRIT_USER=your_username
export GERRIT_PASS=your_http_password

# 多站点凭证（JSON 格式，优先级高于全局凭证）
export GERRIT_SITES_CREDENTIALS='{"http://sz.gerrit.tclcom.com:8080": {"user": "...", "pass": "..."}, "http://hz.gerrit.tclcom.com:8081": {"user": "...", "pass": "..."}}'

# SE 评审信息
export GERRIT_SE_NAME=ZHANFENGPENG
export GERRIT_SE_MODULE=FRAMEWORK

# Confluence（可选）
export CONFLUENCE_KEY=your_api_token
```

### config.json（可选，存放非敏感默认值）

```json
{
  "GERRIT_URL": "http://sz.gerrit.tclcom.com:8080",
  "GERRIT_SITES": ["http://sz.gerrit.tclcom.com:8080", "http://hz.gerrit.tclcom.com:8081"],
  "GERRIT_SE_NAME": "ZHANFENGPENG",
  "GERRIT_SE_MODULE": "FRAMEWORK"
}
```

> **配置优先级**：命令行参数 > 环境变量 > `~/.gerrit_env` > `config.json`

---

## 触发条件

当用户请求以下操作时加载此技能：
- "给 879887 加 Code-Review +2"
- "SE对 gerrit 879887 进行 +2"
- "对 Iabc123 审核+2 并合入"
- "gerrit +2 12345"
- "领域SE +2 879887"
- 任何涉及对指定Gerrit Change进行Code-Review +2的请求

---

## 工作流程

### 步骤 1：获取 Change 详细信息

查询指定 Gerrit Change 的详细信息，获取当前各标签状态和commit信息：

```bash
python3 scripts/gerrit_plus_two.py --change <change_id>
```

关注以下标签：
- `Code-Review` — 当前评审分数
- `Verified` — 当前验证状态

### 步骤 2：执行 Code-Review +2 并添加 SE 评审 Comment（两次独立请求）

对该 Change 的 current revision 分两次独立请求完成：
1. **Code-Review +2**：单独发送投票请求（仅 `labels`，不带 message）
2. **SE 评审 Comment**：单独发送评论请求（仅 `message`，不带 labels）

这样 Gerrit 上会生成两条独立的评审记录：一条为投票，一条为评论。

Comment格式：`领域SE_{MODULE}_代码review_OK_{SE_NAME}`

示例：
- `领域SE_FRAMEWORK_代码review_OK_ZHANFENGPENG`
- `领域SE_AUDIO_代码review_OK_LISI`

SE姓名和模块名可通过以下方式指定：
1. 环境变量 `GERRIT_SE_NAME` 和 `GERRIT_SE_MODULE`
2. 命令行参数 `--se-name` 和 `--module`

### 步骤 3：自动完成 Confluence Checklist 确认

检查该Gerrit Change的commit信息中是否包含 `codereview_Report`：
- **如果存在**：自动解析Confluence页面ID，将对应页面所有checklist项标记为完成（OK）
- **如果不存在**：跳过此步骤

### 步骤 4：检查 Verified 状态并决定是否 Submit

- **如果 `Verified +1` 已存在**：自动调用 Gerrit Submit API 将 Change 合入
- **如果 `Verified` 未 +1 或不存在**：仅报告 Code-Review +2 已完成，等待 Verified +1 后再合入

### 步骤 5：输出整体 Review Merge 状态

最终向用户报告本次code review的完整状态：
- ✅ **已合并** — Code-Review +2 + SE Comment已添加，Checklist已确认（如有），Verified +1 已存在，Submit 成功
- ⏳ **仅+2未提交** — Code-Review +2 + SE Comment已添加，Checklist已确认（如有），但 Verified 未 +1，等待验证通过后合并
- ❌ **提交失败** — Code-Review +2 成功但 Submit 失败（输出失败原因）

---

## 脚本调用说明

### gerrit_plus_two.py

一站式脚本，完成 Code-Review +2、SE Comment、Confluence Checklist确认、自动Submit 全部操作。

```bash
# 基本用法：对指定 change 进行 +2 并自动提交（使用环境变量中的SE信息）
python3 scripts/gerrit_plus_two.py --change 879887

# 指定SE姓名和模块
python3 scripts/gerrit_plus_two.py --change 879887 --se-name ZHANFENGPENG --module FRAMEWORK

# 指定 revision
python3 scripts/gerrit_plus_two.py --change Iabc123 --revision 3

# 自定义 Gerrit URL
python3 scripts/gerrit_plus_two.py --change 879887 --url http://sz.gerrit.tclcom.com:8080

# 跳过 Confluence checklist 确认
python3 scripts/gerrit_plus_two.py --change 879887 --skip-checklist

# Preview 模式（仅检查状态，不实际执行任何修改操作）
python3 scripts/gerrit_plus_two.py --change 879887 --dry-run
```

### 参数说明

| 参数 | 短参数 | 必填 | 说明 |
|------|--------|------|------|
| `--change` | `-c` | 是 | Gerrit Change ID 或数字编号 |
| `--revision` | `-r` | 否 | Revision / Patch Set 编号（默认 `current`） |
| `--se-name` | | 否 | 领域SE姓名（默认使用 `$GERRIT_SE_NAME`） |
| `--module` | | 否 | 所属领域/模块（默认使用 `$GERRIT_SE_MODULE`） |
| `--url` | `-u` | 否 | Gerrit 基础 URL（默认使用 `$GERRIT_URL`） |
| `--skip-checklist` | | 否 | 跳过 Confluence checklist 确认步骤 |
| `--dry-run` | `-n` | 否 | 预览模式，仅查看状态不执行任何修改操作 |

---

## 目录结构

```
gerrit-plus-two/
├── SKILL.md                      # 本文件（AI skill 定义）
├── config.json                   # 默认配置文件（可选）
└── scripts/
    └── gerrit_plus_two.py        # Code-Review +2 + SE Comment + Checklist确认 + 自动Submit 脚本
```
