---
name: jira-team-weekly-summary
description: 分析团队过去一周在Jira上的问题解决、FR闭环、框架及XTS分析情况，生成结构化周报总结及四个可视化图表，支持插入Confluence页面直接展示。触发词：团队周报、团队分析总结、weekly
  summary、团队Jira总结、周报分析、team weekly report、团队工作总结。
disable-model-invocation: false
---

# Jira Team Weekly Summary

## 概述

此 Skill 用于分析团队过去一周在 Jira 上的工作产出，基于 Jira Filter 自动生成结构化周报及可视化图表。输出共 **6 个部分**，均可插入 Confluence 页面直接展示：

1. **文字周报总结** — 四个维度的紧凑纯文字总结，含趋势对比
2. **问题解决二维矩阵表** (filter 37711) — 静态 Confluence 表格，人员(行) × 项目(列)，单元格为可点击的 JQL 超链接
3. **FR 闭环二维矩阵表** (filter 38267) — 静态 Confluence 表格，人员(行) × 项目(列)，单元格为可点击的 JQL 超链接
4. **框架分析分布** — Y 轴为 `framework_analyzed.team_members` 团队成员，展示每人本周分析问题数量，数量链接到对应 `issueFunction in commented` JQL
5. **XTS分析分布** — Y 轴为 `xts_analyzed.team_members` 团队成员，展示每人本周分析问题数量，数量链接到对应 `issueFunction in commented` JQL
6. **项目分析分布** — 汇总框架分析 + XTS 分析的项目分布，Y 轴为项目，X 轴为数量、占比，数量链接到 Jira 具体问题列表，`OTHER_PROJECTS` 归并为“其他”

## 前置条件

- MCP 工具：`mcp__mcp-jira__jira_search`、`mcp__mcp-confluence__confluence_update_page`
- `config.json` 中配置四个 Filter ID、团队人员列表及 Confluence 页面信息

## 使用方式

### 触发条件

当用户输入包含以下关键词时触发：
- `团队周报`、`团队分析总结`、`团队工作总结`
- `weekly summary`、`team weekly report`
- `周报分析`、`团队Jira总结`

### Filter 配置

| Filter | 本周 ID | 上周 ID | 用途 |
|--------|---------|---------|------|
| resolved_defects | 37711 | 106628 | 团队过去一周解决的问题 |
| fr_closed | 38267 | 106629 | 团队过去一周闭环的 FR |
| framework_analyzed | 106293 | - | 框架团队过去一周分析过的问题 |
| xts_analyzed | 106624 | - | XTS 团队过去一周分析过的问题 |

### 团队人员配置

**框架分析**（7 人）：zihang.gao、yihuachen、siyu.zhang、chuntian.ben、hailongwang、ex_jiawei.liu、zhanfengpeng

**XTS 分析**（3 人）：yi-chen、forong.li、zhongwen.nong

## 执行步骤

### Step 0: 自动抓取数据（推荐）

数据获取由 `scripts/fetch_data.py` 自动完成，无需手工拼 JSON：

```bash
python scripts/fetch_data.py --output reports/_weekly_data_YYYY-MM-DD.json
```

该脚本直接通过 connector-proxy 的 MCP 端点调用 Jira API，自动拉取：
- 四个 filter 本周总数 + issue 详情（用于二维矩阵、项目分布、贡献排行）
- 问题解决/FR 闭环的上周 filter 总数（趋势对比）
- 框架/XTS 每位成员的 `issueFunction in commented` 本周/上周评论计数（个人分析数与趋势）

认证信息从 `CODEBUDDY_MCP_CONFIG` 环境变量自动读取，无需手动传入。产出结构化 JSON，供 Step 4/5 的 `weekly_summary.py` 和 `generate_confluence_static.py` 消费。

仅在脚本不可用或需要临时排查时，才回退到下方 Step 1~3 的手工查询流程。

### Step 1: 查询本周数据（获取总数 + 项目分布 + 二维表原始数据）

使用 `mcp__mcp-jira__jira_search`，`limit=200`，`fields="assignee,project,status"` 查询四个 filter：

```
jql = "filter = 37711"    # 问题解决
jql = "filter = 38267"    # FR 闭环
jql = "filter = 106293"   # 框架分析
jql = "filter = 106624"   # XTS 分析
```

从每个 filter 结果中提取：
- **总数（total）**：用于报告各维度本周数量
- **issues 详情**：包含 `key`、`assignee.name`、`project.key`、`status.name`
- **按 project.key 分组的项目分布**：用于总览“问题较多的项目”和饼图/静态表格数据；四个 filter 都必须采集该分布
- **按 assignee.name 分组的贡献排行**：用于总览“贡献排行”；`resolved_defects` 和 `fr_closed` 可由矩阵行合计兜底，`framework_analyzed` 和 `xts_analyzed` 必须使用 Step 3 个人分析数量
- **按 assignee.name + project.key 交叉分组**：用于 filter 1 和 filter 2 二维表

> **注意**：MCP 工具 `mcp__mcp-jira__jira_search` 固定只返回 `id、key、summary、status、project、assignee` 6 个字段，**`issuetype` 字段无论如何请求都不会返回**（这是该工具本身的硬限制）。因此问题解决二维矩阵无法按 issuetype 分列，改为按 `project.key` 分列（filter 37711 本身已限定为 Defect 类型，按 issuetype 分列无实际区分度）。

**贡献排行数据来源规则**：

| 维度 | 贡献排行数据来源 | 说明 |
|------|----------------|------|
| 问题解决 | filter 37711 的 assignee.name 分布 | assignee = 问题解决人，可信 |
| FR 闭环 | filter 38267 的 assignee.name 分布 | assignee = FR 闭环人，可信 |
| 框架分析 | Step 3 `issueFunction in commented` 每人查询 | filter 106293 的 assignee 不等于分析者，**禁止使用** |
| XTS 分析 | Step 3 `issueFunction in commented` 每人查询 | filter 106624 的 assignee 不等于分析者，**禁止使用** |

**为什么框架/XTS 不能用 filter 的 assignee？**
- filter 106293/106624 返回的 assignee 是问题分配人，不是实际分析人
- filter 可能包含大量非团队成员
- 实际分析人通过评论行为体现，需用 `issueFunction in commented` 查询

### Step 2: 查询上周趋势数据

**问题解决 & FR 闭环**：使用 `last_week_filter_id` 查询上周 filter：
```
jql = "filter = 106628"   # 问题解决上周
jql = "filter = 106629"   # FR 闭环上周
```

**框架分析 & XTS 分析**：使用 `issueFunction in commented` 查询上周分析总数：
```
框架: (issueFunction in commented("by zihang.gao after -14d before -7d") OR ... 7人) AND issuetype in (Bug, Defect, Defect_new)
XTS:  (issueFunction in commented("by yi-chen after -14d before -7d") OR ... 3人) AND issuetype in (Bug, Defect, Defect_new)
```

### Step 3: 查询个人分析数量（仅框架 & XTS）

对 `config.json` 中 `team_members` 配置的每位人员，**逐人单独**用 `maxResults=1` 查询总数。每人一个独立 API 调用，按顺序依次发送，通过调用顺序区分结果归属：

```
框架团队（7 人，按顺序）：
1. jql = "issueFunction in commented("by zihang.gao after -7d") AND issuetype in (Bug, Defect, Defect_new)"
2. jql = "issueFunction in commented("by yihuachen after -7d") AND issuetype in (Bug, Defect, Defect_new)"
3. jql = "issueFunction in commented("by siyu.zhang after -7d") AND issuetype in (Bug, Defect, Defect_new)"
4. jql = "issueFunction in commented("by chuntian.ben after -7d") AND issuetype in (Bug, Defect, Defect_new)"
5. jql = "issueFunction in commented("by hailongwang after -7d") AND issuetype in (Bug, Defect, Defect_new)"
6. jql = "issueFunction in commented("by ex_jiawei.liu after -7d") AND issuetype in (Bug, Defect, Defect_new)"
7. jql = "issueFunction in commented("by zhanfengpeng after -7d") AND issuetype in (Bug, Defect, Defect_new)"

XTS 团队（3 人，按顺序）：
1. jql = "issueFunction in commented("by yi-chen after -7d") AND issuetype in (Bug, Defect, Defect_new)"
2. jql = "issueFunction in commented("by forong.li after -7d") AND issuetype in (Bug, Defect, Defect_new)"
3. jql = "issueFunction in commented("by zhongwen.nong after -7d") AND issuetype in (Bug, Defect, Defect_new)"
```

**注意**：由于并行调用结果顺序不可控，**必须逐个顺序调用**（非并行），确保调用顺序与人员顺序一致，以便正确将 total 映射到具体人员。

### Step 4: 生成文字周报总结

按以下紧凑纯文字格式输出到 `reports/周报_YYYY-MM-DD.md`，可直接复制到 Confluence。

**推荐方式**（配合 Step 0 自动抓取的数据）：

```bash
python scripts/weekly_summary.py --data reports/_weekly_data_YYYY-MM-DD.json --output reports/周报_YYYY-MM-DD.md
```

**总览字段完整性要求**：
- 每一行都必须输出“贡献排行”和“问题较多的项目”的具体数据，格式为 `name(N)`。
- 禁止输出空字段，例如 `贡献排行：。`、`问题较多的项目：。`。
- 如果确实无数据，必须输出 `贡献排行：无。` 或 `问题较多的项目：无。`。
- `resolved_defects` 若缺少 `by_person`，必须从 `matrix` 行合计生成贡献排行；`matrix` 列就是项目（project.key），`by_project` 由 `matrix` 列合计生成。
- `fr_closed` 若缺少 `by_person`，必须从 `matrix` 行合计生成贡献排行；若缺少 `by_project`，可从 `matrix` 列合计生成项目分布，因为 FR 矩阵列就是项目。
- `framework_analyzed` / `xts_analyzed` 的贡献排行必须使用 Step 3 个人评论分析计数，缺失时输出 `无`，不得用 filter assignee 冒充分析人。
- 发布到 Confluence 前必须检查生成 HTML 不包含 `贡献排行：。`、`问题较多的项目：。`、`贡献排行：</span>` 等空结果。
- 如果某维度本周总数 `N > 0`，但缺少“贡献排行”或“问题较多的项目”的具体统计数据，必须停止执行并补充 Jira 查询/数据结构，不能用 `无` 兜底。只有 `N = 0` 时才允许输出 `无`。

```
## 团队周报分析（YYYY-MM-DD 至 YYYY-MM-DD）

一、问题解决
本周闭环 N 个（上周 N，趋势）。贡献排行：user1(N)、user2(N)、...。问题较多的项目：PROJ1(N)、PROJ2(N)、...。
二、FR 闭环
本周闭环 N 个（上周 N，趋势）。贡献排行：user1(N)、user2(N)、...。问题较多的项目：PROJ1(N)、PROJ2(N)、...。
三、框架问题分析
本周分析 N 个（上周 N，趋势）。贡献排行：user1(N)、user2(N)、...。问题较多的项目：PROJ1(N)、PROJ2(N)、...。
四、XTS 问题分析
本周分析 N 个（上周 N，趋势）。贡献排行：user1(N)、user2(N)、...。问题较多的项目：PROJ1(N)、PROJ2(N)、...。

----
*报告生成时间: YYYY-MM-DD*
```

### Step 5: 生成五个静态可视化输出

默认生成可直接插入 Confluence storage 内容的完整 6 列表格 `reports/周报_静态图表_YYYY-MM-DD.html`。

**推荐方式**（配合 Step 0 自动抓取的数据）：

```bash
python scripts/generate_confluence_static.py --data reports/_weekly_data_YYYY-MM-DD.json --output reports/周报_静态图表_YYYY-MM-DD.html
```

**参考模板**：严格参照 `框架开发二组 Week 42`（Page ID: 769721375）的 Confluence storage 格式。原始参考文件 `source_w42.html` **当前未随 skill 提供**，实际格式规范以下方「W42 参考模板规范」小节为准（该小节已固化 W42 的核心 HTML 结构、样式与 JQL 链接格式）。

**关键要求**：
- 禁止使用 `jirachart` 宏或其他动态 Jira 控件，避免 Confluence 页面卡顿
- 图表数据必须在 Skill 执行时从 Jira 查询并固化到 Confluence 内容中
- **表格列必须与 W42 参考模板保持一致**：总览、Defect 修复情况、FR 闭环情况、框架分析分布、XTS分析分布、项目分析分布
- **总览列格式必须与 W42 完全一致**：使用 `<span style="color: rgb(0,51,102);">` 深蓝色样式包裹文本，每条用 `<br/>` 分隔，外层用 `<p>` 包裹

如用户明确要求截图/图片，也可调用 `scripts/generate_charts.py` 生成 4 张 PNG 图片，用户手动上传或通过可用附件工具插入。

#### W42 参考模板规范

以下为 W42 周报（Page ID: 769721375）**已完成表格**的核心格式规范，所有输出必须严格遵循：

##### 外层 6 列表格结构

```html
<table class="relative-table wrapped" style="width: 100.0%;">
<colgroup><col style="width: 20.0%;"/><col style="width: 16.0%;"/><col style="width: 16.0%;"/><col style="width: 16.0%;"/><col style="width: 16.0%;"/><col style="width: 16.0%;"/></colgroup>
<tbody>
<tr><th>总览</th><th>Defect 修复情况</th><th>FR 闭环情况</th><th>框架分析分布</th><th>XTS分析分布</th><th>项目分析分布</th></tr>
<tr>
  <td><!-- 总览文本，见下方格式 --></td>
  <td><!-- 内嵌子表格 1 --></td>
  <td><!-- 内嵌子表格 2 --></td>
  <td><!-- 内嵌子表格 3 --></td>
  <td><!-- 内嵌子表格 4 --></td>
  <td><!-- 内嵌子表格 5 --></td>
</tr>
</tbody></table>
```

##### 总览列文本格式（W42 标准）

```html
<p><span style="color: rgb(0,51,102);">一、问题解决本周闭环 N 个（上周 N，趋势）。贡献排行：user1(N)、user2(N)。问题较多的项目：PROJ1(N)、PROJ2(N)。</span><br/>
<span style="color: rgb(0,51,102);">二、FR 闭环本周闭环 N 个（上周 N，趋势）。贡献排行：user1(N)、user2(N)。问题较多的项目：PROJ1(N)、PROJ2(N)。</span><br/>
<span style="color: rgb(0,51,102);">三、框架问题分析本周分析 N 个（上周 N，趋势）。贡献排行：user1(N)、user2(N)。问题较多的项目：PROJ1(N)、PROJ2(N)。</span><br/>
<span style="color: rgb(0,51,102);">四、XTS 问题分析本周分析 N 个（上周 N，趋势）。贡献排行：user1(N)、user2(N)。问题较多的项目：PROJ1(N)、PROJ2(N)。</span></p>
```

**🚨 总览列完整性检查（发布前强制执行）**：
1. 每条 `<span>` 中的「贡献排行」和「问题较多的项目」必须有具体数据，格式为 `name(N)`
2. 禁止输出空字段，如 `贡献排行：。` 或 `问题较多的项目：。`
3. 如果某维度本周总数 `N > 0`，但缺少贡献排行或项目分布数据，必须停止发布并补充 Jira 查询
4. 只有 `N = 0` 时才允许输出 `贡献排行：无。`
5. 发布前必须用 Python 脚本检查 HTML 字符串中不包含 `贡献排行：。`、`问题较多的项目：。`、`贡献排行：</span>` 等空结果
6. `OTHER_PROJECTS` 必须归并显示为"其他"，不得直接出现在总览或表格中

##### 内嵌子表格格式（W42 标准）

每个数据列内嵌的子表格使用统一格式：
```html
<table class="wrapped relative-table" data-mce-resize="false" style="width: 100.0%;font-size: 12.0px;">
<tbody>
<!-- 表头行 + 数据行 + 合计行 -->
</tbody></table>
```

- 字体大小固定为 `font-size: 12.0px`
- 空单元格使用 `<br/>`（不是空 `<td></td>`）
- 数字单元格必须是 Jira JQL 超链接，`target="_blank"`
- 合计行使用 `<th>` 而非 `<td>` 以加粗显示

**图表一：问题解决二维矩阵表**（filter 37711）
- Y 轴（行）：经办人（assignee.name）
- X 轴（列）：项目（project.key）
- 单元格：修复个数，作为超链接指向对应 JQL
- 最后一列为行合计
- 最后一行为列合计

JQL 链接格式：
```
{config.jira_url}/issues/?jql=filter%3D{filter_id}%20AND%20assignee%3D{username}%20AND%20project%3D{project_key}%20AND%20issuetype%20in%20(Defect,Defect_new)
```

注意：
- Defect 修复情况的二维表超链接必须固定筛选 `issuetype in (Defect, Defect_new)`，用于覆盖两种 Defect 类型；禁止生成 `issuetype=Bug` 或 `type=Bug`。
- 由于 MCP 工具不返回 `issuetype` 字段，X 轴改用 `project.key`（见 Step 1 注意事项）；链接中同时带 `project` 和 `issuetype in (Defect, Defect_new)` 条件以精确定位。

链接使用 `target="_blank"` 在新窗口打开。

**图表二：FR 闭环二维矩阵表**（filter 38267）
- Y 轴（行）：经办人（assignee.name）
- X 轴（列）：项目（project.key）
- 单元格：FR 闭环个数，作为超链接指向对应 JQL
- 最后一列为行合计
- 最后一行为列合计

JQL 链接格式：
```
{config.jira_url}/issues/?jql=filter%3D{filter_id}%20AND%20assignee%3D{username}%20AND%20project%3D{project_key}
```
链接使用 `target="_blank"` 在新窗口打开。

**图表三：框架分析人员分布表**
- Y 轴（行）：`framework_analyzed.team_members` 中的团队成员
- X 轴（列）：分析数量、占比
- 分析数量来自 Step 3 每个成员独立查询的 `issueFunction in commented("by <user> after -7d")` total
- 数量单元格必须带 Jira JQL 超链接：`issueFunction in commented("by <user> after -7d") AND issuetype in (Bug, Defect, Defect_new)`
- 禁止使用 filter 106293 的 assignee 作为分析人员分布

**图表四：XTS 分析人员分布表**
- Y 轴（行）：`xts_analyzed.team_members` 中的团队成员
- X 轴（列）：分析数量、占比
- 分析数量来自 Step 3 每个成员独立查询的 `issueFunction in commented("by <user> after -7d")` total
- 数量单元格必须带 Jira JQL 超链接：`issueFunction in commented("by <user> after -7d") AND issuetype in (Bug, Defect, Defect_new)`
- 禁止使用 filter 106624 的 assignee 作为分析人员分布

**图表五：项目分析分布表**（汇总 filter 106293 + 106624）
- 汇总框架分析和 XTS 分析的所有项目分布数据
- Y 轴（行）：项目
- X 轴（列）：数量、占比
- 表格必须包含：项目、数量、占比、总数行
- 表格总数为框架分析 total + XTS 分析 total，并与总览三/四两项合计一致
- 项目名和数量必须带 Jira JQL 超链接，跳转到具体问题列表：`(filter=106293 OR filter=106624) AND project=<project_key>`
- 总数行必须带 Jira JQL 超链接：`filter=106293 OR filter=106624`
- 如果分页只拉取到部分 issue，导致项目分布合计小于 total，差额必须归入“其他”
- `OTHER_PROJECTS` 必须归并显示为“其他”，禁止直接输出 `OTHER_PROJECTS`
- “其他”为多个项目聚合、`OTHER_PROJECTS` 或分页差额，不生成项目级链接，避免跳转条件不准确

### Step 6: 发布到 Confluence（可选，必须局部插入）

如果 `config.json` 中配置了 `confluence.page_id`，发布前必须先使用 `mcp__mcp-confluence__confluence_get_page` 读取目标页面当前 storage 内容，并在保留原页面结构、标题、样式和已有模块顺序的基础上进行**局部插入/替换**。

**参考模板**：所有 Confluence 发布内容必须严格遵循 W42 周报（Page ID: 769721375）的 storage 格式。W42 是经过验证的规范模板，其 HTML 结构、样式、JQL 链接格式均为标准（具体规范见上方「W42 参考模板规范」小节；原始 `source_w42.html` 文件未随 skill 提供）。

发布规则：

1. 读取目标页面当前 storage 内容，`convert_to_markdown=false`。
2. 只生成需要插入的小段 storage 片段，不生成整页模板。
3. 生成完整 6 列表格，列名固定为：总览、Defect 修复情况、FR 闭环情况、框架分析分布、XTS分析分布、项目分析分布。
4. 外层表格必须使用 `class="relative-table wrapped" style="width: 100.0%;"`，与 W42 一致。
5. 在目标页面中查找"已完成（Defect/FR 完成情况）"小节下已有的 6 列周报表格：
   - 如果已存在该表格，必须删除旧表格后插入新生成的完整表格。
   - 如果不存在该表格，则插入到该小节标题后。
6. 不允许保留旧 JiraChart 表格或旧静态结果，避免重复展示。
7. 如果找不到"已完成（Defect/FR 完成情况）"小节，必须停止并提示用户提供页面原模板或插入位置，禁止猜测位置。
8. 调用 `confluence_update_page` 时，`content` 必须是"原页面其他内容 + 新生成完整 6 列表格"，不得破坏页面其他章节。

**发布前质量检查（强制执行）**：

```python
def validate_overview_cell(html_content):
    """检查总览单元格中是否有空数据"""
    import re
    empty_patterns = [
        '贡献排行：。',
        '问题较多的项目：。', 
        '贡献排行：</span>',
        '贡献排行：<br',
        '问题较多的项目：</span>',
        '问题较多的项目：<br',
    ]
    for pattern in empty_patterns:
        if pattern in html_content:
            raise ValueError(f"总览单元格包含空数据: {pattern}")
    # 检查每个维度有数字但没有贡献排行的情况
    # 如 "本周闭环 5 个" 后面必须有 "贡献排行："
    dimensions = re.findall(r'本周\w+ (\d+) 个', html_content)
    rankings = html_content.count('贡献排行：')
    if rankings < len(dimensions):
        raise ValueError(f"贡献排行数量({rankings})少于维度数量({len(dimensions)})，数据不完整")
    return True
```

**禁止**发布 `ac:name="jirachart"` 宏。发布前应检查生成内容中不包含 `jirachart`。

否则，将生成的静态 HTML/PNG 文件路径提供给用户，用户可手动插入到 Confluence。

## 可视化图表规则

### HTML 页面结构

```
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>团队周报可视化</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@3.9.1/dist/chart.min.js"></script>
  <style>...</style>
</head>
<body>
  <div id="content">
    <!-- 标题 -->
    <h1>团队周报可视化（YYYY-MM-DD 至 YYYY-MM-DD）</h1>
    
    <!-- 图表一：问题解决 -->
    <h2>一、问题解决 — 人员 × 项目</h2>
    <table>...</table>
    
    <!-- 图表二：FR 闭环 -->
    <h2>二、FR 闭环 — 人员 × 项目</h2>
    <table>...</table>
    
    <!-- 图表三：框架分析饼图 -->
    <h2>三、框架分析 — 项目分布 TOP 10</h2>
    <canvas id="chart-fw"></canvas>
    
    <!-- 图表四：XTS 分析饼图 -->
    <h2>四、XTS 分析 — 项目分布 TOP 10</h2>
    <canvas id="chart-xts"></canvas>
  </div>
  <script>/* Chart.js 代码 */</script>
</body>
</html>
```

### 样式要求

- 表格单元格宽度最小 60px，标题行加粗
- 第二行（汇总行）加粗并带背景色
- 最后一列（合计列）加粗
- 超链接颜色为蓝色 `#0052CC`，无下划线，悬停下划线
- 饼图 canvas 宽度 500px，高度 400px
- 饼图图例在右侧，标签显示百分比
- 整体页面最大宽度 1100px，居中

### 颜色方案（饼图）

10 种颜色循环（用于 TOP 10 项目）：
```
#4C78A8, #F58518, #E45756, #72B7B2, #54A24B,
#EECA3B, #B279A2, #FF9DA6, #9D755D, #BAB0AC
```
"其他"使用 `#D3D3D3`

## 输出文件

执行后生成以下文件（存放于 `reports/` 目录）：

| 文件 | 内容 | 插入 Confluence |
|------|------|----------------|
| `周报_YYYY-MM-DD.md` | 文字周报总结（纯 Markdown） | 可直接复制内容 |
| `周报_静态图表_YYYY-MM-DD.html` | 5 个静态可视化输出（Confluence storage HTML 片段，整体为 6 列表格） | 直接合并到页面 storage 内容 |
| `chart_*_YYYY-MM-DD.png` | 可选：4 张 PNG 静态图片 | 有附件上传工具时上传并嵌入，或手动上传 |

## Guardrails

- 统计时间窗口为过去 7 天（filter 内置）
- **本周分析总数**（框架/XTS）使用 filter 查询的 total，**贡献排行**使用 `issueFunction in commented` 每人独立计数
- **严禁**用 filter 106293/106624 的 assignee 分布代替贡献排行
- 二维表人员仅显示有数据的人员（按合计降序排列），不显示全零行
- 二维表项目仅显示有数据的列（按合计降序排列）
- 二维表合计列（行合计、列合计）自动计算
- JQL 超链接中的特殊字符需要 URL 编码（空格 → `%20`）
- 饼图 TOP 10 取至第 10 名，从第 11 名起全部归为"其他"
- 如果项目数 ≤ 10，不显示"其他"分类
- 项目分布取 TOP 5（文字周报中），不足时显示全部
- 人员名称统一使用 username（如 siyu.zhang），不使用 displayName
- 人员数量格式：`username(N)`，不标注百分比（文字周报中）
- 所有数字为精确值，不使用"约"等模糊表述
- 趋势仅显示上涨/下降/持平三个值
- Markdown 使用 UTF-8 编码，标准 Markdown 格式（## 标题）
- 周报紧凑排列，四个维度之间无空行
- HTML 文件使用 UTF-8 编码
- Confluence 页面发布内容不得包含 `ac:name="jirachart"` 或动态 JiraChart 宏
- 优先使用静态 Confluence table/HTML/SVG；如果使用图片，图片必须在 Skill 执行时生成

## 配置格式

### config.json 完整示例

```json
{
  "team_name": "MyTeam",
  "filters": { ... },
  "analysis_keywords": [ ... ],
  "defaults": { ... },
  "jira_url": "https://jira.tcl.com",
  "confluence": {
    "space_key": "TEAM",
    "page_id": "123456789",
    "page_title": "团队周报"
  }
}
```

`confluence.page_id` 为可选配置。如果配置了，Step 6 将自动更新 Confluence 页面。

## 目录结构

```
jira-team-weekly-summary/
├── SKILL.md                    # 本文件（AI skill 定义）
├── config.json                 # Filter ID + 团队人员 + Confluence 配置
└── scripts/
    ├── fetch_data.py                  # 数据抓取脚本（自动调 Jira API 生成 JSON，推荐）
    ├── weekly_summary.py              # 文字周报生成脚本
    ├── generate_confluence_static.py  # 静态 Confluence 图表片段生成脚本（默认）
    ├── generate_charts.py             # PNG 静态图片生成脚本（可选）
    └── generate_visuals.py            # 旧 HTML 图表生成脚本（仅本地预览，不用于 Confluence 发布）
```

## 依存关系

- **Step 1 输出** → Step 4 文字周报 + Step 5 所有 4 个图表
- **Step 2 输出** → Step 4 趋势对比
- **Step 3 输出** → Step 4 框架/XTS 贡献排行
- **Step 5 输出** → Step 6 Confluence 发布
