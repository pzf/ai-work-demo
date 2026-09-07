---
name: jira-fr-assign
description: 根据标题或模块匹配自动分配Owner Jira中的FR任务，分配前先预览报告并征得用户确认。触发词：jira-fr-assign、FR分配、分配FR任务。
---

# Jira FR Task Assignment

## 概述

此 Skill 用于自动分配 Owner  Jira 中的 FR 任务。根据预定义的映射规则（克隆来源匹配优先，标题匹配次之，模块匹配再次），自动将 FR 任务分配给具体的责任人。

## 功能

1. **FR 任务查询** — 查询指定 Owner 名下未解决的 FR 任务
2. **智能匹配** — 克隆来源匹配 > 标题模糊匹配 > 模块精确匹配
3. **预览确认** — 先输出分配报告，征得用户同意后执行
4. **Dry-run 模式** — 只查询不分配，快速验证
5. **分配报告** — 输出详细的分配结果

## 前置条件

环境变量（统一配置在 `~/.gerrit_env`，source ~/.gerrit_env 加载）：

```bash
# Jira
export JIRA_URL=https://jira.tcl.com
export JIRA_USER=your_username
export JIRA_PASS=your_password_or_api_token
```

> **注意**：所有敏感配置（凭证、Token）统一存放在 `~/.gerrit_env`。

## 分配规则配置

分配规则存储在 `config.json` 中：

```json
{
  "title_rules": [
    {"owner": "zhangsan", "patterns": ["Camera", "Imaging"]},
    {"pattern": "Audio", "owner": "lisi"},
    {"pattern": "Bluetooth", "owner": "wangwu"}
  ],
  "module_rules": [
    {"pattern": "Camera", "owner": "zhangsan"},
    {"pattern": "Audio", "owner": "lisi"},
    {"pattern": "Connectivity", "owner": "wangwu"}
  ],
  "clone_source_rules": {
    "GOOGLEGMS": [
      {"owner": "zhangsan", "source_keys": ["GOOGLEGMS-139", "GOOGLEGMS-125"]}
    ],
    "GOOGLEREQ": [
      {"owner": "lisi", "source_keys": ["GOOGLEREQ-84", "GOOGLEREQ-27"]}
    ]
  }
}
```

- **title_rules**：标题模糊匹配。每条规则可含：
  - `pattern`：单个子串匹配（大小写不敏感）
  - `patterns`：该 owner 的多个子串，内部按正则 OR 匹配（等价于多个独立 `pattern`，用于合并减少条目）
  - 注：需求库需求（GOOGLEGMS/GOOGLEREQ）的标题规则已转换为 `clone_source_rules`，`title_rules` 仅保留非需求库来源（TCL AI、运营商定制、Google 应用类等）的兜底
- **module_rules**：模块精确匹配（component 字段）
- **clone_source_rules**：克隆来源匹配。按来源需求库项目分组（顶层 key = 来源 FR 的项目前缀），组内条目支持：
  - `source_keys`：该 owner 的多个来源 FR 精确 key 数组（如 `["GOOGLEGMS-139", ...]`），命中任一即返回该 owner
  - `source_key`：单个来源 FR 精确 key（兼容旧格式）
  - `pattern` / `patterns`：来源 FR 标题的子串匹配（大小写不敏感）
  - 匹配优先级：组内先匹配 `source_keys`/`source_key`（精确），再匹配 `patterns`（标题）
- **匹配优先级**：克隆来源匹配 > 标题匹配 > 模块匹配 > 保持不变
- 合并规则（`patterns` / `source_keys`）仅合并相邻且同 owner 的条目，不改变精确匹配优先的次序，保证分配行为不变

> **克隆来源说明**：项目 FR 通常由需求库 FR（如 `GOOGLEGMS-*`、`GLEXP-*`、`GOOGLEREQ-*`）克隆而来，通过 Jira 标准的 `Cloners` 链接关联。脚本读取当前 FR 的 `issuelinks` 中 `Cloners` 链接的 `outward_issue` 作为来源需求 FR（实测确认，非 `inward_issue`），再按其项目前缀在 `clone_source_rules` 中定位并匹配 owner。

## 使用方式

### 触发条件

当用户请求以下操作时加载此技能：
- "jira-fr-assign zhangsan"
- "FR分配"
- "分配FR任务 zhangsan"

### 执行方式

```bash
# 交互模式：先预览，询问确认后执行
python scripts/jira_fr_assign.py --owner zhangsan

# Dry-run 模式：只预览，不询问不执行
python scripts/jira_fr_assign.py --owner zhangsan --dry-run
```

## 参数说明

| 参数 | 短参数 | 必填 | 说明 |
|------|--------|------|------|
| `--owner` | `-o` | 是 | Jira 用户名（assignee） |
| `--dry-run` | `-n` | 否 | 预览模式，不实际执行分配 |

## JQL 查询条件

```
issuetype = FR
AND assignee = <owner>
AND resolution in (Unresolved, Reopen)
AND status != Accepted
```

## 输出示例

```
============================================================
FR Task Assignment Report (Preview)
Owner: zhangsan
============================================================
Total FRs found: 5
Will assign: 3
Unchanged: 2

[Preview Mode - No changes will be made]

------------------------------------------------------------
Assignments:
------------------------------------------------------------
1. ANDROID-12345 "9.23 Private Compute Services"
   Component: Google XTS | Assign to: zhangsan (matched by clone, source: GOOGLEGMS-139)

2. ANDROID-12346 "Audio Driver Fix"
   Component: Audio | Assign to: lisi (matched by title)

3. ANDROID-12347 "Bluetooth Pairing Issue"
   Component: Bluetooth | Assign to: wangwu (matched by title)

4. ANDROID-12348 "WiFi Stability Problem"
   Component: Connectivity | Assign to: wangwu (matched by module)

5. ANDROID-12349 "Random Issue"
   Component: Other | Assign to: zhangsan (no match)

------------------------------------------------------------
Continue with assignment? [y/N]: y

============================================================
Executing assignment...
[OK] ANDROID-12345 assigned to zhangsan
[OK] ANDROID-12346 assigned to lisi
[OK] ANDROID-12347 assigned to wangwu
[OK] ANDROID-12348 assigned to wangwu
[SKIP] ANDROID-12349 unchanged

Assignment complete. 4 changed, 1 unchanged.
============================================================
```

## 目录结构

```
jira-fr-assign/
├── SKILL.md                    # 本文件（AI skill定义）
├── config.json                 # 分配规则配置
└── scripts/
    └── jira_fr_assign.py       # 主控脚本
```
