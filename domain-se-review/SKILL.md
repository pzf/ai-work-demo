---
name: domain-se-review
description: 领域SE代码评审技能。当一个领域SE需要对指定Jira单描述中的代码进行评审时使用此技能。触发包括："领域SE评审"、"SE review"、"SE评审jira"、"SE代码评审"、"领域SE review jira"、或任何要求对Jira单中Gerrit链接进行SE评审的场景。此技能完成四件事：1) 从Jira描述中提取Gerrit链接（若描述无Gerrit链接但含Confluence汇总页链接，则访问Confluence页提取"未Review列表"中的Gerrit链接）；2) 对每个Gerrit链接执行Code-Review +2（已存在则跳过）；3) 添加SE评审comment（已存在则跳过）；4) 对commit中有codereview_Report的Gerrit链接自动勾选Confluence checklist；5) 完成后流转Jira单状态至"评审通过"。
---

# 领域SE代码评审

## 概述

此技能用于**领域SE（System Engineer）**对指定Jira单中涉及的代码提交进行评审。整个流程自动化完成四个步骤：

1. **提取Gerrit链接** — 从Jira单描述中提取所有Gerrit链接。**若Jira描述中没有直接的Gerrit链接，但包含Confluence汇总页链接，则自动访问该Confluence页并仅提取"未Review列表"中的Gerrit链接**（"已Review列表"已评审过，不重复处理）
2. **逐个评审** — 对每个Gerrit链接：
   - Code-Review +2（**若已存在 +2 则跳过**）
   - 添加SE评审comment（**若已存在相同格式的 comment 则跳过**）
   - 检查commit message中是否有`codereview_Report`，如有则自动勾选Confluence代码评审checklist（**若已全部勾选则跳过**）
3. **流转Jira单** — 所有Gerrit链接评审完成后，将Jira单流转为"评审通过"

---

## 前置条件

环境变量（统一配置在 `~/.gerrit_env`，source ~/.gerrit_env 加载）：

```bash
# Gerrit
export GERRIT_URL=http://sz.gerrit.tclcom.com:8080
export GERRIT_USER=your_username
export GERRIT_PASS=your_http_password
export GERRIT_SITES_CREDENTIALS='{"http://sz.gerrit.tclcom.com:8080": {"user": "...", "pass": "..."}}'

# SE 评审信息
export GERRIT_SE_NAME=your_name          # 如 ZHANFENGPENG

# Confluence
export CONFLUENCE_KEY=your_api_token
# Jira
export JIRA_URL=https://jira.tcl.com
export JIRA_USER=your_username
export JIRA_PASS=your_jira_password_or_token
```

> **注意**：所有敏感配置（凭证、Token）统一存放在 `~/.gerrit_env`，三个 Skill 共用此文件。

---

## config.json

非敏感配置，存放在技能目录下：

```json
{
  "DEFAULT_MODULE": "FRAMEWORK",
  "TARGET_STATUS_ID": "11523"
}
```

- `DEFAULT_MODULE`: 默认模块名，如不指定 `--module` 则使用此值
- `TARGET_STATUS_ID`: 目标Jira状态ID，用于流转到指定状态（如 "11523" 对应"通过"状态）

---

## 工作流程

### 触发条件

当用户请求以下操作时加载此技能：
- "领域SE评审 ANDROID-12345"
- "SE review jira FRAMEWORK-6789"
- "SE评审这个jira单"
- "对Jira单中的代码做SE评审"
- 任何涉及Jira单中Gerrit链接的SE评审请求

### 执行步骤

#### 步骤 1：解析用户输入

从用户请求中提取关键参数：
- **Jira单号**：如 `ANDROID-12345`
- **模块名称**：如 `FRAMEWORK`（`--module` 参数 > `config.json` DEFAULT_MODULE）
- **SE用户名**：执行评审的SE用户名

#### 步骤 2：执行评审脚本

调用主脚本：

```bash
python3 scripts/jira_se_review.py \
    --issue <JIRA单号> \
    --user <SE用户名> \
    [--module <模块名>] \
    [--target-status-id <目标状态ID>] \
    [--dry-run]
```

脚本将自动完成所有操作并输出详细日志。

#### 步骤 3：检查结果

脚本执行完成后，输出评审汇总表格：
- 提取到多少个Gerrit链接（来源：Jira描述 **或** Confluence页"未Review列表"）
- 每个链接的 Code-Review +2 状态（已添加 / 跳过）
- 每个链接的 SE Comment 状态（已添加 / 跳过）
- Confluence checklist 状态（已确认 / 已完成 / 跳过）
- Jira单流转结果

---

## 脚本调用说明

### jira_se_review.py

主控脚本，协调Jira、Gerrit、Confluence三个系统的操作。

```bash
# 基础用法（使用 config.json 中的 DEFAULT_MODULE）
python3 scripts/jira_se_review.py \
    --issue ANDROID-12345 \
    --user ZHANFENGPENG

# 指定模块
python3 scripts/jira_se_review.py \
    --issue ANDROID-12345 \
    --module AUDIO \
    --user ZHANFENGPENG

# Dry run（预览不执行）
python3 scripts/jira_se_review.py \
    --issue ANDROID-12345 \
    --user ZHANFENGPENG \
    --dry-run
```

### 参数说明

| 参数 | 短参数 | 必填 | 说明 |
|------|--------|------|------|
| `--issue` | `-i` | 是 | Jira单号（如 ANDROID-12345） |
| `--user` | `-u` | 是 | SE用户名（英文大写） |
| `--module` | `-m` | 否 | 模块名称（如 FRAMEWORK），不指定则使用 config.json DEFAULT_MODULE |
| `--target-status-id` | `-t` | 否 | 目标Jira状态ID（如 11523 对应"通过"），不指定则使用 config.json TARGET_STATUS_ID |
| `--dry-run` | `-n` | 否 | 预览模式，不实际执行任何操作 |

---

## 详细流程

### 1. 提取Jira描述中的Gerrit链接

从Jira单的`description`字段中用正则匹配Gerrit链接，支持的格式：

- `http://sz.gerrit.tclcom.com:8080/c/PROJECT/+/12345`
- `http://sz.gerrit.tclcom.com:8080/12345`
- `https://sz.gerrit.tclcom.com:8080/c/PROJECT/+/12345`
- 纯数字Change Number（如 `12345`）

提取后得到每个Gerrit的Change ID用于后续操作。

**Confluence 汇总页兜底**：若Jira描述中没有匹配到任何Gerrit链接，但包含Confluence页面链接（格式如 `https://confluence.tclking.com/pages/viewpage.action?pageId=123456`），脚本会自动：

1. 从URL提取 `pageId`
2. 调用Confluence REST API获取页面正文
3. 定位页面中 **"未Review列表"** 区段（`<h2>未Review列表</h2>` 至下一个 `<h2>` 之间的表格），**仅**提取该区段内的Gerrit链接（"已Review列表"中的链接已评审过，不处理）
4. 对提取到的链接执行后续评审流程

> 注意：此功能需要 `CONFLUENCE_KEY` 环境变量配置正确的 Confluence 访问令牌（对应 MCP `mcp-confluence` 的 `X-Confluence-TOKEN`，需有目标空间/页面的访问权限）。

### 2. 对每个Gerrit进行评审

#### 2a. Code-Review +2（去重）

- **检查**：当前是否已有任何人的 +2 投票
- **若已有**：跳过，输出 "skipped(已有+2)"
- **若没有**：执行 +2 投票

#### 2b. SE评审Comment（去重）

**评论格式**：`领域SE_<模块>_代码review_OK_<用户名>`

示例：`领域SE_FRAMEWORK_代码review_OK_ZHANFENGPENG`

- **检查**：当前是否已有相同格式的评论
- **若已有**：跳过，输出 "skipped(已有)"
- **若没有**：添加评论

#### 2c. Confluence Checklist

对于每个Gerrit链接，检查其commit message中是否包含`codereview_Report`字样和对应的Confluence pageId：

- **如果未找到 codereview_Report**：跳过
- **如果找到但页面无可勾选项**：输出 "already_done"
- **如果找到且有待勾选项**：自动勾选所有"检查OK"项

### 3. 流转Jira单状态

所有Gerrit链接处理完毕后：
1. 在Jira单下添加评审摘要评论
2. 将Jira单流转到"评审通过"状态

流转状态匹配关键词："通过"、"已评审"、"Done"、"Resolved"、"评审通过"

---

## Jira API 说明

### 认证方式

支持两种认证方式（脚本自动检测）：

1. **Basic Auth**：使用 `JIRA_USER` + `JIRA_PASS`
2. **Bearer Token**：优先检测 `JIRA_TOKEN` 环境变量

### 状态流转

脚本会先查询Jira单可用的transition列表，自动匹配目标状态名称，关键词包括："通过"、"已评审"、"Done"、"Resolved"、"评审通过"。

---

## 目录结构

```
domain-se-review/
├── SKILL.md                    # 本文件（AI skill定义）
├── config.json                 # 默认配置（DEFAULT_MODULE）
└── scripts/
    └── jira_se_review.py       # 主控脚本（完全独立，不依赖其他skill）
```
