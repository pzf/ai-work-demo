---
name: jira-team-risk-analysis
description: 分析总结团队当前Jira任务的问题分布及风险情况。基于三个可配置JQL
  filter（待解决问题/Block问题/待解决FR），统计人员问题占比，识别Block风险，结合用户输入的近期重点项目节点信息做风险预警。触发词：团队风险分析、问题分布分析、风险总结、team
  risk analysis、任务风险分析、Jira风险分析、团队任务风险。
disable-model-invocation: false
---

# Jira Team Risk Analysis

## 概述

此 Skill 用于分析团队当前 Jira 任务的问题分布及风险情况。基于三个可配置的 Jira Filter 获取数据，结合用户输入的近期重点项目节点信息，生成风险分析报告。报告涵盖三个维度：问题分布分析（待解决问题单的人员占比）、Block 分布分析（被 Block 问题的人员分布）、近期重点项目风险预警（结合项目节点推进 FR/Defect 处理）。同时支持参考 `jira-team-weekly-summary` 的静态 Confluence 输出方式，基于三个 filter 生成三个矩阵图表，并整合成一个可直接替换 Confluence 页面“进行中（项目问题分布情况）”表格的静态 HTML/storage 表格。

## 前置条件

- MCP 工具：`mcp__mcp-jira__jira_search`（Jira 搜索）
- `config.json` 中配置三个 Filter ID 及团队人员列表

## 使用方式

### 触发条件

当用户输入包含以下关键词时触发：
- `团队风险分析`、`问题分布分析`、`风险总结`
- `team risk analysis`、`任务风险分析`
- `Jira风险分析`、`团队任务风险`

### 参数说明

| 参数 | 短参数 | 必填 | 默认值 | 说明 |
|------|--------|------|--------|------|
| `--focus-projects` | `-p` | 否 | - | 近期需重点关注的项目节点信息，格式：`项目名:截止日期:说明`，多个项目用逗号分隔。例如：`PROJ-A:2026-08-15:版本发布,PROJ-B:2026-08-20:验收节点` |
| `--output` | `-o` | 否 | `./reports/` | 报告输出目录 |

### Filter 配置

| Filter | 用途 |
|--------|------|
| pending_issues | 团队当前所有待解决的 Jira 问题单（Defect/Bug 类型，非终态） |
| blocked_issues | 团队当前被标记为 Block 状态的问题单 |
| pending_fr | 团队当前所有待解决的 FR 需求单 |

### 团队人员配置

在 `config.json` 的 `team_members` 数组中配置团队成员 username 列表。此配置用于识别 Filter 结果中属于团队内部的人员，过滤掉非团队成员。

## 执行步骤

### Step 1: 查询三个 Filter 数据

使用 `mcp__mcp-jira__jira_search`，`limit=200`，`fields="summary,assignee,status,project,issuetype,priority,created,updated"` 依次查询三个 filter：

```
jql = "filter = <pending_issues_filter_id>"   # 待解决问题
jql = "filter = <blocked_issues_filter_id>"   # Block 问题
jql = "filter = <pending_fr_filter_id>"       # 待解决 FR
```

从每个 filter 结果中提取：
- **总数（total）**：用于报告各维度整体数量
- **issue 列表**：包含 key、summary、assignee、status、project、issuetype、priority、created、updated

### Step 2: 问题分布分析

**注意：filter 结果可能超过单页限制，必须逐人查询获取精确计数。**

对所有团队成员，**逐人**用 `limit=1` 查询 JQL，取返回的 `total` 作为该成员精确待解决问题数：
```
jql = "assignee = zihang.gao AND filter = 37706"
jql = "assignee = yihuachen AND filter = 37706"
// ... 逐人查询全体团队成员
```

每人查询 `maxResults=1`，从 API 返回的 `total` 字段获取精确数量。然后：
1. **计算占比**：计算每个成员的问题数占团队总待解决问题的百分比
2. **识别高负载人员**：当某成员占比超过团队平均占比的 1.5 倍时，标记为"需关注"
3. **识别超负载人员**：当某成员占比超过团队平均占比的 2 倍时，标记为"需支持"
4. **输出格式**：按问题数降序排列，显示姓名、问题数、状态标记

### Step 3: Block 分布分析（基于 blocked_issues filter）

基于第二个 filter 的结果进行分析：

1. **按 assignee 统计**：统计每个团队成员持有的 Block 问题数量
2. **识别风险人员**：持有 Block 问题的人员，标记 Block 数量和详情
3. **分析 Block 集中度**：判断 Block 问题是否集中在个别人员或项目
4. **输出格式**：显示姓名、Block 数、涉及的 issue key 列表

### Step 4: FR 分布分析

**注意：filter 37708 结果量可能很大（100+），不能直接从 filter 分页结果中统计人员分布，必须逐人单独查询。**

对所有团队成员，**逐人**用 `limit=1` 查询 JQL，取返回的 `total` 作为该成员精确 FR 数量：
```
jql = "assignee = zihang.gao AND filter = 37708"
jql = "assignee = yihuachen AND filter = 37708"
jql = "assignee = siyu.zhang AND filter = 37708"
jql = "assignee = chuntian.ben AND filter = 37708"
jql = "assignee = hailongwang AND filter = 37708"
jql = "assignee = ex_jiawei.liu AND filter = 37708"
jql = "assignee = zhanfengpeng AND filter = 37708"
jql = "assignee = yi-chen AND filter = 37708"
jql = "assignee = forong.li AND filter = 37708"
jql = "assignee = zhongwen.nong AND filter = 37708"
```

每人查询 `maxResults=1`，从 API 返回的 `total` 字段获取精确数量。FR 较多阈值：>=3 个，提醒及时流转。

### Step 5: 近期重点项目风险分析（基于 pending_fr filter + 用户输入）

如果用户通过 `--focus-projects` 提供了近期重点项目节点信息：

1. **筛选相关 FR**：在 pending_fr 结果中，筛选出项目名匹配用户指定项目的 FR
2. **筛选相关 Defect**：在 pending_issues 结果中，筛选出项目名匹配用户指定项目的 Defect
3. **按人员统计**：统计每个相关人员持有的相关 FR 和 Defect 数量
4. **风险提示**：对每个相关项目，列出相关人员及其持有的 FR/Defect，提示需在节点前推进处理
5. **输出格式**：按项目分组，显示项目名、节点日期、节点说明、相关人员及任务列表

### Step 6: 生成风险分析报告和静态图表

汇总以上分析结果，按以下紧凑纯文字格式输出到 `reports/风险分析_YYYY-MM-DD.md`，可直接复制到 Confluence。

**参考模板**：严格参照 `框架开发二组 Week 42`（Page ID: 769721375）中"进行中（项目问题分布情况）"的 Confluence storage 格式。该模板位于工作区 `source_w42.html`，是经过验证的正确输出格式。

同时生成 Confluence storage 静态表格，默认输出到 `reports/风险分析_静态图表_YYYY-MM-DD.html`。

**静态表格由脚本直接调用 Jira API 生成，不依赖手工 JSON 中转**：

```bash
python scripts/generate_confluence_risk_static.py --output reports/风险分析_静态图表_YYYY-MM-DD.html
```

脚本内部会：
1. 从 `config.json` 读取三个 filter ID 与 `jira_url`；
2. 从环境变量 `CODEBUDDY_MCP_CONFIG` 读取 connector-proxy 的认证凭证；
3. 通过 connector-proxy 的 MCP 端点（`tools/call` 调用 `mcp-jira_jira_search`）分页拉取三个 filter 的全部 issue；
4. 复用脚本内置的矩阵/分布构建逻辑生成 6 列静态表格并写入 HTML 文件。

该表格包含 6 列：

| 列 | 内容 |
|---|---|
| 总览 | 文字风险总结 |
| Defect 分布情况 | 第一个 filter：Y 轴经办人，X 轴状态 |
| Block 分布情况 | 第二个 filter：Y 轴经办人，X 轴项目 |
| Defect 项目分布情况 | 第一个 filter：Y 轴项目，X 轴数量、占比 |
| FR 分布情况 | 第三个 filter：Y 轴经办人，X 轴状态 |
| FR 项目分布情况 | 第三个 filter：Y 轴项目，X 轴数量、占比 |

#### W42 参考模板规范（进行中表格）

外层 6 列表格结构与"已完成"表格相同，但列定义和 `<colgroup>` 略有不同：

```html
<table class="relative-table wrapped" style="width: 100.0%;">
<colgroup><col style="width: 18.0%;"/><col style="width: 17.0%;"/><col style="width: 16.0%;"/><col style="width: 17.0%;"/><col style="width: 16.0%;"/><col style="width: 16.0%;"/></colgroup>
<tbody>
<tr><th>总览</th><th>Defect 分布情况</th><th>Block 分布情况</th><th>Defect 项目分布情况</th><th>FR 分布情况</th><th>FR 项目分布情况</th></tr>
<tr>
  <td><!-- 总览文本 --></td>
  <td><!-- 内嵌子表格：Defect 分布 --></td>
  <td><!-- 内嵌子表格：Block 分布 --></td>
  <td><!-- 内嵌子表格：Defect 项目分布 --></td>
  <td><!-- 内嵌子表格：FR 分布 --></td>
  <td><!-- 内嵌子表格：FR 项目分布 --></td>
</tr>
</tbody></table>
```

##### 总览列文本格式（W42 标准）

```html
<p><span style="color: rgb(0,51,102);">一、待解决问题 N 个。人员分布：user1(N)、user2(N)。</span><br/>
<span style="color: rgb(0,51,102);">二、Block 问题 N 个。人员分布：user1(N)、user2(N)。</span><br/>
<span style="color: rgb(0,51,102);">三、待解决 FR N 个。人员分布：user1(N)、user2(N)。</span></p>
```

**🚨 总览列完整性检查（发布前强制执行）**：
1. 每个维度的「人员分布」必须有具体数据
2. 禁止输出 `人员分布：。` 等空结果
3. 如果某维度总数 > 0，但人员分布为空，必须停止发布并补充查询
4. 发布前必须用 Python 脚本检查 HTML 不包含空数据

##### 内嵌子表格格式（与已完成表格一致）

```html
<table class="wrapped relative-table" data-mce-resize="false" style="width: 100.0%;font-size: 12.0px;">
<tbody>
<!-- 表头行 + 数据行 + 合计行 -->
</tbody></table>
```

- 字体大小固定为 `font-size: 12.0px`
- 空单元格使用 `<br/>`
- 数字单元格必须是 Jira JQL 超链接
- 合计行使用 `<th>` 加粗

图表规则：
- 统一使用静态 HTML table，不使用 `jirachart`、Chart.js、iframe 或页面加载时动态请求。
- 表格数字必须是 Jira JQL 超链接。
- Defect 分布情况的列使用 `status.name`（如 Assigned、Opened、已关闭），与 W42 一致。
- Defect 分布情况的链接格式：`filter=<filter_id> AND assignee=<user> AND status=<状态值>`
- Block 分布情况的链接格式：`filter=<filter_id> AND assignee=<user> AND project=<project_key>`；合计列使用 `filter=<filter_id> AND assignee=<user>`；合计行使用 `filter=<filter_id> AND project=<project_key>`
- Defect 项目分布情况列固定为：项目、数量、占比；项目名和数量必须是 Jira JQL 超链接，链接格式：`filter=<pending_issues_filter_id> AND project=<project_key>`，总数行使用 `filter=<pending_issues_filter_id>`
- FR 分布情况列使用 `status.name`，链接格式：`filter=<pending_fr_filter_id> AND assignee=<user> AND status=<状态值>`
- FR 项目分布情况列固定为：项目、数量、占比；链接格式：`filter=<pending_fr_filter_id> AND project=<project_key>`，总数行使用 `filter=<pending_fr_filter_id>`

```
## 团队风险分析（YYYY-MM-DD）

一、问题分布
待解决 N 个。人员分布：user1(N)、user2(N)、...。需关注：user1(超人均 1.5 倍)、user2(超人均 2 倍)。
二、Block 问题
Block N 个，涉及 X 人。user1(N): ISSUE-1, ISSUE-2; user2(N): ISSUE-3。需关注 P0/High: ISSUE-1(P0), ISSUE-2(High)。
三、FR 分布
待解决 FR N 个。FR 较多人员：user1(N)、user2(N)。（如有 --focus-projects）重点项目中：user1 在 PROJ-A 有 M 个任务，距节点 X 天，需优先处理。

----
*报告生成时间: YYYY-MM-DD*
```

### Step 7: 发布到 Confluence（可选）

当用户要求发布到 Confluence 页面时：

**参考模板**：严格遵循 W42 周报"进行中（项目问题分布情况）"表格的 storage 格式。

1. 必须先读取目标页面当前 storage 内容。
2. 定位标题/小节 `进行中（项目问题分布情况）`。
3. 如果该小节下存在旧表格，删除旧表格后插入最新静态风险表格。
4. 外层表格必须使用 `class="relative-table wrapped" style="width: 100.0%;"`，与 W42 一致。
5. 只替换该小节对应表格，不允许覆盖页面其他章节。
6. 找不到明确插入点时必须停止并提示用户，不允许猜测位置。
7. 发布前检查生成内容和最终页面内容不包含 `jirachart`。
8. **发布前质量检查**：执行与 Step 6 相同的总览列完整性检查，确保无空数据。

## Guardrails

- 所有 Filter ID 必须已在 `config.json` 中配置，若为 `null` 则提示用户先配置
- `team_members` 列表用于过滤非团队成员，若未配置则统计所有 assignee
- 人员名称统一使用 username（如 siyu.zhang），不使用 displayName
- "需关注"阈值：超过团队人均 1.5 倍
- "需支持"阈值：超过团队人均 2 倍
- 贡献排行显示全量人员，不截断
- 人员数量格式：`username(N)`，不标注百分比
- 所有数字为精确值，不使用"约"等模糊表述
- Block 问题列出涉及 Issue，仅显示 P0/High 需重点关注
- FR 较多提醒阈值：>=3 个
- Markdown 使用 UTF-8 编码，标准 Markdown 格式（## 标题）
- 报告紧凑排列，三个维度之间无空行
- 报告输出目录默认为 `./reports/`，不存在时自动创建

## 目录结构

```
jira-team-risk-analysis/
├── SKILL.md                    # 本文件（AI skill 定义）
├── config.json                 # Filter ID + 团队人员配置
├── scripts/
│   └── generate_confluence_risk_static.py   # 静态表格生成脚本（直接调 Jira API）
└── reports/                    # 报告输出目录（运行时创建）
    ├── 风险分析_YYYY-MM-DD.md
    └── 风险分析_静态图表_YYYY-MM-DD.html
```
