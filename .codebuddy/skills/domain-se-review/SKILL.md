---
name: domain-se-review
description: 领域SE代码评审技能。当一个领域SE需要对指定Jira单描述中的代码进行评审时使用此技能。触发包括："领域SE评审"、"SE review"、"SE评审jira"、"SE代码评审"、"领域SE review jira"、或任何要求对Jira单中Gerrit链接进行SE评审的场景。此技能完成三件事：1) 对Jira描述中的每个Gerrit链接添加SE评审comment；2) 对commit中有codereview_Report的Gerrit链接自动勾选Confluence checklist；3) 完成后流转Jira单状态。
---

# 领域SE代码评审

## 概述

此技能用于**领域SE（System Engineer）**对指定Jira单中涉及的代码提交进行评审。整个流程自动化完成三个步骤：

1. **提取Gerrit链接** — 从Jira单描述中提取所有Gerrit链接
2. **逐个评审** — 对每个Gerrit链接：
   - 添加SE评审comment（格式：`领域SE_<模块>_代码review_OK_<用户名>`）
   - 检查commit message中是否有`codereview_Report`，如有则自动勾选Confluence代码评审checklist
3. **流转Jira单** — 所有Gerrit链接评审完成后，将Jira单流转为"评审通过"或"评审不通过"

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
export GERRIT_SE_MODULE=your_module      # 如 FRAMEWORK

# Confluence
export CONFLUENCE_KEY=your_api_token

# Jira
export JIRA_URL=https://jira.tclking.com
export JIRA_USER=your_username
export JIRA_PASS=your_jira_password_or_token
```

> **注意**：所有敏感配置（凭证、Token）统一存放在 `~/.gerrit_env`，三个 Skill 共用此文件。

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
- **模块名称**：如 `FRAMEWORK`（可选，从Jira单中推测或用户指定）
- **SE用户名**：执行评审的SE用户名
- **评审结论**：通过 or 不通过（默认通过）

#### 步骤 2：执行评审脚本

调用主脚本：

```bash
python3 scripts/jira_se_review.py \
    --issue <JIRA单号> \
    --module <模块名> \
    --user <SE用户名> \
    [--result pass|fail]
```

脚本将自动完成所有操作并输出详细日志。

#### 步骤 3：检查结果

脚本执行完成后，向用户报告：
- 从Jira描述中提取到多少个Gerrit链接
- 每个链接的评论是否添加成功
- Confluence checklist勾选情况
- Jira单流转结果

---

## 脚本调用说明

### jira_se_review.py

主控脚本，协调Jira、Gerrit、Confluence三个系统的操作。

```bash
# 基础用法：评审通过
python3 scripts/jira_se_review.py \
    --issue ANDROID-12345 \
    --module FRAMEWORK \
    --user ZHANFENGPENG

# 评审不通过
python3 scripts/jira_se_review.py \
    --issue ANDROID-12345 \
    --module FRAMEWORK \
    --user ZHANFENGPENG \
    --result fail

# Dry run（预览不执行）
python3 scripts/jira_se_review.py \
    --issue ANDROID-12345 \
    --module FRAMEWORK \
    --user ZHANFENGPENG \
    --dry-run
```

### 参数说明

| 参数 | 短参数 | 必填 | 说明 |
|------|--------|------|------|
| `--issue` | `-i` | 是 | Jira单号（如 ANDROID-12345） |
| `--module` | `-m` | 是 | 模块名称（如 FRAMEWORK） |
| `--user` | `-u` | 是 | SE用户名（英文大写） |
| `--result` | `-r` | 否 | 评审结果：`pass`（通过，默认）或 `fail`（不通过） |
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

### 2. 对每个Gerrit添加SE评审Comment

脚本内嵌了 Gerrit +2 逻辑，直接向每个 Gerrit Change 进行 Code-Review +2 并添加评审comment。

**评论格式**：
```
领域SE_<模块>_代码review_OK_<用户名>
```

示例：
```
领域SE_FRAMEWORK_代码review_OK_ZHANFENGPENG
```

当评审结论为"不通过"时，评论格式会自动调整为：
```
领域SE_<模块>_代码review_NG_<用户名>
```

### 3. 自动勾选Confluence代码评审Checklist

对于每个Gerrit链接，检查其commit message中是否包含`codereview_Report`字样和对应的Confluence pageId：

- **如果找到**：脚本自动将所有checklist任务标记为完成（`incomplete` → `complete`）
- **如果未找到**：跳过此步骤，在日志中记录

### 4. 流转Jira单状态

所有Gerrit链接处理完毕后，将Jira单流转到目标状态：
- **评审通过**：流转到"已评审"或"评审通过"等对应状态
- **评审不通过**：流转到"评审不通过"或"待修改"等对应状态

流转时会一同添加评论，记录本次评审的摘要信息。

---

## Jira API 说明

### 认证方式

支持两种认证方式（脚本自动检测）：

1. **Basic Auth**：使用 `JIRA_USER` + `JIRA_PASS`
2. **Bearer Token**：优先检测 `JIRA_TOKEN` 环境变量

### 状态流转

脚本会先查询Jira单可用的transition列表，自动匹配目标状态名称：
- `pass` → 匹配包含 "通过"、"已评审"、"Done"、"Resolved" 等关键词的状态
- `fail` → 匹配包含 "不通过"、"Rejected"、"待修改" 等关键词的状态

也可以手动指定 `--jira-transition <状态ID或名称>`。

---

## 目录结构

```
domain-se-review/
├── SKILL.md                    # 本文件（AI skill定义）
└── scripts/
    └── jira_se_review.py       # 主控脚本（完全独立，不依赖其他skill）
```