#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Textual UI for Photo & Video Renamer.

This module is an optional frontend. The core rename/undo behavior remains in
photo_renamer.py so CLI and TUI share the same tested service layer.

Usage:
    python photo_renamer.py --tui
    python photo_renamer_tui.py  (direct launch, dev mode)

Keyboard shortcuts:
    P  - 预览
    E  - 执行重命名
    U  - 撤销
    Q  - 退出
"""

import os
import subprocess
import sys
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Label, Static

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


def open_folder(path: str) -> str:
    """Open the folder containing path in the system file manager.
    Returns an error message string if it fails, otherwise empty string.
    """
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


class PhotoRenamerTuiApp(App):
    """Terminal UI shell for preview, execute, undo, rules, formats, and history workflows."""

    CSS = """
    Screen {
        background: $surface;
        color: $text;
    }

    #layout {
        height: 100%;
        padding: 1 1;
    }

    #sidebar {
        width: 36;
        padding: 1 1;
        border: solid $primary-darken-2;
        background: $panel;
    }

    #workspace {
        padding: 1 1;
        border: solid $primary-darken-2;
        background: $panel;
    }

    .section-title {
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }

    .subsection-title {
        text-style: bold;
        color: $secondary;
        margin-top: 1;
        margin-bottom: 0;
    }

    Input {
        margin-bottom: 1;
    }

    Checkbox {
        margin-bottom: 1;
    }

    Button {
        width: 100%;
        margin-bottom: 1;
    }

    Button.action-btn {
        margin-bottom: 0;
    }

    #btn-row {
        height: 3;
        margin-bottom: 1;
    }

    #btn-row Button {
        width: 1fr;
        margin-right: 1;
        margin-bottom: 0;
    }

    #btn-row Button:last-of-type {
        margin-right: 0;
    }

    #summary {
        height: 6;
        padding: 1;
        margin-bottom: 1;
        border: round $primary-darken-3;
        background: $surface;
        color: $text-muted;
    }

    #results {
        height: 1fr;
        border: round $primary-darken-3;
    }

    #status {
        height: 3;
        padding: 1;
        border: round $accent-darken-2;
        background: $surface;
        color: $text-muted;
    }

    .format-input-row {
        height: 3;
        margin-bottom: 1;
    }

    .format-input-row Input {
        width: 1fr;
        margin-right: 1;
        margin-bottom: 0;
    }

    .format-input-row Input:last-of-type {
        margin-right: 0;
    }
    """

    BINDINGS = [
        ('p', 'preview', '预览'),
        ('e', 'execute', '重命名'),
        ('u', 'undo', '撤销'),
        ('q', 'quit', '退出'),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id='layout'):
            with Vertical(id='sidebar'):
                yield Label('Photo & Video Renamer', classes='section-title')

                # --- 源文件夹 & 选项 ---
                yield Input(placeholder='源文件夹路径（可拖拽）', id='source_input')
                yield Checkbox('包含子目录', value=True, id='recursive_checkbox')
                yield Input(placeholder='输出格式，留空=默认  例: %Y%m%d_%H%M%S', id='format_input')
                yield Input(placeholder='CSV 日志路径，留空=自动', id='csv_input')

                # --- 主操作按钮 ---
                with Horizontal(id='btn-row'):
                    yield Button('预览 [P]', id='preview_button', variant='primary', classes='action-btn')
                    yield Button('执行 [E]', id='execute_button', variant='success', classes='action-btn')
                yield Button('撤销 CSV [U]', id='undo_button', variant='warning')

                # --- 功能按钮 ---
                yield Label('───── 工具 ─────', classes='subsection-title')
                yield Button('未知规则发现', id='rules_button')
                yield Button('格式管理', id='formats_button')
                yield Button('历史报告', id='history_button')

                # --- 格式管理快捷输入（格式管理面板激活时显示） ---
                yield Label('─ 新增格式 ─', classes='subsection-title', id='fmt_section_label')
                with Horizontal(classes='format-input-row'):
                    yield Input(placeholder='格式名称', id='fmt_name_input')
                    yield Input(placeholder='表达式 如 %Y%m%d_%H%M', id='fmt_expr_input')
                yield Button('保存格式', id='save_format_button', variant='default')
                yield Button('设为当前格式', id='set_current_button', variant='default')

            with Vertical(id='workspace'):
                yield Static(
                    '等待操作。\n填写源文件夹后按 P 预览，E 执行重命名；\n填写 CSV 路径后按 U 撤销。',
                    id='summary'
                )
                table = DataTable(id='results', cursor_type='row')
                table.add_columns('状态', '原文件名', '新文件名', '日期', '规则来源', '错误原因')
                yield table
                yield Static(
                    '快捷键：P 预览  E 重命名  U 撤销  Q 退出',
                    id='status'
                )
        yield Footer()

    # ──────────────────────────────────────────────
    # 内部辅助
    # ──────────────────────────────────────────────

    def _set_status(self, message: str):
        self.query_one('#status', Static).update(message)

    def _set_summary(self, message: str):
        self.query_one('#summary', Static).update(message)

    def _options(self, mode: str) -> RenameJobOptions:
        source = self.query_one('#source_input', Input).value.strip().strip('"').strip("'")
        fmt = self.query_one('#format_input', Input).value.strip()
        csv_path = self.query_one('#csv_input', Input).value.strip()
        recursive = self.query_one('#recursive_checkbox', Checkbox).value
        return RenameJobOptions(
            source_dir=source,
            mode=mode,
            recursive=recursive,
            fmt_arg=fmt,
            csv_path=csv_path,
        )

    def _set_result_columns(self, *columns: str) -> DataTable:
        table = self.query_one('#results', DataTable)
        table.clear(columns=True)
        table.add_columns(*columns)
        return table

    def _render_rename_results(self, results: list):
        """渲染重命名/预览结果，含日期、规则来源、错误原因等完整字段。"""
        table = self._set_result_columns('状态', '原文件名', '新文件名', '日期', '规则来源', '错误原因')
        for row in results[:1000]:
            status = str(row.get('status', ''))
            orig = Path(row.get('original', '')).name
            new_name = str(row.get('new_name', ''))
            date = str(row.get('date', '') or '')[:16]  # 截断到分钟
            source = str(row.get('source', ''))
            error = str(row.get('error', '') or '')
            table.add_row(status, orig, new_name, date, source, error)

    def _run_job(self, mode: str):
        source = self.query_one('#source_input', Input).value.strip().strip('"').strip("'")
        if not source:
            self._set_status('错误：请先填写源文件夹路径。')
            return
        try:
            summary = run_rename_job(self._options(mode))
        except Exception as e:
            self._set_status(f'错误：{e}')
            return

        ok = summary['ok_count']
        err = summary['error_count']
        total = summary['files_count']
        csv_p = summary.get('csv_path', '')
        history_p = summary.get('history_path', '')

        label = {'preview': '预览', 'execute': '执行重命名'}.get(mode, mode)
        self._set_summary(
            f'{label} 完成\n'
            f'扫描 {total} 个文件  ✓ 成功 {ok}  ✗ 冲突/错误 {err}\n'
            f'CSV 日志: {csv_p}\n'
            f'历史记录: {history_p or "—"}'
        )
        self._render_rename_results(summary['results'])
        self._set_status(f'{label} 完成。如需撤销，将上方 CSV 路径填入"CSV 日志"框后按 U。')

    # ──────────────────────────────────────────────
    # 快捷键动作
    # ──────────────────────────────────────────────

    def action_preview(self):
        self._run_job('preview')

    def action_execute(self):
        self._run_job('execute')

    def action_undo(self):
        csv_path = self.query_one('#csv_input', Input).value.strip()
        if not csv_path:
            self._set_status('错误：撤销需要先填写 CSV 日志路径。')
            return
        try:
            summary = undo_from_csv(csv_path)
            report_path = write_undo_report(csv_path, summary['details'])
        except Exception as e:
            self._set_status(f'撤销失败：{e}')
            return

        rows = summary['rows']
        restored = summary['restored']
        skipped = summary['skipped']
        errors = summary['errors']
        self._set_summary(
            f'撤销 完成\n'
            f'记录 {rows} 条  ✓ 恢复 {restored}  跳过 {skipped}  ✗ 错误 {errors}\n'
            f'撤销报告: {report_path}'
        )
        # 渲染撤销详情
        table = self._set_result_columns('状态', '目标路径', '已恢复为', '说明')
        for d in summary['details'][:1000]:
            table.add_row(
                str(d.get('status', '')),
                str(d.get('dst', '') or d.get('new_name', '')),
                str(d.get('original', '')),
                str(d.get('undo_error', '') or ''),
            )
        self._set_status('撤销完成。跳过和错误原因见最右列。')

    # ──────────────────────────────────────────────
    # 按钮事件
    # ──────────────────────────────────────────────

    @on(Button.Pressed, '#preview_button')
    def on_preview_button(self):
        self.action_preview()

    @on(Button.Pressed, '#execute_button')
    def on_execute_button(self):
        self.action_execute()

    @on(Button.Pressed, '#undo_button')
    def on_undo_button(self):
        self.action_undo()

    # ── 未知规则发现 ──

    @on(Button.Pressed, '#rules_button')
    def on_rules_button(self):
        source = self.query_one('#source_input', Input).value.strip().strip('"').strip("'")
        if not source:
            self._set_status('错误：规则发现需要先填写源文件夹。')
            return
        recursive = self.query_one('#recursive_checkbox', Checkbox).value
        try:
            suggestions = discover_rule_suggestions(source, recursive=recursive)
        except Exception as e:
            self._set_status(f'规则发现失败：{e}')
            return

        # 保存候选列表供后续选中操作使用
        self._rule_suggestions = suggestions

        table = self._set_result_columns('签名', '数量', '示例文本', '建议正则', '操作')
        for item in suggestions[:200]:
            example = item['examples'][0]['match_text'] if item['examples'] else ''
            regex = (item.get('suggestion') or {}).get('regex', '')
            table.add_row(
                item['signature'],
                str(item['count']),
                example,
                regex,
                '← 选中行后点"加入规则"',
            )

        self._set_summary(
            f'规则发现完成\n'
            f'发现 {len(suggestions)} 种未知规则候选。\n'
            f'选中某行后，点击"加入规则"按钮将其写入 patterns.json。'
        )
        self._set_status('规则候选已加载。选中一行后可加入规则。')

        # 动态注入"加入规则"按钮（如尚未存在）
        sidebar = self.query_one('#sidebar')
        if not self.query('#add_rule_button'):
            btn = Button('加入选中规则', id='add_rule_button', variant='warning')
            sidebar.mount(btn, before=self.query_one('#rules_button'))

    @on(Button.Pressed, '#add_rule_button')
    def on_add_rule_button(self):
        table = self.query_one('#results', DataTable)
        if table.cursor_row is None or table.cursor_row < 0:
            self._set_status('请先在表格中点击选中一行候选规则。')
            return
        suggestions = getattr(self, '_rule_suggestions', [])
        idx = table.cursor_row
        if idx >= len(suggestions):
            self._set_status('选中行超出候选列表范围。')
            return

        sig = suggestions[idx]['signature']
        try:
            result = add_pattern_suggestion(sig)
        except Exception as e:
            self._set_status(f'写入规则失败：{e}')
            return

        if result is None:
            self._set_status(f'无法为签名 [{sig}] 生成规则（格式不可识别）。')
            return

        self._set_status(
            f'已将规则 [{sig}] 写入 patterns.json（id={result["id"]}）。重新扫描可刷新候选列表。'
        )
        # 从表格中移除已添加行，防止重复
        try:
            table.remove_row(table.cursor_row_key)
        except Exception:
            pass
        if suggestions:
            suggestions.pop(idx)

    # ── 格式管理 ──

    @on(Button.Pressed, '#formats_button')
    def on_formats_button(self):
        try:
            profiles = load_format_profiles()
        except Exception as e:
            self._set_status(f'格式加载失败：{e}')
            return

        # 保存供选中后填充
        self._format_profiles = profiles

        table = self._set_result_columns('当前', '名称', '格式表达式', '类型', '示例')
        for profile in profiles:
            is_current = '✓' if profile.get('current') else ''
            kind = '内置' if profile.get('builtin') else '自定义'
            # 生成示例（用固定时间演示格式）
            try:
                from datetime import datetime
                sample_dt = datetime(2024, 6, 15, 14, 30, 0)
                example = sample_dt.strftime(profile['format'])
            except Exception:
                example = ''
            table.add_row(is_current, profile.get('name', ''), profile.get('format', ''), kind, example)

        self._set_summary(
            f'文件名格式管理\n'
            f'共 {len(profiles)} 个格式（含内置）。\n'
            f'选中某行可填充到下方输入框，再点"保存格式"或"设为当前格式"。'
        )
        self._set_status('格式列表已加载。选中行后可填充并保存或设为当前。')

    @on(DataTable.RowSelected, '#results')
    def on_result_row_selected(self, event: DataTable.RowSelected):
        """选中格式列表中的行时，自动填充到格式名称/表达式输入框。"""
        profiles = getattr(self, '_format_profiles', None)
        if profiles is None:
            return
        idx = event.cursor_row
        if idx is not None and 0 <= idx < len(profiles):
            p = profiles[idx]
            self.query_one('#fmt_name_input', Input).value = p.get('name', '')
            self.query_one('#fmt_expr_input', Input).value = p.get('format', '')

    @on(Button.Pressed, '#save_format_button')
    def on_save_format_button(self):
        name = self.query_one('#fmt_name_input', Input).value.strip()
        expr = self.query_one('#fmt_expr_input', Input).value.strip()
        if not name or not expr:
            self._set_status('错误：格式名称和表达式均不能为空。')
            return
        try:
            save_format_profile(name, expr, make_current=False)
        except Exception as e:
            self._set_status(f'保存格式失败：{e}')
            return
        self._set_status(f'格式 [{name}] 已保存。点击"设为当前格式"可立即启用。')
        # 刷新格式列表
        self.on_formats_button()

    @on(Button.Pressed, '#set_current_button')
    def on_set_current_button(self):
        name = self.query_one('#fmt_name_input', Input).value.strip()
        expr = self.query_one('#fmt_expr_input', Input).value.strip()
        if not name or not expr:
            self._set_status('错误：格式名称和表达式均不能为空。先选中一个格式或手动填写。')
            return
        try:
            save_format_profile(name, expr, make_current=True)
        except Exception as e:
            self._set_status(f'设置当前格式失败：{e}')
            return
        # 同步到主界面的格式输入框
        self.query_one('#format_input', Input).value = expr
        self._set_status(f'格式 [{name}] 已设为当前格式，将用于下次预览/执行。')
        # 刷新格式列表
        self.on_formats_button()

    # ── 历史报告 ──

    @on(Button.Pressed, '#history_button')
    def on_history_button(self):
        try:
            rows = load_history_reports()
        except Exception as e:
            self._set_status(f'历史加载失败：{e}')
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

        self._set_summary(
            f'历史报告\n'
            f'共 {len(rows)} 条记录。\n'
            f'选中某行后，可点击"打开 CSV 所在目录"跳转到文件管理器。'
        )
        self._set_status('历史报告已加载。选中行后可打开 CSV 所在目录。')

        # 动态注入"打开目录"按钮（如尚未存在）
        sidebar = self.query_one('#sidebar')
        if not self.query('#open_folder_button'):
            btn = Button('打开 CSV 所在目录', id='open_folder_button', variant='default')
            sidebar.mount(btn, before=self.query_one('#history_button'))

    @on(Button.Pressed, '#open_folder_button')
    def on_open_folder_button(self):
        table = self.query_one('#results', DataTable)
        history = getattr(self, '_history_rows', [])
        # DataTable cursor_row 在历史模式下对应 rows[-500:]，取最近 500 条的索引
        idx = table.cursor_row
        if idx is None or idx < 0:
            self._set_status('请先在历史表格中点击选中一行。')
            return
        offset = max(0, len(history) - 500)
        real_idx = offset + idx
        if real_idx >= len(history):
            self._set_status('选中行超出历史范围。')
            return
        csv_path = history[real_idx].get('csv_path', '')
        if not csv_path:
            self._set_status('该记录没有 CSV 路径信息。')
            return
        err = open_folder(csv_path)
        if err:
            self._set_status(f'无法打开目录：{err}')
        else:
            self._set_status(f'已打开 CSV 所在目录: {Path(csv_path).parent}')


def main():
    app = PhotoRenamerTuiApp()
    app.run()


if __name__ == '__main__':
    main()
