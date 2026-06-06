#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Textual UI for Photo & Video Renamer  v2.8

视觉风格：接近 Windows 经典浅色 GUI
- 浅灰背景 + 白色面板 + 深蓝高亮
- 勾选状态显示 ✓ 而非 x
- 输出格式改为下拉选择器（Select 组件）
- CSV 路径不再手动填写，默认存到目标目录；子目录各自生成独立 CSV
- 预览 / 执行 / 撤销 固定实体按钮，始终可见

Usage:
    python photo_renamer.py --tui
    python photo_renamer_tui.py   (dev mode)

快捷键:
    P  - 预览
    E  - 执行重命名
    U  - 撤销最近 CSV
    Q  - 退出
"""

import os
import subprocess
import sys
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.theme import Theme
from textual.widgets import (
    Button, Checkbox, DataTable, Footer, Header,
    Input, Label, Select, Static,
)

from photo_renamer import (
    RenameJobOptions,
    add_pattern_suggestion,
    append_history_report,
    discover_rule_suggestions,
    load_format_profiles,
    load_history_reports,
    run_rename_job,
    save_format_profile,
    undo_from_csv,
    write_undo_report,
)

# ── Windows 浅色主题颜色定义 ─────────────────────────────────────────────────
WIN_THEME = Theme(
    name='win-light',
    primary='#0078d4',          # 微软蓝
    secondary='#005a9e',
    accent='#005a9e',
    background='#f0f0f0',       # 经典 Win 灰背景
    surface='#ffffff',          # 白色面板
    panel='#f5f5f5',
    error='#c42b1c',
    success='#107c10',
    warning='#d97706',
    foreground='#1a1a1a',
    dark=False,
    variables={
        'text': '#1a1a1a',
        'text-muted': '#5c5c5c',
        'border': '#cccccc',
        'input-background': '#ffffff',
        'input-foreground': '#1a1a1a',
        'button-color-foreground': '#ffffff',
    },
)


def open_folder(path: str) -> str:
    """在系统文件管理器里打开 path 所在目录。返回错误信息或空字符串。"""
    try:
        target = Path(path)
        if not target.exists():
            return f'路径不存在: {path}'
        folder = target if target.is_dir() else target.parent
        if sys.platform == 'win32':
            os.startfile(str(folder))
        elif sys.platform == 'darwin':
            subprocess.run(['open', str(folder)], check=True)
        else:
            subprocess.run(['xdg-open', str(folder)], check=True)
        return ''
    except Exception as e:
        return f'打开文件夹失败: {e}'


def _build_format_options() -> list[tuple[str, str]]:
    """返回 Select 组件所需的 (label, value) 元组列表，来自 load_format_profiles()。"""
    try:
        profiles = load_format_profiles()
    except Exception:
        profiles = []
    options = []
    current_value = ''
    for p in profiles:
        label = f"{'✓ ' if p.get('current') else '  '}{p['name']}  →  {p['format']}"
        options.append((label, p['format']))
        if p.get('current'):
            current_value = p['format']
    if not options:
        options = [('  默认（YYYYMMDD_HHMMSS）  →  %Y%m%d_%H%M%S', '%Y%m%d_%H%M%S')]
        current_value = '%Y%m%d_%H%M%S'
    return options, current_value


class PhotoRenamerApp(App):
    """Photo & Video Renamer — Windows 风格 TUI"""

    TITLE = 'Photo & Video Renamer  v2.8'
    CSS_PATH = None

    # 内联 CSS：浅色 Windows 风格
    CSS = """
    Screen {
        background: $background;
        color: $foreground;
    }

    Header {
        background: $primary;
        color: white;
        text-style: bold;
    }

    Footer {
        background: $secondary;
        color: white;
    }

    /* ── 总体布局 ── */
    #layout {
        height: 100%;
        padding: 0 1;
    }

    /* ── 左侧边栏 ── */
    #sidebar {
        width: 40;
        min-width: 36;
        padding: 1 1;
        background: $panel;
        border-right: solid $primary;
    }

    /* ── 右侧工作区 ── */
    #workspace {
        padding: 1 1;
        background: $surface;
    }

    /* ── 分区标题 ── */
    .sec-title {
        text-style: bold;
        color: $primary;
        margin-top: 1;
        margin-bottom: 0;
        padding: 0 0;
    }

    .sec-divider {
        color: $primary;
        margin-top: 0;
        margin-bottom: 1;
    }

    /* ── 输入框 ── */
    Input {
        background: white;
        border: tall $border;
        margin-bottom: 1;
        color: $foreground;
    }

    Input:focus {
        border: tall $primary;
    }

    /* ── Select 下拉 ── */
    Select {
        margin-bottom: 1;
        background: white;
        border: tall $border;
    }

    Select:focus {
        border: tall $primary;
    }

    /* ── 复选框：使用原生 Checkbox，label 颜色调亮 ── */
    Checkbox {
        margin-bottom: 1;
        color: $foreground;
        background: transparent;
    }

    /* ── 主操作按钮行（固定显示） ── */
    #action-row {
        height: 3;
        margin-bottom: 1;
        dock: bottom;
    }

    #action-row Button {
        width: 1fr;
        margin-right: 1;
    }

    #action-row Button:last-of-type {
        margin-right: 0;
    }

    /* ── 侧边栏其他按钮 ── */
    .tool-btn {
        width: 100%;
        margin-bottom: 1;
        background: $panel;
        border: tall $border;
        color: $foreground;
    }

    .tool-btn:hover {
        background: $primary;
        color: white;
    }

    /* ── 格式快捷输入行 ── */
    .fmt-row {
        height: 3;
        margin-bottom: 1;
    }

    .fmt-row Input {
        width: 1fr;
        margin-right: 1;
        margin-bottom: 0;
    }

    .fmt-row Input:last-of-type {
        margin-right: 0;
    }

    /* ── 摘要区 ── */
    #summary {
        height: 7;
        padding: 1;
        margin-bottom: 1;
        border: solid $primary;
        background: #eaf4fb;
        color: $foreground;
    }

    /* ── 结果表格 ── */
    #results {
        height: 1fr;
        border: solid $border;
        background: white;
    }

    DataTable > .datatable--cursor {
        background: $primary 30%;
    }

    DataTable > .datatable--header {
        background: $secondary;
        color: white;
        text-style: bold;
    }

    /* ── 状态栏 ── */
    #status {
        height: 2;
        padding: 0 1;
        background: #e8e8e8;
        color: $foreground;
        border-top: solid $border;
    }
    """

    BINDINGS = [
        ('p', 'preview', '预览'),
        ('e', 'execute', '执行'),
        ('u', 'undo', '撤销'),
        ('q', 'quit', '退出'),
    ]

    def __init__(self):
        super().__init__()
        self._rule_suggestions: list = []
        self._format_profiles: list = []
        self._history_rows: list = []
        self._last_csv_path: str = ''   # 最近一次执行生成的 CSV，供撤销使用

    # ────────────────────────────────────────────────────────
    # compose — 界面布局
    # ────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        opts, cur_fmt = _build_format_options()

        with Horizontal(id='layout'):
            # ── 左侧边栏 ────────────────────────────────────
            with Vertical(id='sidebar'):
                yield Label('▶ 源文件夹', classes='sec-title')
                yield Input(
                    placeholder='拖拽或粘贴文件夹路径',
                    id='source_input',
                )
                yield Checkbox('包含子目录', value=True, id='recursive_checkbox')

                yield Label('▶ 输出格式', classes='sec-title')
                yield Select(
                    options=opts,
                    value=cur_fmt,
                    id='format_select',
                    allow_blank=False,
                )

                yield Label('─────── 工具 ───────', classes='sec-divider')
                yield Button('🔍 未知规则发现', id='rules_button', classes='tool-btn')
                yield Button('📋 格式管理',     id='formats_button', classes='tool-btn')
                yield Button('📁 历史报告',     id='history_button', classes='tool-btn')

                # 格式快捷新增（默认折叠，点格式管理后可用）
                yield Label('▶ 新增自定义格式', classes='sec-title', id='fmt_sec_label')
                with Horizontal(classes='fmt-row'):
                    yield Input(placeholder='格式名称', id='fmt_name_input')
                    yield Input(placeholder='如 %Y%m%d_%H%M', id='fmt_expr_input')
                yield Button('💾 保存格式', id='save_format_button', classes='tool-btn')
                yield Button('✅ 设为当前格式', id='set_current_button', classes='tool-btn')

                # ── 固定操作按钮（dock=bottom 模拟固定） ────
                with Horizontal(id='action-row'):
                    yield Button('▶ 预览 [P]',   id='preview_button',  variant='primary')
                    yield Button('✔ 执行 [E]',   id='execute_button',  variant='success')
                    yield Button('↩ 撤销 [U]',   id='undo_button',     variant='warning')

            # ── 右侧工作区 ──────────────────────────────────
            with Vertical(id='workspace'):
                yield Static(
                    '就绪。\n在左侧填入源文件夹后按 P 预览，E 执行重命名。\n执行后可按 U 撤销最近一次操作。',
                    id='summary',
                )
                table = DataTable(id='results', cursor_type='row')
                table.add_columns('状态', '原文件名', '新文件名', '日期', '规则来源', '错误原因')
                yield table
                yield Static('快捷键：P 预览  E 执行  U 撤销  Q 退出', id='status')

        yield Footer()

    # ────────────────────────────────────────────────────────
    # 应用启动后注册主题
    # ────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.register_theme(WIN_THEME)
        self.theme = 'win-light'

    # ────────────────────────────────────────────────────────
    # 内部辅助
    # ────────────────────────────────────────────────────────

    def _status(self, msg: str):
        self.query_one('#status', Static).update(msg)

    def _summary(self, msg: str):
        self.query_one('#summary', Static).update(msg)

    def _get_source(self) -> str:
        return self.query_one('#source_input', Input).value.strip().strip('"').strip("'")

    def _get_format(self) -> str:
        val = self.query_one('#format_select', Select).value
        return str(val) if val else ''

    def _get_recursive(self) -> bool:
        return self.query_one('#recursive_checkbox', Checkbox).value

    def _set_result_columns(self, *columns: str) -> DataTable:
        table = self.query_one('#results', DataTable)
        table.clear(columns=True)
        table.add_columns(*columns)
        return table

    def _render_rename_results(self, results: list):
        table = self._set_result_columns('状态', '原文件名', '新文件名', '日期', '规则来源', '错误原因')
        for row in results[:2000]:
            status = str(row.get('status', ''))
            orig   = Path(str(row.get('original', ''))).name
            new_n  = str(row.get('new_name', ''))
            date   = str(row.get('date', '') or '')[:16]
            src    = str(row.get('source', ''))
            err    = str(row.get('error', '') or '')
            table.add_row(status, orig, new_n, date, src, err)

    def _make_options(self, mode: str) -> RenameJobOptions:
        """构建 RenameJobOptions，CSV 路径留空（由服务层自动放到目标目录）。"""
        return RenameJobOptions(
            source_dir=self._get_source(),
            mode=mode,
            recursive=self._get_recursive(),
            fmt_arg=self._get_format(),
            csv_path='',   # 默认：服务层自动生成到目标目录
        )

    # ────────────────────────────────────────────────────────
    # 预览 / 执行
    # ────────────────────────────────────────────────────────

    def _run_job(self, mode: str):
        source = self._get_source()
        if not source:
            self._status('错误：请先填写源文件夹路径。')
            return
        self._status(f'{"预览" if mode=="preview" else "执行"}中，请稍候…')
        try:
            summary = run_rename_job(self._make_options(mode))
        except Exception as e:
            self._status(f'错误：{e}')
            return

        ok      = summary.get('ok_count', 0)
        err     = summary.get('error_count', 0)
        total   = summary.get('files_count', 0)
        csv_p   = summary.get('csv_path', '')
        hist_p  = summary.get('history_path', '')

        if csv_p:
            self._last_csv_path = csv_p

        label = '预览' if mode == 'preview' else '执行重命名'
        self._summary(
            f'{label} 完成\n'
            f'扫描 {total} 个文件  ✓ 成功 {ok}  ✗ 冲突/错误 {err}\n'
            f'CSV 日志: {csv_p or "（预览模式不生成 CSV）"}\n'
            f'历史记录: {hist_p or "—"}'
        )
        self._render_rename_results(summary.get('results', []))
        self._status(f'{label}完成。按 U 可撤销最近一次执行。')

    def action_preview(self):
        self._run_job('preview')

    def action_execute(self):
        self._run_job('execute')

    # ────────────────────────────────────────────────────────
    # 撤销
    # ────────────────────────────────────────────────────────

    def action_undo(self):
        csv_path = self._last_csv_path
        if not csv_path:
            self._status('暂无可撤销记录。请先执行一次重命名，或从历史报告选取 CSV 路径。')
            return
        self._status(f'撤销中：{csv_path}')
        try:
            summary     = undo_from_csv(csv_path)
            report_path = write_undo_report(csv_path, summary['details'])
        except Exception as e:
            self._status(f'撤销失败：{e}')
            return

        rows     = summary.get('rows', 0)
        restored = summary.get('restored', 0)
        skipped  = summary.get('skipped', 0)
        errors   = summary.get('errors', 0)
        self._summary(
            f'撤销完成\n'
            f'记录 {rows} 条  ✓ 恢复 {restored}  跳过 {skipped}  ✗ 错误 {errors}\n'
            f'撤销报告: {report_path}'
        )
        table = self._set_result_columns('状态', '目标路径', '已恢复为', '说明')
        for d in summary.get('details', [])[:2000]:
            table.add_row(
                str(d.get('status', '')),
                str(d.get('dst', '') or d.get('new_name', '')),
                str(d.get('original', '')),
                str(d.get('undo_error', '') or ''),
            )
        self._status('撤销完成。详情见右侧表格。')
        self._last_csv_path = ''   # 已撤销，清空记录

    # ────────────────────────────────────────────────────────
    # 按钮事件 — 主操作
    # ────────────────────────────────────────────────────────

    @on(Button.Pressed, '#preview_button')
    def _on_preview(self):
        self.action_preview()

    @on(Button.Pressed, '#execute_button')
    def _on_execute(self):
        self.action_execute()

    @on(Button.Pressed, '#undo_button')
    def _on_undo(self):
        self.action_undo()

    # ────────────────────────────────────────────────────────
    # 未知规则发现
    # ────────────────────────────────────────────────────────

    @on(Button.Pressed, '#rules_button')
    def _on_rules(self):
        source = self._get_source()
        if not source:
            self._status('错误：规则发现需要先填写源文件夹。')
            return
        self._status('扫描未知规则中…')
        try:
            suggestions = discover_rule_suggestions(source, recursive=self._get_recursive())
        except Exception as e:
            self._status(f'规则发现失败：{e}')
            return

        self._rule_suggestions = suggestions
        table = self._set_result_columns('签名', '数量', '示例文本', '建议正则', '操作提示')
        for item in suggestions[:200]:
            example = item['examples'][0]['match_text'] if item.get('examples') else ''
            regex   = (item.get('suggestion') or {}).get('regex', '')
            table.add_row(
                item['signature'],
                str(item['count']),
                example,
                regex,
                '← 选中后点"加入规则"',
            )
        self._summary(
            f'规则发现完成\n'
            f'发现 {len(suggestions)} 种未匹配规则候选。\n'
            f'选中某行，点"加入选中规则"写入 patterns.json。'
        )
        self._status('候选规则已加载。选中行后点"加入选中规则"。')

        # 动态注入"加入规则"按钮
        if not self.query('#add_rule_button'):
            btn = Button('➕ 加入选中规则', id='add_rule_button', classes='tool-btn', variant='warning')
            self.query_one('#sidebar').mount(btn, before=self.query_one('#rules_button'))

    @on(Button.Pressed, '#add_rule_button')
    def _on_add_rule(self):
        table = self.query_one('#results', DataTable)
        if table.cursor_row is None or table.cursor_row < 0:
            self._status('请先在表格中点击选中一行候选规则。')
            return
        suggestions = self._rule_suggestions
        idx = table.cursor_row
        if idx >= len(suggestions):
            self._status('选中行超出候选列表范围。')
            return
        sig = suggestions[idx]['signature']
        try:
            result = add_pattern_suggestion(sig)
        except Exception as e:
            self._status(f'写入规则失败：{e}')
            return
        if result is None:
            self._status(f'无法为签名 [{sig}] 生成规则（格式不可识别）。')
            return
        self._status(f'✓ 规则 [{sig}] 已写入 patterns.json（id={result["id"]}）。')
        try:
            table.remove_row(table.cursor_row_key)
        except Exception:
            pass
        if suggestions:
            suggestions.pop(idx)

    # ────────────────────────────────────────────────────────
    # 格式管理
    # ────────────────────────────────────────────────────────

    @on(Button.Pressed, '#formats_button')
    def _on_formats(self):
        try:
            profiles = load_format_profiles()
        except Exception as e:
            self._status(f'格式加载失败：{e}')
            return

        self._format_profiles = profiles
        table = self._set_result_columns('当前', '名称', '格式表达式', '类型', '示例')
        from datetime import datetime
        sample = datetime(2024, 6, 15, 14, 30, 0)
        for p in profiles:
            is_cur = '✓' if p.get('current') else ''
            kind   = '内置' if p.get('builtin') else '自定义'
            try:
                example = sample.strftime(p['format'])
            except Exception:
                example = ''
            table.add_row(is_cur, p.get('name', ''), p.get('format', ''), kind, example)

        self._summary(
            f'文件名格式管理\n'
            f'共 {len(profiles)} 个格式（含内置）。\n'
            f'选中行后可填充到左侧输入框，点"保存格式"或"设为当前格式"。'
        )
        self._status('格式列表已加载。选中行后可编辑或设为当前。')

    @on(DataTable.RowSelected, '#results')
    def _on_row_selected(self, event: DataTable.RowSelected):
        """选中格式行时自动填充名称/表达式输入框（仅格式管理模式有效）。"""
        profiles = self._format_profiles
        if not profiles:
            return
        idx = event.cursor_row
        if idx is not None and 0 <= idx < len(profiles):
            p = profiles[idx]
            self.query_one('#fmt_name_input', Input).value = p.get('name', '')
            self.query_one('#fmt_expr_input', Input).value = p.get('format', '')

    @on(Button.Pressed, '#save_format_button')
    def _on_save_format(self):
        name = self.query_one('#fmt_name_input', Input).value.strip()
        expr = self.query_one('#fmt_expr_input', Input).value.strip()
        if not name or not expr:
            self._status('错误：格式名称和表达式均不能为空。')
            return
        try:
            save_format_profile(name, expr, make_current=False)
        except Exception as e:
            self._status(f'保存失败：{e}')
            return
        self._status(f'✓ 格式 [{name}] 已保存。点"设为当前格式"立即启用。')
        self._on_formats()
        self._refresh_format_select()

    @on(Button.Pressed, '#set_current_button')
    def _on_set_current(self):
        name = self.query_one('#fmt_name_input', Input).value.strip()
        expr = self.query_one('#fmt_expr_input', Input).value.strip()
        if not name or not expr:
            self._status('错误：名称和表达式不能为空。请先选中或手动填写。')
            return
        try:
            save_format_profile(name, expr, make_current=True)
        except Exception as e:
            self._status(f'设置失败：{e}')
            return
        self._status(f'✓ 格式 [{name}] 已设为当前。')
        self._on_formats()
        self._refresh_format_select()

    def _refresh_format_select(self):
        """重新加载格式列表到 Select 下拉框，并标记当前选中项。"""
        try:
            opts, cur = _build_format_options()
            sel = self.query_one('#format_select', Select)
            sel.set_options(opts)
            if cur:
                sel.value = cur
        except Exception:
            pass

    # ────────────────────────────────────────────────────────
    # 历史报告
    # ────────────────────────────────────────────────────────

    @on(Button.Pressed, '#history_button')
    def _on_history(self):
        try:
            rows = load_history_reports()
        except Exception as e:
            self._status(f'历史加载失败：{e}')
            return

        self._history_rows = rows
        table = self._set_result_columns('时间', '模式', '文件夹', '文件数', '成功', '错误', 'CSV 路径')
        for row in rows[-500:]:
            table.add_row(
                row.get('timestamp', ''),
                row.get('mode', ''),
                row.get('folder', ''),
                str(row.get('files_count', '')),
                str(row.get('ok_count', '')),
                str(row.get('error_count', '')),
                row.get('csv_path', ''),
            )
        self._summary(
            f'历史报告\n'
            f'共 {len(rows)} 条记录。\n'
            f'选中行后点"打开 CSV 目录"跳转到文件管理器。'
        )
        self._status('历史报告已加载。选中行后可打开 CSV 所在目录。')

        if not self.query('#open_folder_button'):
            btn = Button('📂 打开 CSV 目录', id='open_folder_button', classes='tool-btn')
            self.query_one('#sidebar').mount(btn, before=self.query_one('#history_button'))

    @on(Button.Pressed, '#open_folder_button')
    def _on_open_folder(self):
        table   = self.query_one('#results', DataTable)
        history = self._history_rows
        idx     = table.cursor_row
        if idx is None or idx < 0:
            self._status('请先在历史表格中点击选中一行。')
            return
        offset   = max(0, len(history) - 500)
        real_idx = offset + idx
        if real_idx >= len(history):
            self._status('选中行超出历史范围。')
            return
        csv_path = history[real_idx].get('csv_path', '')
        if not csv_path:
            self._status('该记录没有 CSV 路径信息。')
            return
        err = open_folder(csv_path)
        if err:
            self._status(f'无法打开目录：{err}')
        else:
            self._status(f'✓ 已打开目录: {Path(csv_path).parent}')


# ────────────────────────────────────────────────────────────
# 入口
# ────────────────────────────────────────────────────────────

def main():
    app = PhotoRenamerApp()
    app.run()


if __name__ == '__main__':
    main()
