---
name: jira-fast-track
description: 快速流转无代码关联的 Jira 单，从当前状态自动推进至 Verified_SW。触发词：jira-fast-track、快速流转、无代码流转。
---

# Jira Fast Track

## 概述

此 Skill 用于快速流转**无代码关联**的 Jira 单（如 support 类工单），从当前状态自动向前推进至最终的 **Delivered** 状态，减少人工操作。流转前，AI 会根据问题单描述与克隆 FR 判断验证方式并先给 Jira 添加评论。

## 工作流程

1. **检测 Gerrit 链接** — 检查 Jira Description 中是否包含 Gerrit 链接
2. **AI 评论（流转前必做）** — AI 读取问题单描述与克隆 FR，判断验证方式，先给 Jira 添加评论
3. **状态路由** — 根据当前状态确定需要的 transition 步骤
4. **字段填充** — 自动填写各 transition 所需的固定字段值
5. **执行流转** — 依次执行状态流转（含 Deliver → Delivered 最终步骤）并添加 Comment

---

## 前置条件

环境变量（统一配置在 `~/.gerrit_env`，source ~/.gerrit_env 加载）：

```bash
# Jira
export JIRA_URL=https://jira.tcl.com
export JIRA_USER=your_username
export JIRA_PASS=your_password_or_api_token
```

> **注意**：所有敏感配置（凭证、Token）统一存放在 `~/.gerrit_env`，三个 Skill 共用此文件。

**AI 评论所需 MCP 工具**（用于流转前添加评论）：
- `mcp__mcp-jira__jira_get_issue` — 读取问题单描述及 `issuelinks`（克隆 FR）
- `mcp__mcp-jira__jira_add_comment` — 添加评论

---

## 使用方式

### 触发条件

当用户请求以下操作时加载此技能：
- "jira-fast-track ANDROID-12345"
- "快速流转这个无代码的jira单"
- "流转 jira 单到 Verified_SW"
- 任何涉及无代码 Jira 单快速流转的请求

### 执行流程（AI 协作 + 脚本流转）

> 本 Skill 为**人机协作**模式：AI 负责读取问题单、生成并添加评论，Python 脚本负责状态流转。

#### 第一步：AI 检查并添加评论（必须）

1. AI 用 `mcp__mcp-jira__jira_get_issue`（fields 含 `issuelinks`）获取问题单描述及其克隆 FR（`issuelinks` 中 `type.name = Cloners` 的 `outward_issue.key`）。
2. AI 判断是否存在明确的验证方式（详见下方「评论生成」）。
3. AI 用 `mcp__mcp-jira__jira_add_comment` 给问题单添加评论。
4. AI 向用户展示评论内容，确认无误后进入流转。

#### 第二步：脚本流转

```bash
python3 scripts/jira_fast_track.py --issue ANDROID-12345
```

#### JQL 批量处理

> 批量模式下 AI 逐个为每个 Issue 添加评论，再统一流转。

```bash
python3 scripts/jira_fast_track.py --jql "project=ANDROID AND status in ('In Work', 'Develop')"
```

#### 预览模式（不实际执行）

```bash
python3 scripts/jira_fast_track.py --issue ANDROID-12345 --dry-run
python3 scripts/jira_fast_track.py --jql "..." --dry-run
```

---

## 评论生成（流转前必做）

在**任何状态流转前**，必须先给 Jira 添加一条评论。评论内容取决于能否从问题单描述或克隆 FR 中找到**明确的验证方式**：

### 情况一：找到明确验证方式
若问题单描述或克隆 FR 中包含可操作的验证说明（如验证步骤、命令行、测试命令、XTS/IR 用例、`adb` 命令、gms 测试等），AI 应**用 AI 总结**出具体的验证说明或命令行验证方法，写入评论。

示例评论：
```
根据 FR 描述，验证方式如下：
[AI 总结的具体验证说明或命令行，例如]
- 确认设备已预载 com.google.android.as.oss（PCS APK）
- 执行命令：adb shell dumpsys package com.google.android.as.oss | grep ...
- 或运行对应 XTS/CTS 用例验证
```

### 情况二：找不到明确验证方式
若问题单描述（及克隆 FR）中**无法**找到明确的验证方法，则添加如下通用评论，说明是通用 FR，通过 XTS IR 验证，可直接走状态：

```
通用FR，通过XTS IR验证，可直接走状态。
```

### 判断要点
- 克隆 FR：通过 `issuelinks` 中 `type.name = Cloners`、`outward_issue.key`（如 `GOOGLEGMS-139`）定位，再读取该 FR 的描述。
- 验证关键词线索：`验证`、`test`、`command`、`adb`、`XTS`、`CTS`、`IR`、`reproduce`、`步骤`、`check` 等。
- 若描述为纯需求说明（如 "MUST preload the X APK"）而无可执行验证命令，通常归为通用 FR。

> **重要**：评论**必须先于**脚本流转完成。若评论添加失败或未获用户确认，不得执行脚本流转。

---

## 参数说明

| 参数 | 短参数 | 必填 | 说明 |
|------|--------|------|------|
| `--issue` | `-i` | 与 --jql 二选一 | Jira 单号（如 ANDROID-12345） |
| `--jql` | `-q` | 与 --issue 二选一 | JQL 查询语句 |
| `--dry-run` | `-n` | 否 | 预览模式，不实际执行任何操作 |

---

## 状态流转

系统从当前状态向前推进，支持以下路径：

```
Assigned → Accept → Develop → Verified_SW → Delivered
Accept   → Develop → Verified_SW → Delivered
Develop  → Verified_SW → Delivered
Verified_SW → Delivered
Delivered → (跳过，已完成)
```

**注意**: 实际流转路径取决于 Jira 项目配置，部分项目可能包含 "In Work" 状态。若当前状态存在 `Deliver` transition，系统会继续流转至最终的 **Delivered** 状态（而非停在 Verified_SW）。若项目不含 `Deliver` transition，脚本会自动跳过该步骤。

### 字段填充

**Assigned → Accept:**
| 字段名称 | Customfield ID | 值 | 格式 |
|----------|----------------|-----|------|
| Ergo Related | customfield_16000 | NO | `{"id": "23807"}` |
| Solution Type | customfield_11584 | TCT ROM Support | `[{"id": "12579"}]` |
| R&D Confirm | customfield_11594 | Yes | `{"id": "12428"}` |
| R&D Comments | customfield_11574 | default support | string |
| SW workload(MD) | customfield_14225 | 0 | number |
| TCT Customized | customfield_11592 | NO | `{"id": "12425"}` |

**Develop → Verified_SW:**
| 字段名称 | Customfield ID | 值 | 格式 |
|----------|----------------|-----|------|
| Team For Checking | customfield_12310 | TCT ROM | `[{"id": "14258"}]` |
| Ergo Related | customfield_16000 | NO | `{"id": "23807"}` |
| Additional DEV Comment | customfield_12697 | Google XTS cover | string |
| R&D自测结果 | customfield_24401 | 无自测条件 | `{"id": "53302"}` |
| COTA Confirm | customfield_25911 | NO | `{"id": "55333"}` |

---

## 输出示例

```
============================================================
Issue: ANDROID-12345
============================================================
当前状态: In Work
将执行 1 个 transition:

- In Work → Verified_SW
  填充字段:
    Team For Checking (customfield_12310) = [{'id': '14258'}]
    Ergo Related (customfield_16000) = {'id': '23807'}
    Additional DEV Comment (customfield_12697) = Google XTS cover
    R&D自测结果 (customfield_24401) = {'id': '53302'}
    COTA Confirm (customfield_25911) = {'id': '55333'}
  [Dry-run] 跳过实际执行

[OK] Issue ANDROID-12345 处理完成

============================================================
处理完成:
  成功: 1
  失败: 0
  总计: 1
============================================================
```

---

## 目录结构

```
jira-fast-track/
├── SKILL.md                    # 本文件（AI skill定义）
├── config.json                 # 默认配置（可选）
└── scripts/
    └── jira_fast_track.py      # 主控脚本
```
