#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Textual UI for Photo & Video Renamer  v2.10

视觉风格：深色终端 IDE 风格（参照 rich-vs-textual-cli.html 设计稿）
- 深色背景 #111720 + 深蓝侧边栏 #141b25 + 亮蓝高亮 #7fa7ff
- 选中行：左侧蓝条 + 深色高亮（类 VS Code）
- Checkbox 勾选状态显示 ✓ 而非 X（重写 BUTTON_INNER）
- 输出格式下拉，选中时右侧摘要区实时预览重命名示例
- CSV 路径不再手动填写，默认存到目标目录；子目录各自生成独立 CSV
- 预览 / 执行 / 撤销 固定实体按钮，始终可见
- 所有 Input/Select 宽度限制在侧边栏内，不越界

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
from datetime import datetime
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.theme import Theme
from textual.widgets import (
    Button, DataTable, Footer, Header,
    Input, Label, Select, Static,
)
from textual.widgets import Checkbox as _BaseCheckbox

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


# ── 修复 Checkbox 显示 ✓ 而非 X ────────────────────────────────────────────
class Checkbox(_BaseCheckbox):
    """覆盖 BUTTON_INNER，让勾选框显示 ✓ 而非 X。"""
    BUTTON_INNER: str = "✓"


# ── 深色 IDE 主题（参照 rich-vs-textual-cli.html 设计稿） ───────────────────
#   背景     #111720   侧边栏   #141b25   顶底栏   #0d1219
#   高亮蓝   #7fa7ff   选中行   #1f2f45   表头     #222b37
#   面板     #18212c   边框     #29323d   文字     #dce4ec
#   暗文字   #8291a2   绿色     #71d69c   琥珀     #e5b15f
#   错误红   #ee7777
DARK_THEME = Theme(
    name='ide-dark',
    primary='#7fa7ff',          # 亮蓝高亮
    secondary='#65a8f2',        # 蓝色变体
    accent='#7fa7ff',
    background='#111720',       # 最深背景
    surface='#141b25',          # 工作区/侧边栏
    panel='#18212c',            # 卡片/表头
    error='#ee7777',
    success='#71d69c',
    warning='#e5b15f',
    foreground='#dce4ec',       # 主文字
    dark=True,
    variables={
        'text': '#dce4ec',
        'text-muted': '#8291a2',
        'border': '#29323d',
        'input-background': '#18212c',
        'input-foreground': '#dce4ec',
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


def _build_format_options() -> tuple[list[tuple[str, str]], str]:
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


def _format_example(fmt: str) -> str:
    """用当前时间生成格式示例，如 2024.06.15_1430。"""
    try:
        now = datetime.now()
        return now.strftime(fmt)
    except Exception:
        return f'（无效格式: {fmt}）'


class PhotoRenamerApp(App):
    """Photo & Video Renamer — 深色 IDE 风格 TUI"""

    TITLE = 'Photo & Video Renamer  v2.10'
    CSS_PATH = None

    # 内联 CSS：深色 IDE 风格（对照 rich-vs-textual-cli.html）
    CSS = """
    Screen {
        background: #111720;
        color: #dce4ec;
    }

    Header {
        background: #0d1219;
        color: #a7b4c2;
        text-style: bold;
        border-bottom: solid #29323d;
    }

    Footer {
        background: #0d1219;
        color: #a7b4c2;
        border-top: solid #29323d;
    }

    /* ── 总体布局 ── */
    #layout {
        height: 100%;
        padding: 0;
    }

    /* ── 左侧边栏 ── */
    #sidebar {
        width: 38;
        min-width: 34;
        max-width: 40;
        padding: 1 1;
        background: #141b25;
        border-right: solid #29323d;
        overflow-y: auto;
        overflow-x: hidden;
    }

    /* ── 侧边栏内所有输入组件最大宽度限制，防止越界 ── */
    #sidebar Input {
        width: 100%;
        max-width: 36;
        margin-bottom: 1;
        background: #18212c;
        border: tall #343f4d;
        color: #dce4ec;
    }

    #sidebar Input:focus {
        border: tall #7fa7ff;
    }

    #sidebar Select {
        width: 100%;
        max-width: 36;
        margin-bottom: 1;
        background: #18212c;
        border: tall #343f4d;
        color: #dce4ec;
    }

    #sidebar Select:focus {
        border: tall #7fa7ff;
    }

    #sidebar Checkbox {
        width: 100%;
        max-width: 36;
        margin-bottom: 1;
        color: #c9d4df;
        background: transparent;
    }

    /* ── 右侧工作区 ── */
    #workspace {
        padding: 1 1;
        background: #111720;
    }

    /* ── 分区标题 ── */
    .sec-title {
        text-style: bold;
        color: #7fa7ff;
        margin-top: 1;
        margin-bottom: 0;
        padding: 0 0;
        width: 100%;
    }

    .sec-divider {
        color: #29323d;
        margin-top: 0;
        margin-bottom: 1;
        width: 100%;
    }

    /* ── 主操作按钮行（固定显示） ── */
    #action-row {
        height: 3;
        margin-top: 1;
        margin-bottom: 0;
        dock: bottom;
        width: 100%;
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
        max-width: 36;
        margin-bottom: 1;
        background: #1d2633;
        border: tall #42516a;
        color: #cfd9e5;
    }

    .tool-btn:hover {
        background: #24304d;
        border: tall #7fa7ff;
        color: #ffffff;
    }

    /* ── 格式快捷输入行 ── */
    .fmt-row {
        height: 3;
        margin-bottom: 1;
        width: 100%;
    }

    .fmt-row Input {
        width: 1fr;
        margin-right: 1;
        margin-bottom: 0;
        max-width: 18;
    }

    .fmt-row Input:last-of-type {
        margin-right: 0;
    }

    /* ── 摘要区（深色卡片） ── */
    #summary {
        height: 8;
        padding: 1;
        margin-bottom: 1;
        border: solid #42516a;
        background: #151e29;
        color: #c9d4df;
        overflow-y: auto;
    }

    /* ── 结果表格 ── */
    #results {
        height: 1fr;
        border: solid #33404f;
        background: #111720;
        overflow-x: auto;
        overflow-y: auto;
    }

    DataTable > .datatable--cursor {
        background: #1f2f45;
    }

    DataTable > .datatable--header {
        background: #222b37;
        color: #b5c1cf;
        text-style: bold;
    }

    DataTable > .datatable--fixed {
        background: #222b37;
        color: #b5c1cf;
    }

    /* ── 状态栏 ── */
    #status {
        height: 2;
        padding: 0 1;
        background: #0d1219;
        color: #a7b4c2;
        border-top: solid #29323d;
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
        self._ui_mode: str = 'idle'  # idle / rename / undo / rules / formats / history

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

                # 格式快捷新增
                yield Label('▶ 新增/编辑格式', classes='sec-title', id='fmt_sec_label')
                with Horizontal(classes='fmt-row'):
                    yield Input(placeholder='名称', id='fmt_name_input')
                    yield Input(placeholder='%Y%m%d_%H%M', id='fmt_expr_input')
                yield Button('💾 保存格式', id='save_format_button', classes='tool-btn')
                yield Button('✅ 设为当前格式', id='set_current_button', classes='tool-btn')

                # ── 固定操作按钮（dock=bottom 模拟固定） ────
                with Horizontal(id='action-row'):
                    yield Button('▶ 预览 [P]', id='preview_button',  variant='primary')
                    yield Button('✔ 执行 [E]', id='execute_button',  variant='success')
                    yield Button('↩ 撤销 [U]', id='undo_button',     variant='warning')

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
        self.register_theme(DARK_THEME)
        self.theme = 'ide-dark'

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

    # 各列宽度配置（字符宽度）
    _COL_WIDTHS = {
        # 重命名结果列
        '状态':   6,
        '原文件名': 36,
        '新文件名': 36,
        '日期':   18,
        '规则来源': 18,
        '错误原因': 28,
        # 撤销结果列
        '目标路径': 36,
        '已恢复为': 36,
        '说明':   28,
        # 规则发现列
        '签名':   28,
        '数量':   6,
        '示例文本': 32,
        '建议正则': 36,
        # 格式管理列
        '当前':   5,
        '名称':   16,
        '格式表达式': 22,
        '类型':   6,
        '示例':   22,
        # 历史报告列
        '时间':   20,
        '模式':   10,
        '文件夹': 30,
        '文件数': 6,
        '成功':   5,
        '错误':   5,
        'CSV 路径': 40,
    }

    def _set_result_columns(self, *columns: str) -> DataTable:
        table = self.query_one('#results', DataTable)
        table.clear(columns=True)
        for col in columns:
            w = self._COL_WIDTHS.get(col)
            table.add_column(col, width=w)
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
    # 输出格式选中时显示预览示例
    # ────────────────────────────────────────────────────────

    @on(Select.Changed, '#format_select')
    def _on_format_changed(self, event: Select.Changed):
        """选中输出格式后，在摘要区显示当前时间的重命名示例。"""
        fmt = str(event.value) if event.value else ''
        if not fmt:
            return
        example = _format_example(fmt)
        source = self._get_source()
        source_tip = f'\n源文件夹: {source}' if source else ''
        self._summary(
            f'输出格式预览\n'
            f'格式: {fmt}\n'
            f'示例（当前时间）: IMG_20240101_120000.jpg → {example}.jpg{source_tip}\n'
            f'\n按 P 预览实际重命名效果。'
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
        self._ui_mode = 'rename'
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
        fmt     = self._get_format()

        if csv_p:
            self._last_csv_path = csv_p

        label = '预览' if mode == 'preview' else '执行重命名'
        example = _format_example(fmt) if fmt else ''
        example_line = f'格式示例: {example}.jpg\n' if example else ''
        self._summary(
            f'{label} 完成\n'
            f'扫描 {total} 个文件  ✓ 成功 {ok}  ✗ 冲突/错误 {err}\n'
            f'{example_line}'
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
        self._ui_mode = 'undo'
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
        self._ui_mode = 'rules'
        try:
            suggestions = discover_rule_suggestions(source, recursive=self._get_recursive())
        except Exception as e:
            self._status(f'规则发现失败：{e}')
            return

        self._rule_suggestions = suggestions
        # 规则发现表格不含"操作提示"列，加入规则直接通过按钮操作
        table = self._set_result_columns('签名', '数量', '示例文本', '建议正则')
        for item in suggestions[:200]:
            example = item['examples'][0]['match_text'] if item.get('examples') else ''
            regex   = (item.get('suggestion') or {}).get('regex', '')
            table.add_row(
                item['signature'],
                str(item['count']),
                example,
                regex,
            )
        self._summary(
            f'未知规则发现\n'
            f'发现 {len(suggestions)} 种未匹配文件名规则候选。\n'
            f'在下方表格中选中某行，\n'
            f'点击"➕ 加入选中规则"将其写入 patterns.json。'
        )
        self._status(f'发现 {len(suggestions)} 个候选规则。选中行后点"加入选中规则"。')

        # 动态注入"加入规则"按钮（只注入一次）
        if not self.query('#add_rule_button'):
            btn = Button('➕ 加入选中规则', id='add_rule_button', classes='tool-btn', variant='warning')
            self.query_one('#sidebar').mount(btn, before=self.query_one('#rules_button'))

    @on(Button.Pressed, '#add_rule_button')
    def _on_add_rule(self):
        """将选中的候选规则写入 patterns.json。"""
        # 只在规则发现模式下处理
        suggestions = self._rule_suggestions
        if not suggestions:
            self._status('请先点击"未知规则发现"扫描。')
            return

        table = self.query_one('#results', DataTable)
        idx = table.cursor_row
        if idx is None or idx < 0:
            self._status('请先在表格中点击选中一行候选规则。')
            return
        if idx >= len(suggestions):
            self._status('选中行已超出候选列表范围，请重新扫描。')
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

        # 从候选列表和表格里移除该行，防止重复添加
        suggestions.pop(idx)
        try:
            row_key = table.get_row_at(idx)
            table.remove_row(table.cursor_row_key)
        except Exception:
            pass

        # 更新摘要
        self._summary(
            f'规则写入成功\n'
            f'签名: {sig}\n'
            f'规则 id: {result["id"]}\n'
            f'剩余候选: {len(suggestions)} 个\n'
            f'patterns.json 已热更新，下次预览即生效。'
        )

    # ────────────────────────────────────────────────────────
    # 格式管理
    # ────────────────────────────────────────────────────────

    @on(Button.Pressed, '#formats_button')
    def _on_formats(self):
        self._ui_mode = 'formats'
        try:
            profiles = load_format_profiles()
        except Exception as e:
            self._status(f'格式加载失败：{e}')
            return

        self._format_profiles = profiles
        table = self._set_result_columns('当前', '名称', '格式表达式', '类型', '示例')
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
            f'选中某行：左侧输入框自动填充，可直接编辑后保存。\n'
            f'点"设为当前格式"立即生效。'
        )
        self._status('格式列表已加载。选中行后可编辑或设为当前。')

    @on(DataTable.RowSelected, '#results')
    def _on_row_selected(self, event: DataTable.RowSelected):
        """
        选中行的处理：
        - 格式管理模式：自动填充名称/表达式输入框
        - 规则发现模式：在摘要区显示选中规则的详情
        """
        idx = event.cursor_row
        if idx is None or idx < 0:
            return

        # 格式管理模式
        profiles = self._format_profiles
        if profiles and self._ui_mode == 'formats':
            if 0 <= idx < len(profiles):
                p = profiles[idx]
                self.query_one('#fmt_name_input', Input).value = p.get('name', '')
                self.query_one('#fmt_expr_input', Input).value = p.get('format', '')
                # 同时在摘要区显示格式效果
                fmt = p.get('format', '')
                example = _format_example(fmt)
                self._summary(
                    f'格式预览\n'
                    f'名称: {p.get("name", "")}\n'
                    f'格式: {fmt}\n'
                    f'示例（当前时间）: {example}.jpg\n'
                    f'类型: {"内置" if p.get("builtin") else "自定义"}'
                )
            return

        # 规则发现模式：摘要区显示规则详情
        suggestions = self._rule_suggestions
        if suggestions and self._ui_mode == 'rules':
            if 0 <= idx < len(suggestions):
                item = suggestions[idx]
                examples = item.get('examples', [])
                ex_text = '\n'.join(
                    f'  {e["match_text"]}' for e in examples[:3]
                )
                regex = (item.get('suggestion') or {}).get('regex', '（无建议）')
                self._summary(
                    f'候选规则详情\n'
                    f'签名: {item["signature"]}\n'
                    f'匹配文件数: {item["count"]}\n'
                    f'建议正则: {regex}\n'
                    f'示例文件名:\n{ex_text}\n'
                    f'确认无误后点"➕ 加入选中规则"写入 patterns.json。'
                )

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
        example = _format_example(expr)
        self._status(f'✓ 格式 [{name}] 已保存，示例: {example}。点"设为当前格式"立即启用。')
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
        example = _format_example(expr)
        self._status(f'✓ 格式 [{name}] 已设为当前，示例: {example}。')
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
        self._ui_mode = 'history'
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
            f'选中行后点"📂 打开 CSV 目录"跳转到文件管理器。'
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
