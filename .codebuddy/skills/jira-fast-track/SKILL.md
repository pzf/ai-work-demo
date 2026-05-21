---
name: jira-fast-track
description: 快速流转无代码关联的 Jira 单，从当前状态自动推进至 Verified_SW。触发词：jira-fast-track、快速流转、无代码流转。
---

# Jira Fast Track

## 概述

此 Skill 用于快速流转**无代码关联**的 Jira 单（如 support 类工单），从当前状态自动向前推进至 Verified_SW，减少人工操作。

## 工作流程

1. **检测 Gerrit 链接** — 检查 Jira Description 中是否包含 Gerrit 链接
2. **状态路由** — 根据当前状态确定需要的 transition 步骤
3. **字段填充** — 自动填写各 transition 所需的固定字段值
4. **执行流转** — 依次执行状态流转并添加 Comment

---

## 前置条件

环境变量（统一配置在 `~/.gerrit_env`，source ~/.gerrit_env 加载）：

```bash
# Jira
export JIRA_URL=https://jira.tclking.com
export JIRA_USER=your_username
export JIRA_PASS=your_password_or_api_token
```

> **注意**：所有敏感配置（凭证、Token）统一存放在 `~/.gerrit_env`，三个 Skill 共用此文件。

---

## 使用方式

### 触发条件

当用户请求以下操作时加载此技能：
- "jira-fast-track ANDROID-12345"
- "快速流转这个无代码的jira单"
- "流转 jira 单到 Verified_SW"
- 任何涉及无代码 Jira 单快速流转的请求

### 执行方式

#### 单条处理

```bash
python3 scripts/jira_fast_track.py --issue ANDROID-12345
```

#### JQL 批量处理

```bash
python3 scripts/jira_fast_track.py --jql "project=ANDROID AND status in ('In Work', 'Develop')"
```

#### 预览模式（不实际执行）

```bash
python3 scripts/jira_fast_track.py --issue ANDROID-12345 --dry-run
python3 scripts/jira_fast_track.py --jql "..." --dry-run
```

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
Assigned → Accept → Develop → Verified_SW
Accept   → Develop → Verified_SW
Develop  → Verified_SW
Verified_SW → (跳过，已完成)
```

**注意**: 实际流转路径取决于 Jira 项目配置，部分项目可能包含 "In Work" 状态。

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
