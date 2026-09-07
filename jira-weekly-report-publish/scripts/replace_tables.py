#!/usr/bin/env python3
"""周报页面表格替换与「本周进展」清空工具。

职责：
1. 按「已完成」/「进行中」文字标题定位章节，整体替换其下第一张 6 列表格。
2. 清空所有含「本周进展」列表格的对应数据单元格。

用法：
    python replace_tables.py \\
        --storage-html source.html \\
        --weekly-table weekly_table.html \\
        --risk-table risk_table.html \\
        --output updated.html

参数：
    --storage-html  源页面 storage HTML 文件
    --weekly-table  jira-team-weekly-summary 产出的 6 列表格（已完成）
    --risk-table    jira-team-risk-analysis 产出的 6 列表格（进行中）
    --output        输出替换+清空后的 storage HTML

说明：
- 输入/输出均为 Confluence storage 格式 HTML（字符串）。
- 「已完成」表格替换为 weekly_table，「进行中」表格替换为 risk_table。
- 替换后仍执行「本周进展」清空（对页面中其它含该列的业务表格）。
"""
import argparse
import sys

from bs4 import BeautifulSoup

HEADING_TAGS = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']


def _read(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


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
            return heading, table
    return None, None


def replace_table_by_title(soup, title_keyword, new_table_html):
    """按标题定位并整体替换章节下第一张 table，返回是否成功。"""
    new_soup = BeautifulSoup(new_table_html, 'html.parser')
    new_table = new_soup.find('table')
    if new_table is None:
        raise RuntimeError(f"新表格 HTML 无效（无 <table>）：{title_keyword}")

    heading, old_table = find_table_after_heading(soup, title_keyword)
    if old_table is None:
        raise RuntimeError(f"未在标题「{title_keyword}」下找到表格，请检查页面结构")

    old_table.replace_with(new_table)
    return True


def clear_progress_column(soup):
    """清空所有含「本周进展」列表格的数据行对应单元格（仅数据行 td，不动表头 th）。"""
    cleared = 0
    for table in soup.find_all('table'):
        first_row = table.find('tr')
        if first_row is None:
            continue
        headers = first_row.find_all('th')
        progress_idx = None
        for i, th in enumerate(headers):
            if '本周进展' in th.get_text():
                progress_idx = i
                break
        if progress_idx is None:
            continue
        # 跳过表头行，清空数据行的「本周进展」列
        data_rows = table.find_all('tr')[1:]
        for tr in data_rows:
            cells = tr.find_all(['td', 'th'])
            if len(cells) > progress_idx:
                cell = cells[progress_idx]
                cell.clear()
                cell.append(BeautifulSoup('<br/>', 'html.parser'))
                cleared += 1
    return cleared


def main():
    parser = argparse.ArgumentParser(description='周报表格替换与本周进展清空')
    parser.add_argument('--storage-html', required=True, help='源页面 storage HTML 文件路径')
    parser.add_argument('--weekly-table', required=True, help='已完成 6 列表格 HTML 文件')
    parser.add_argument('--risk-table', required=True, help='进行中 6 列表格 HTML 文件')
    parser.add_argument('--output', required=True, help='输出文件路径')
    parser.add_argument('--no-clear', action='store_true', help='跳过「本周进展」清空')
    args = parser.parse_args()

    storage_html = _read(args.storage_html)
    weekly_html = _read(args.weekly_table)
    risk_html = _read(args.risk_table)

    soup = BeautifulSoup(storage_html, 'html.parser')

    replace_table_by_title(soup, '已完成', weekly_html)
    replace_table_by_title(soup, '进行中', risk_html)

    if not args.no_clear:
        cleared = clear_progress_column(soup)
        print(f'清空「本周进展」单元格：{cleared} 个', file=sys.stderr)

    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(str(soup))
    print(f'已输出：{args.output}')


if __name__ == '__main__':
    main()
