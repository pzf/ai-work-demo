---
disable-model-invocation: false
---

# Jira Weekly Report Publish

## 概述

自动化团队周报发布流程：从 Confluence 父页面定位最新周报子页面 → 复制并创建新一周周报 → **清空各事项「本周进展」列内容** → 执行 Jira 周报和风险分析，各自产出完整 6 列表格 → 将两张 6 列表格分别整体替换到「已完成（Defect/FR 完成情况）」和「进行中（项目问题分布情况）」章节 → 分析各节待推进事项（**含「一.NPI 项目交付」中「2.项目需求开发」子表**）→ 生成推进邮件并发送 → **推送 T信通知**。

## 触发条件

关键词：`发布周报`、`周报发布`、`publish weekly report`、`生成周报模板`、`copy weekly report`

## 前置依赖

- MCP 工具：`mcp__mcp-confluence__*`、`mcp__mcp-t-email__send_email`
- 同级 Skill：`jira-team-weekly-summary`（产出「已完成」6 列表格）、`jira-team-risk-analysis`（产出「进行中」6 列表格）
- `config.json` 中配置父页面 ID 及邮件收件人

## 数据区域定位（关键约定）

页面「一.NPI 项目交付」下有两个**独立章节**，各自含一张完整 6 列表格：

| 章节标题 | 数据来源 Skill | 生成脚本 | 表格 6 列 |
|---------|--------------|---------|----------|
| **已完成（Defect/FR 完成情况）** | `jira-team-weekly-summary` | `generate_confluence_static.py` | 总览、Defect 修复情况、FR 闭环情况、框架分析分布、XTS分析分布、项目分析分布 |
| **进行中（项目问题分布情况）** | `jira-team-risk-analysis` | `generate_confluence_risk_static.py` | 总览、Defect 分布情况、Block 分布情况、Defect 项目分布情况、FR 分布情况、FR 项目分布情况 |

**替换方式**：按「已完成」/「进行中」这两个**文字标题**定位章节，将章节下的**完整 6 列表格整体替换**（不保留旧表格、不保留旧 JiraChart）。

## 配置说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `parent_page_id` | Confluence 周报父页面 ID | `745543342` |
| `parent_page_title` | 父页面标题 | `团队周报` |
| `page_title_template` | 子页面命名模板 | `框架开发二组【 {year}W{week}】` |
| `email.to` | 推进邮件收件人列表 | `["zhanfengpeng@tcl.com"]` |
| `email.is_html` | 邮件是否 HTML 格式 | `true` |
| `tlink.to` | T信推送接收人列表（username） | `["zhanfengpeng"]` |
| `tlink.enabled` | 是否启用 T信通知 | `true` |
| `analysis_exclude_sections` | 不参与陈旧检查的节（「已完成/进行中」两张数据总览表） | `["已完成（Defect/FR 完成情况）", "进行中（项目问题分布情况）"]` |
| `section_owner_mapping` | 节标题 → 默认 Owner username，用于负责人列无法识别时的回退 | 见 config.json |
| `owner_name_mapping` | 中文名 → username 映射，用于表格 Owner 列解析 | 见 config.json |

## 执行步骤

### Step 1：定位最新周报子页面

使用 `mcp__mcp-confluence__confluence_get_page_children` 获取父页面（`parent_page_id`）下的所有子页面。

从子页面标题中提取周数，找到 `week` 值最大的页面作为**源页面**：
- 标题格式：`框架开发二组【 YYYYWww】`（如 `2026W31`）
- 提取 `W` 后面的数字作为当前周数

### Step 2：计算新周数并创建新页面

计算新周数：`新周数 = 当前最大周数 + 1`，年份取当前日期所在年份。

使用 `mcp__mcp-confluence__confluence_get_page` 以 `convert_to_markdown=false`（storage 格式）读取源页面完整内容。

**两阶段创建法**（避免 Confluence XHTML 校验失败导致页面不完整）：

**阶段 A**：先用精简骨架创建页面（仅含标题占位），再立即更新为完整内容。
1. 用 `mcp__mcp-confluence__confluence_create_page` 创建新页面，标题为 `框架开发二组【 2026W{week}】`，content 先写入精简 HTML（仅 `<p>placeholder</p>`），`content_format: "storage"`
2. 创建成功后，用 `mcp__mcp-confluence__confluence_update_page` 将 content 替换为源页面的**完整 storage 格式内容**，`content_format: "storage"`

**🚨 关键规则：源页面内容必须原样复制，不得做任何修改！**
- **禁止**在复制时添加 `@username` 标记、修改任何文字、删减任何表格内容或结构
- **禁止**简化或改写源页面的 HTML 结构（如去掉 `<div class="content-wrapper">`、合并 `<p>` 标签等）
- 源页面的 storage HTML 是唯一的模板来源，必须逐字符保留所有内容——包括 JiraChart 宏、status 宏、内联样式、`<div>` 嵌套结构等
- 新页面与源页面的唯一区别是：标题更新为新周数，其余内容完全相同

**阶段 B**（页面已有完整内容后）：用 Python 将 Step 3/4 产出的两张完整 6 列表格，按「已完成」/「进行中」标题定位后**整体替换**到对应章节。

> **执行时序**：阶段 A（创建页面）与 Step 2b（清空本周进展）可先完成；阶段 B 的表格替换必须等 Step 3/4 产出 `weekly_table_html` 和 `risk_table_html` 后再执行。最终通过一次 `mcp__mcp-confluence__confluence_update_page` 将「清空后的页面 + 两张新表格」写回。

**定位与替换逻辑**：

1. 在「一.NPI 项目交付」章节下，找到文字标题为「已完成」（或含「已完成」且含「Defect/FR 完成」）的标题节点，其下第一个 `<table>` 即为「已完成」数据表 → 用 `jira-team-weekly-summary` 产出的完整 6 列表格整体替换。
2. 找到文字标题为「进行中」（或含「进行中」且含「项目问题分布」）的标题节点，其下第一个 `<table>` 即为「进行中」数据表 → 用 `jira-team-risk-analysis` 产出的完整 6 列表格整体替换。

> **推荐**：直接调用本 Skill 自带的 `scripts/replace_tables.py`（已封装定位、替换、清空逻辑），无需手写解析代码：
>
> ```bash
> python scripts/replace_tables.py \
>   --storage-html source_w42_storage.html \
>   --weekly-table weekly_complete_table.html \
>   --risk-table risk_table.html \
>   --output updated_page.html
> ```

若需在自定义脚本中内联实现，参考以下**正确的定位逻辑**（注意：不可用 `find_all_next()` 直接取第一个 table，因为标题后代节点与跨章节边界会干扰定位，必须「遇下一标题即停止」）：

```python
from bs4 import BeautifulSoup

soup = BeautifulSoup(storage_html, 'html.parser')

HEADING_TAGS = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']

def find_table_after_heading(soup, title_keyword):
    """按标题关键字定位章节，返回其下第一张 table（跨兄弟/后代，遇下一标题即停）。"""
    for heading in soup.find_all(HEADING_TAGS):
        text = heading.get_text(strip=True)
        if title_keyword not in text:
            continue
        table = None
        for node in heading.find_all_next():
            if node.name in HEADING_TAGS and node is not heading:
                break  # 进入下一章节，停止
            if node.name == 'table':
                table = node
                break
        if table is not None:
            return table
    return None

def replace_table_by_title(soup, title_keyword, new_table_html):
    new_table = BeautifulSoup(new_table_html, 'html.parser').find('table')
    if new_table is None:
        raise RuntimeError(f"新表格 HTML 无效（无 <table>）：{title_keyword}")
    old_table = find_table_after_heading(soup, title_keyword)
    if old_table is None:
        raise RuntimeError(f"未在标题「{title_keyword}」下找到表格，请检查页面结构")
    old_table.replace_with(new_table)
    return True

# 「已完成（Defect/FR 完成情况）」→ weekly summary 表格
replace_table_by_title(soup, "已完成", weekly_summary_table_html)
# 「进行中（项目问题分布情况）」→ risk analysis 表格
replace_table_by_title(soup, "进行中", risk_analysis_table_html)

updated_html = str(soup)
```

> ⚠️ **真实页面结构说明**（已从参考页面 pageId=769721375 验证）：
> - 「已完成」标题为 `<h2><strong>已完成（</strong>Defect/FR 完成情况）</h2>`，其下**直接**是 6 列表格。
> - 「进行中」标题为 `<h2><strong>进行中（</strong>项目问题分布情况）</h2>`，其下第一个节点是 `<p>1.项目Defect、Block、FR 问题</p>`，**之后**才是 6 列表格。定位逻辑会跳过该 `<p>` 正确命中表格。

**注意**：
- `content_format` 必须为 `"storage"`
- 阶段 A 必须先创建再更新，确保源页面的 Confluence 宏（jirachart、status、task-list）不丢失
- 更新页面时 `title` 仍为新页面标题
- space_key 从源页面 metadata.space.key 获取
- **整体替换**：用子 skill 产出的完整 6 列表格**替换**章节下旧表格，不保留旧 JiraChart、不保留旧数据表；若标题下存在多个表格（如旧 JiraChart 宏表格），应一并清空后仅保留新表格
- 若按「已完成」/「进行中」标题定位失败，**必须停止执行**并提示用户提供页面实际标题结构，禁止用「总览」/「待解决」等单元格关键词猜测定位

### Step 2b：清空新页面「本周进展」列内容

新页面创建后，所有事项的「本周进展」列需要清空，以便团队成员填写本周新进展。

**操作方式**：使用 Python 脚本解析 storage HTML，对每个包含"本周进展"表头的表格，定位该列在数据行中对应的 `<td>`，将其内部 HTML 清空为 `<br/>`。

**🚨 关键规则：仅清空「本周进展」列，绝对不修改其他任何内容！**
- 只修改数据行中「本周进展」列表头对应的 `<td>` 单元格，将其内容替换为 `<br/>`
- **不得**修改表头行（`<th>`）
- **不得**修改「本周进展」列以外的任何列（项目、需求说明、负责人、交付时间、状态、风险等）
- **不得**添加或删除任何行、列、HTML 结构

```python
from bs4 import BeautifulSoup

soup = BeautifulSoup(storage_html, 'html.parser')

for table in soup.find_all('table'):
    # 查找表头行中"本周进展"列的索引
    headers = table.find('tr').find_all(['th'])
    progress_col_idx = None
    for i, th in enumerate(headers):
        if '本周进展' in th.get_text():
            progress_col_idx = i
            break
    
    if progress_col_idx is None:
        continue
    
    # 遍历数据行（跳过表头行），清空"本周进展"列
    for tr in table.find_all('tr')[1:]:
        cells = tr.find_all(['td', 'th'])
        if len(cells) > progress_col_idx:
            cells[progress_col_idx].clear()
            cells[progress_col_idx].append(BeautifulSoup('<br/>', 'html.parser'))

cleaned_html = str(soup)
```

**注意**：BeautifulSoup 需提前安装（`python -m pip install beautifulsoup4`）。清空操作在 Step 2 阶段 B（更新页面）之前执行，使用 `mcp__mcp-confluence__confluence_update_page` 将清空后的 HTML 写回页面。

> 💡 Step 2b 的清空逻辑已封装进 `scripts/replace_tables.py`（`clear_progress_column` 函数），可随表格替换一并完成，无需单独编写清空脚本。

清空范围覆盖所有章节中包含"本周进展"列的表格：
- 一.NPI 项目交付 / 项目需求开发 表格
- 二.XTS IR 测试管理 表格
- 三.AI 建设专项 表格
- 四.部门OKR以及组内重点承接事项 表格
- 五.复盘 & 培训 & 专利 & 洞察 表格
- 六.长期跟进项 表格
- 七.其他议题 表格

**注意**：仅清空数据行（`<tr>`）中对应列的 `<td>` 内容，不清空表头 `<th>`。

### Step 3：执行 Jira 数据分析（产出完整 6 列表格）

依次调用两个子 Skill 的**生成脚本**，各自产出**完整 6 列表格 HTML**（不是总览文本，也不是只填单元格）。

**关键规则：必须实时查询 Jira filter 重新生成数据，禁止复用 reports 目录下旧的 `weekly_complete_table_*.html`、`risk_table_*.html` 或历史 JSON 作为本次发布结果。**

#### 3a. 「已完成」表格 —— `jira-team-weekly-summary`

按 `jira-team-weekly-summary` 的 SKILL.md 执行（Step 0 自动抓取 → 生成），产出完整 6 列表格：

```bash
# 1. 抓取数据
cd <jira-team-weekly-summary>/scripts
python fetch_data.py --output reports/_weekly_data_YYYY-MM-DD.json

# 2. 生成完整 6 列表格（已完成）
python generate_confluence_static.py \
  --data reports/_weekly_data_YYYY-MM-DD.json \
  --output reports/weekly_complete_table_YYYY-MM-DD.html
```

产出表格 6 列：**总览、Defect 修复情况、FR 闭环情况、框架分析分布、XTS分析分布、项目分析分布**。这张完整表格就是「已完成（Defect/FR 完成情况）」章节要整体替换进去的内容（含总览列）。

#### 3b. 「进行中」表格 —— `jira-team-risk-analysis`

按 `jira-team-risk-analysis` 的 SKILL.md 执行，产出完整 6 列表格：

```bash
cd <jira-team-risk-analysis>/scripts
python generate_confluence_risk_static.py \
  --output reports/risk_table_YYYY-MM-DD.html
```

产出表格 6 列：**总览、Defect 分布情况、Block 分布情况、Defect 项目分布情况、FR 分布情况、FR 项目分布情况**。这张完整表格就是「进行中（项目问题分布情况）」章节要整体替换进去的内容（含总览列）。

#### 空数据检查（发布前强制执行）

在替换页面内容之前，必须对两张表格分别执行空数据检查：

```python
def validate_publish_content(html_content, section):
    """检查发布内容中是否有空数据"""
    empty_patterns = [
        '贡献排行：。',
        '问题较多的项目：。',
        '贡献排行：</span>',
        '贡献排行：<br',
        '问题较多的项目：</span>',
        '问题较多的项目：<br',
        '人员分布：。',
        '人员分布：</span>',
    ]
    for pattern in empty_patterns:
        if pattern in html_content:
            raise ValueError(f"[{section}] 包含空数据: {pattern}")

    import re
    if section == '已完成':
        dimensions = re.findall(r'本周\w+ (\d+) 个', html_content)
        rankings = html_content.count('贡献排行：')
        if rankings < len(dimensions):
            raise ValueError(f"[已完成] 贡献排行数量({rankings})少于维度数量({len(dimensions)})")
    elif section == '进行中':
        if '待解决' in html_content and '人员分布：' not in html_content:
            raise ValueError("[进行中] 有待解决问题但缺少人员分布")
    return True

validate_publish_content(weekly_table_html, '已完成')
validate_publish_content(risk_table_html, '进行中')
```

### Step 4：组装完整 6 列表格

Step 3 已直接产出两张完整 6 列表格 HTML（`weekly_complete_table_*.html` 和 `risk_table_*.html`），**无需再手工拼总览文本**。

- `weekly_table_html` = `weekly_complete_table_*.html` 文件内容（「已完成」章节的完整表格）
- `risk_table_html` = `risk_table_*.html` 文件内容（「进行中」章节的完整表格）

这两段 HTML 直接作为 Step 2 阶段 B 中 `replace_table_by_title` 的输入，整体替换到对应章节。

**🚨 发布前检查**：在 Step 2 阶段 B 更新页面前，必须对两张表格执行空数据检查（见 Step 3 的 `validate_publish_content`）。检查通过后才能继续。

### Step 5：分析待推进事项

页面更新完成后，使用 `mcp__mcp-confluence__confluence_get_page` 以 `convert_to_markdown=true` 重新读取新页面内容，分析各节的待推进事项。

#### 分析范围

分析以下各节及子表：

- `## 一.NPI 项目交付` 中的 **`2.项目需求开发`** 子表格（仅分析该子表，**不分析**「已完成（Defect/FR 完成情况）」和「进行中（项目问题分布情况）」两张数据总览表）
- `## 二.XTS IR 测试管理`
- `## 三.AI 建设专项`
- `## 四.部门OKR以及组内重点承接事项`
- `## 五.复盘 & 培训 & 专利 & 洞察`
- `## 六.长期跟进项`
- `## 七.其他议题`

**排除项**：不分析「一.NPI 项目交付」中的「已完成（Defect/FR 完成情况）」和「进行中（项目问题分布情况）」两张数据总览表（该部分数据由 Step 3 的两个子 Skill 生成）。

#### 陈旧判定规则

按以下优先级逐条匹配，取第一个命中的严重级别：

| 严重级别 | 判定条件 | 典型模式 |
|---------|---------|---------|
| 🔴 高 | 明确存在问题或风险 | `Delay`、`风险`、`卡在`、`阻塞`、`冲突`、`不合规范` |
| 🟡 中 | 本周进展未填写（清空后为空） | 本周进展列为空或仅有 `<br/>` — **需 Owner 填写** |
| 🟡 中 | 交付时间缺失 | 交付时间列为空、`—`、`待补充`、`待定` |
| 🟡 中 | 状态待推进 | `TODO`、状态列为空 |
| 🟡 中 | 引用旧周数（比当前周少 2 周以上） | `W31:无更新`（当前为 W33，跨度 2 周） |
| 🟢 低 | 正常进行中 | `ONGOING`（有进展描述 + 有交付时间）、`推进中`、`已完成`、`DONE` |

**通用规则（所有表格适用）**：
- 「本周进展」列为空（清空后）→ 🟡
- 「交付时间」列为空、`—` 或 `待` 开头 → 🟡
- 以上两条以**第一条匹配**为准，不重复标记（一项最多一条 🟡）

**周数陈旧计算**：`当前周数 - 引用周数 >= 2` 时标记为 🟡。例如当前 W33，引用 W31 → 差 2 周，标记；引用 W32 → 差 1 周，不标记。

**「项目需求开发」表特殊规则**：
- 该表的「风险」列若包含非空内容（如 `Delay`、`风险`、`阻塞`），直接按 🔴 高 标记
- 该表的「本周进展」列在 Step 2b 清空后为空 → 🟡（提醒 Owner 填写本周进展）
- 该表的「交付时间」列为空或 `—` → 🟡（提醒 Owner 补充交付时间）
- DONE/已完成状态的行**跳过「本周进展为空」检查**，但仍检查交付时间等其他字段
- 若一行同时命中多项规则，取最严重级别（🔴 > 🟡）

#### 提取规则

对每个判定为 🔴/🟡 的事项，提取：
1. **所属节**：如「XTS IR 测试管理」
2. **子项名称**：如「IR 测试」
3. **当前状态描述**：原文字（截取关键句，不超过 60 字）
4. **严重级别**：🔴 或 🟡
5. **关键字命中**：匹配到的模式
6. **Owner**：事项负责人

#### Owner 提取规则

**根本原则**：从页面**现有**内容中提取 Owner。页面使用中文姓名（如"高梓航"、"张思宇"），通过 `owner_name_mapping` 将中文名转为 username。**不要为了提取 Owner 而修改页面内容。**

按以下优先级逐级尝试：

**优先级 1 — 表格负责人列**（最高优先级）：
提取事项所在表格行中「负责人」列的值，使用 `owner_name_mapping` 将中文名转为 username。

**优先级 2 — 节 Owner 映射**：
若优先级 1 无法识别（负责人列为空），使用 config.json 中 `section_owner_mapping` 根据节标题映射到默认 section owner。

**优先级 3 — 不可识别**：
Owner 标记为 `（待确认）`。

#### 推荐页面格式规范（供用户手动参考，Skill 不得自动添加 @username）

> ⚠️ 以下为**建议**格式，用于帮助用户在手动编辑页面时添加 `@username` 标记以提升 AI 识别准确度。**Skill 在执行复制/创建页面时，不得自动添加任何 `@username` 标记，必须原样复制源页面内容。**

**格式 A：表格型（适用于有"进展"列的表格）**

在现有表格基础上，确保每行包含 `@username` 标记。示例：

```
| 专项 | 跟进事项 | 本周进展 | 风险 |
|------|---------|---------|------|
| IR 测试 | 项目IR需求 | 待基线升级... @forong.li | A17 checklist未释放 |
| Funcation Patch | 2026#7 Patch | 3笔待Owner评估 @forong.li | |
```

**格式 B：任务型（适用于 task-list 项）**

每个 `<ac:task-body>` 的文本末尾添加 `@username`：

```
闭源3.0 开发专项 @zihang.gao W31:TSSI 基线更新完成
端侧场景识别框架 @zhanfengpeng W31：待分配Owner
FaceUnlock NPS/BCR 改善 @yihuachen W31:
```

**格式 C：节标题**

节标题无需改动，Owner 通过 `section_owner_mapping` 配置即可。

**迁移方法**：手动或在下次创建新页面时，将现有 `<ac:link><ri:user ri:userkey="KEY">` 替换为 `@username`（基于已验证的 userkey_mapping）。

#### `section_owner_mapping` 配置

在 config.json 中维护每个节的默认 Owner，用于事项无显式 `@username` 时的回退：

```json
{
  "section_owner_mapping": {
    "一.NPI 项目交付": "zhanfengpeng",
    "二.XTS IR 测试管理": "forong.li",
    "三.AI 建设专项": "siyu.zhang",
    "四.部门OKR以及组内重点承接事项": "zhanfengpeng",
    "五.复盘 & 培训 & 专利 & 洞察": "zhanfengpeng",
    "六.长期跟进项": "zhanfengpeng",
    "七.其他议题": "zhanfengpeng"
  }
}
```

**注意**：section owner 仅作为最低优先级回退。若事项有 `@username` 标记，优先使用标记值。

#### Owner 统筹汇总

将提取的待推进事项按 Owner 分组汇总，格式如下：

```
### 📋 按 Owner 统筹

| Owner | 事项数 | 事项列表 |
|-------|--------|---------|
| zihang.gao | 2 | 拖拽直达(W31起无更新)、跨应用拖拽(待补充) |
| chuntian.ben | 3 | 控件不合规(W31起)、AI建设(待完善)... |
```

如有 `（待确认）` 项，单独追加一段 `待确认归属事项：N 项` 并列出。**注意**：统筹汇总段只在邮件正文最末出现一次，不在各节表格中重复。

### Step 6：发送推进邮件

使用 `mcp__mcp-t-email__send_email` 发送 HTML 格式邮件给 `config.json` 中 `email.to` 列表中的**所有**收件人。

#### 邮件参数

| 参数 | 值 |
|------|-----|
| `to` | `config.json` 中 `email.to` 列表（数组） |
| `subject` | `【周报填写提醒】框架开发二组 {year}W{week} 周报填写提醒` |
| `body` | 下方邮件正文模板渲染后的完整 HTML（必填） |
| `is_html` | `true` |

#### 邮件正文模板

```html
<h2>框架开发二组 {year}W{week} 周报 — 周报填写提醒</h2>
<p>以下事项需要各 Owner 填写更新，请在<strong style=\"color:#d32f2f\">周会前</strong>完成更新：</p>

<h3>🔴 需紧急处理</h3>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%">
<tr style="background:#ffebee"><th style="width:12%">所属节</th><th style="width:14%">子项</th><th style="width:12%">Owner</th><th style="width:62%">当前状态</th></tr>
<!-- 每个 🔴 事项一行 -->
<tr><td>{section}</td><td>{item}</td><td>{owner}</td><td>{status}</td></tr>
</table>

<h3>🟡 需关注推进</h3>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%">
<tr style="background:#fff8e1"><th style="width:12%">所属节</th><th style="width:14%">子项</th><th style="width:12%">Owner</th><th style="width:62%">当前状态</th></tr>
<!-- 每个 🟡 事项一行 -->
<tr><td>{section}</td><td>{item}</td><td>{owner}</td><td>{status}</td></tr>
</table>

<h3>📋 按 Owner 统筹</h3>
<p>以下按负责人汇总待推进事项，请各自确认更新：</p>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse; width:100%">
<tr style="background:#e3f2fd"><th style="width:15%">Owner</th><th style="width:8%">事项数</th><th style="width:77%">事项列表</th></tr>
<!-- 每个 Owner 一行，按事项数降序 -->
<tr><td>{owner}</td><td>{count}</td><td>{items_list}</td></tr>
</table>
<!-- 若有待确认项 -->
<p style="color:#e65100">⚠ 待确认归属事项：{unowned_items}</p>

<p style="color:#666; margin-top:20px">周报链接：<a href="{page_url}">{page_title}</a></p>

<h3>📝 新增议题提醒</h3>
<p style="color:#1565c0">各位如有需要在本周周报中讨论的新议题，请直接编辑周报页面添加，避免遗漏重要事项。</p>

<p style="color:#999; font-size:12px">此邮件由 WorkBuddy 自动生成 · {date}</p>
```

**规则**：
- 若某级别无事项，该段输出 `无` 并跳过表格
- 若所有事项均为 🟢，邮件主题改为 `【周报正常】...`，正文仅输出 `所有事项均已正常推进，无需特别关注。`
- 邮件正文需包含 `⏰ 请在周会前完成周报填写更新` 提醒行
- 正文使用 HTML 格式（`is_html: true`）

### Step 6b：发送 T信通知

在邮件发送完成后，使用 `mcp__mcp-t-link__push_message` 向 `config.json` 中 `tlink.to` 列表中的**所有**接收人推送简要周报摘要。

#### T信参数

| 参数 | 值 |
|------|-----|
| `to` | `config.json` 中 `tlink.to` 列表的**单个** username（`to` 为字符串，非数组；逐个遍历列表，对每个 username 单独调用一次 `push_message`） |
| `content` | 下方 T信消息模板渲染后的纯文本（必填） |
| `content_type` | `0`（文本） |

#### T信消息模板

```
【{year}W{week} 周报已发布】

一、问题解决：本周闭环N个(上周N，趋势)。Top: xxx(N)、xxx(N)
二、FR闭环：本周N个(上周N，趋势)。Top: xxx(N)、xxx(N)
三、框架分析：本周N个(上周N，趋势)。Top: xxx(N)、xxx(N)
四、XTS分析：本周N个(上周N，趋势)。Top: xxx(N)、xxx(N)

⚠ 风险：待解决N个；Block N个，P0/High共N个需关注
📋 周报链接：{page_url}
⏰ 请于周会前完成周报填写更新
📝 若有新增议题，请编辑周报页面添加
```

**规则**：
- 若 `tlink.enabled` 为 `false`，跳过 T信通知
- 若 `tlink.to` 为空列表，跳过 T信通知
- T信消息为纯文本（`content_type: 0`），控制在 500 字以内
- 若所有事项均为 🟢 正常，T信消息末尾增加「✅ 所有事项正常推进」

### Step 7：输出结果

回复中提供：
- 新创建的周报页面链接
- 周报和风险分析数据摘要（含趋势、贡献排行、风险标记）
- 邮件发送状态及识别的待推进事项数量

## Guardrails

- 周数递增逻辑：如果源页面为 W31，则新页面为 W32；年份取实际当前日期
- 源页面复制使用**两阶段法**：先用骨架创建页面，再更新为完整源内容，以避免 Confluence XHTML 校验失败导致页面内容不完整
- **🚨 复制源页面时禁止修改任何内容**：不得添加 `@username` 标记、不得改写文字、不得删减表格结构、不得简化 HTML。新页面与源页面的唯一区别是标题周数
- **🚨 清空「本周进展」时仅修改该列单元格**：不得修改表头、其他列、表格结构或任何非本周进展列的内容
- **替换「已完成」/「进行中」表格时，仅整体替换章节下对应的 6 列表格**，不得改动该章节以外的 JIRA 宏、状态标签、任务列表等 Confluence 特性内容
- **定位失败即停止**：若按「已完成」/「进行中」标题定位不到表格，必须停止并提示用户，禁止用「总览」/「待解决」等单元格关键词猜测定位
- 人员名称统一使用 username（如 siyu.zhang），不使用 displayName
- 所有数字为精确值，不使用"约"等模糊表述
- 趋势仅显示上涨/下降/持平三个值
- 项目分布取 TOP 5，不足时显示全部
- **陈旧分析覆盖全部有"本周进展"列的表格**，包括「一.NPI 项目交付」中「2.项目需求开发」子表。仅排除「已完成（Defect/FR 完成情况）」和「进行中（项目问题分布情况）」两张数据总览表
- **本周进展为空即标记 🟡**：Step 2b 清空后，所有事项的「本周进展」列均为空，每个事项都会被标记为 🟡，提醒 Owner 填写。仅 DONE/已完成 行跳过此检查
- **交付时间缺失即标记 🟡**：所有表格中「交付时间」列为空、`—`、`待补充`、`待定` 的行均标记
- 每条事项仅取**第一条匹配规则**，不重复标记（🔴 > 🟡）
- 周数陈旧阈值：引用周数与当前周数差距 >= 2 周才标记
- 邮件始终发送给 `config.json` 中 `email.to` 列表中的所有收件人
- T信始终发送给 `config.json` 中 `tlink.to` 列表中的所有接收人（当 `tlink.enabled` 为 `true` 时）
- T信消息为纯文本，控制在 500 字以内；逐人发送，不合并
- 若所有事项均为 🟢 正常，邮件主题改为「周报正常」，T信末尾追加正常提示
- 若所有事项均为 🟢 正常，邮件主题改为「【周报正常】...」，正文仅输出正常提示
- 邮件和 T信中统一使用「周报填写提醒」替代「待推进事项」
- 邮件正文为完整 HTML，包含内联样式以确保邮件客户端兼容
- **Owner 提取**：从页面现有内容中提取（表格负责人列 → 中文名映射 → section_owner_mapping）。**禁止为了 Owner 识别而修改页面内容（如添加 @username）**
- **禁止使用 `<ri:user>` userkey 推断 Owner**：userkey 为 Confluence 内部不透明标识符，无法通过 API 查询，任何基于 userkey 的映射均为猜测
- 推荐页面格式使用 `@username` 标记确保 AI 可识别
- **Owner 名称统一为 username**（如 siyu.zhang），与 Jira 数据格式一致
- 按 Owner 统筹汇总仅出现在邮件最末，不在各节表格中重复
- 邮件中 Owner 为空或待确认的项，在统筹汇总中单独列出
- 邮件和 T信末尾均包含「📝 新增议题提醒」和「⏰ 周会前截止」提示

## 目录结构

```
jira-weekly-report-publish/
├── SKILL.md                 # 本文件
├── config.json              # 父页面 ID、邮件收件人、section_owner_mapping、owner_name_mapping
└── scripts/
    └── replace_tables.py    # 按标题定位整体替换「已完成/进行中」表格 + 清空「本周进展」列
```
