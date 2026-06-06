#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Textual UI for Photo & Video Renamer.

The UI stays thin on purpose: rename, undo, rule discovery, and profile
management are delegated to the service layer in photo_renamer.py.
"""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from tkinter import Tk, filedialog

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.theme import Theme
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Select,
    Static,
)

from photo_renamer import (
    RenameJobOptions,
    add_pattern_suggestion,
    discover_rule_suggestions,
    load_format_profiles,
    load_history_reports,
    run_rename_job,
    save_format_profile,
    undo_from_csv,
    write_undo_report,
)


THEME = Theme(
    name="renamer-workbench",
    primary="#6fb3ff",
    secondary="#8be9fd",
    accent="#c3e88d",
    background="#0f141b",
    surface="#161d27",
    panel="#1d2633",
    error="#ff7b88",
    success="#9ed46b",
    warning="#ffc777",
    foreground="#d6deeb",
    dark=True,
    variables={
        "text": "#d6deeb",
        "text-muted": "#90a0b4",
        "border": "#334155",
        "input-background": "#101924",
        "input-foreground": "#d6deeb",
    },
)


def open_folder(path: str) -> str:
    """Open a file or folder location in the system file manager."""
    try:
        target = Path(path)
        if not target.exists():
            return f"路径不存在：{path}"
        folder = target if target.is_dir() else target.parent
        if sys.platform == "win32":
            os.startfile(str(folder))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(folder)], check=True)
        else:
            subprocess.run(["xdg-open", str(folder)], check=True)
        return ""
    except Exception as exc:
        return f"打开文件夹失败：{exc}"


def choose_directory(initial_dir: str = "") -> str:
    """Open a native folder picker and return the chosen directory."""
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(
            title="选择源文件夹",
            initialdir=initial_dir or str(Path.home()),
            mustexist=True,
        )
        return selected or ""
    finally:
        root.destroy()


def _build_format_options() -> tuple[list[tuple[str, str]], str]:
    try:
        profiles = load_format_profiles()
    except Exception:
        profiles = []

    options: list[tuple[str, str]] = []
    current_value = ""
    for profile in profiles:
        prefix = "默认  " if profile.get("current") else "      "
        label = f"{prefix}{profile.get('name', '')} | {profile.get('format', '')}"
        fmt = profile.get("format", "")
        if fmt:
            options.append((label, fmt))
        if profile.get("current"):
            current_value = fmt

    if not options:
        options = [("默认  默认 | %Y.%m.%d_%H%M", "%Y.%m.%d_%H%M")]
        current_value = "%Y.%m.%d_%H%M"
    return options, current_value or options[0][1]


def _format_example(fmt: str) -> str:
    try:
        return datetime(2024, 6, 15, 14, 30, 0).strftime(fmt)
    except Exception:
        return "格式无效"


class PhotoRenamerApp(App):
    TITLE = "Photo & Video Renamer"
    CSS_PATH = None

    CSS = """
    Screen {
        background: #0f141b;
        color: #d6deeb;
    }

    Header {
        background: #0a0f15;
        color: #d6deeb;
        text-style: bold;
        border-bottom: solid #263241;
    }

    Footer {
        background: #0a0f15;
        color: #8ea0b5;
        border-top: solid #263241;
    }

    #layout {
        height: 100%;
    }

    #sidebar {
        width: 42;
        min-width: 38;
        max-width: 46;
        padding: 1;
        background: #161d27;
        border-right: solid #334155;
        overflow-y: auto;
        overflow-x: hidden;
    }

    #workspace {
        padding: 1;
        background: #0f141b;
    }

    .section {
        margin-top: 1;
        margin-bottom: 0;
        color: #78aefc;
        text-style: bold;
    }

    .hint {
        color: #90a0b4;
        margin-bottom: 1;
    }

    #sidebar Input,
    #sidebar Select {
        width: 100%;
        margin-bottom: 1;
        background: #101924;
        color: #d6deeb;
        border: tall #334155;
    }

    #sidebar Input:focus,
    #sidebar Select:focus {
        border: tall #78aefc;
    }

    #recursive_toggle {
        width: 100%;
        margin-bottom: 1;
        color: #d6deeb;
        background: #152232;
        border: tall #3c5d7c;
        padding: 0 2;
        height: 3;
        text-style: bold;
    }

    #recursive_toggle.toggle-on {
        background: #4c93d9;
        border: tall #b9dcff;
        color: #081522;
    }

    #recursive_toggle.toggle-off {
        background: #152232;
        border: tall #3c5d7c;
        color: #d6deeb;
    }

    .tool-btn {
        width: 100%;
        margin-bottom: 1;
        background: #1d2633;
        color: #d6deeb;
        border: tall #41536c;
    }

    .tool-btn:hover {
        background: #24324a;
        border: tall #78aefc;
    }

    .primary-btn {
        width: 100%;
        margin-bottom: 1;
        text-style: bold;
    }

    #preview_button {
        background: #76b7ff;
        color: #0c1a2a;
        border: tall #9fd0ff;
        text-style: bold;
    }

    #execute_button {
        background: #b7da63;
        color: #192406;
        border: tall #d8f08f;
        text-style: bold;
    }

    #undo_button {
        background: #ffd690;
        color: #372100;
        border: tall #ffe8bb;
        text-style: bold;
    }

    #format-row {
        height: 3;
        margin-bottom: 1;
    }

    #format-row Input {
        width: 1fr;
        margin-right: 1;
        margin-bottom: 0;
    }

    #format-row Input:last-of-type {
        margin-right: 0;
    }

    #summary {
        height: 9;
        padding: 1 2;
        margin-bottom: 1;
        background: #161d27;
        color: #d6deeb;
        border: solid #334155;
        overflow-y: auto;
    }

    #results {
        height: 1fr;
        background: #0f141b;
        border: solid #334155;
        overflow-x: auto;
        overflow-y: auto;
    }

    DataTable > .datatable--header {
        background: #1d2633;
        color: #d6deeb;
        text-style: bold;
    }

    DataTable > .datatable--cursor {
        background: #243a5a;
    }

    #status {
        height: 3;
        padding: 1 2;
        margin-top: 1;
        background: #0a0f15;
        color: #a9b8c7;
        border: solid #263241;
    }
    """

    BINDINGS = [
        ("p", "preview", "预览"),
        ("e", "execute", "执行"),
        ("u", "undo", "撤销"),
        ("q", "quit", "退出"),
    ]

    _COL_WIDTHS = {
        "状态": 8,
        "原文件名": 34,
        "新文件名": 34,
        "日期": 18,
        "规则来源": 18,
        "错误原因": 30,
        "目标路径": 42,
        "恢复到": 42,
        "说明": 30,
        "匹配数": 8,
        "示例文件名": 36,
        "建议正则": 42,
        "规则签名": 34,
        "启动默认": 10,
        "名称": 16,
        "格式表达式": 26,
        "类型": 8,
        "示例": 24,
        "时间": 20,
        "模式": 10,
        "文件夹": 34,
        "文件数": 8,
        "成功": 7,
        "错误": 7,
        "CSV 路径": 46,
    }

    def __init__(self):
        super().__init__()
        self._rule_suggestions: list[dict] = []
        self._format_profiles: list[dict] = []
        self._history_rows: list[dict] = []
        self._last_csv_path = ""
        self._recursive = True
        self._source_input_revision = 0
        self._suppress_source_change = False
        self._ui_mode = "idle"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        options, current = _build_format_options()

        with Horizontal(id="layout"):
            with Vertical(id="sidebar"):
                yield Label("1. 文件夹", classes="section")
                yield Input(placeholder="粘贴或拖入源文件夹路径", id="source_input")
                yield Static("可直接粘贴路径；如终端宿主支持，也可拖到上方路径框。", classes="hint")
                yield Button("选择文件夹", id="browse_button", classes="tool-btn")
                yield Button("", id="recursive_toggle", classes="toggle-on")

                yield Button("预览", id="preview_button", classes="primary-btn")
                yield Button("执行重命名", id="execute_button", classes="primary-btn")
                yield Button("撤销最近一次", id="undo_button", classes="primary-btn")
                yield Static("高频操作放在这里。先预览，再执行。", classes="hint")

                yield Label("2. 文件名格式", classes="section")
                yield Select(options=options, value=current, id="format_select", allow_blank=False)
                yield Static("启动时默认选中的格式会写入配置并自动记忆。", classes="hint")

                yield Label("3. 陌生规则", classes="section")
                yield Button("扫描陌生规则", id="rules_button", classes="tool-btn")
                yield Button("加入选中规则", id="add_rule_button", classes="tool-btn")

                yield Label("4. 格式管理", classes="section")
                with Horizontal(id="format-row"):
                    yield Input(placeholder="名称", id="fmt_name_input")
                    yield Input(placeholder="%Y.%m.%d_%H%M", id="fmt_expr_input")
                yield Button("查看格式列表", id="formats_button", classes="tool-btn")
                yield Button("保存格式", id="save_format_button", classes="tool-btn")
                yield Button("设为启动默认", id="set_current_button", classes="tool-btn")

                yield Label("5. 历史", classes="section")
                yield Button("历史报告", id="history_button", classes="tool-btn")
                yield Button("撤销命名", id="history_undo_button", classes="tool-btn")

            with Vertical(id="workspace"):
                yield Static(
                    "准备就绪\n"
                    "1. 选择源文件夹和文件名格式。\n"
                    "2. 点击左侧上方的预览或执行重命名。\n"
                    "3. 执行后可撤销最近一次，或从历史记录选择旧 CSV 撤销。",
                    id="summary",
                )
                table = DataTable(id="results", cursor_type="row")
                table.add_columns("状态", "原文件名", "新文件名", "日期", "规则来源", "错误原因")
                yield table
                yield Static("快捷键：P 预览 | E 执行 | U 撤销最近一次 | Q 退出", id="status")

        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(THEME)
        self.theme = "renamer-workbench"
        self._update_recursive_toggle()
        self.query_one("#source_input", Input).focus()

    def _status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)

    def _summary(self, message: str) -> None:
        self.query_one("#summary", Static).update(message)

    def _refocus_source_input(self) -> None:
        self.query_one("#source_input", Input).focus()

    def _summary_with_csv(self, lines: list[str], csv_path: str = "", history_path: str = "") -> None:
        text = Text("\n".join(lines))
        if csv_path:
            path_obj = Path(csv_path)
            text.append("\nCSV 已生成：\n")
            text.append(str(path_obj), style="bold #9fd0ff")
            try:
                uri = path_obj.resolve().as_uri()
            except ValueError:
                uri = ""
            if uri:
                text.append("\n打开链接：")
                text.append(uri, style=f"underline #8be9fd link {uri}")
        if history_path:
            text.append(f"\n历史记录：{history_path}")
        self.query_one("#summary", Static).update(text)

    def _get_source(self) -> str:
        return self.query_one("#source_input", Input).value.strip().strip('"').strip("'")

    def _get_recursive(self) -> bool:
        return self._recursive

    def _get_format(self) -> str:
        value = self.query_one("#format_select", Select).value
        return str(value) if value else ""

    def _normalize_pasted_path(self, text: str) -> str:
        candidate = text.strip().strip('"').strip("'")
        if candidate.endswith("\\") and len(candidate) > 3:
            candidate = candidate.rstrip("\\")
        return candidate

    def _apply_source_path(self, target: Path) -> None:
        source_input = self.query_one("#source_input", Input)
        self._suppress_source_change = True
        source_input.value = str(target)
        self._suppress_source_change = False
        self._refocus_source_input()
        self._status(f"已接收目录：{target}")
        self._summary(
            "目录已填入\n"
            f"源文件夹：{target}\n"
            "现在可以直接点击“预览”或“执行重命名”。"
        )

    def _update_recursive_toggle(self) -> None:
        button = self.query_one("#recursive_toggle", Button)
        button.label = "✓ 已包含子目录" if self._recursive else "□ 不包含子目录"
        if self._recursive:
            button.add_class("toggle-on")
            button.remove_class("toggle-off")
        else:
            button.add_class("toggle-off")
            button.remove_class("toggle-on")

    def _pick_external_target(self, raw_paths: list[str]) -> Path | None:
        for raw in raw_paths:
            candidate = self._normalize_pasted_path(raw)
            if not candidate:
                continue
            path = Path(candidate)
            if path.is_dir():
                return path
            if path.exists():
                return path.parent
        return None

    def _accept_external_path(self, text: str) -> bool:
        target = self._pick_external_target([text])
        if target is None:
            return False
        self._apply_source_path(target)
        return True

    def _schedule_source_path_resolution(self, candidate: str) -> None:
        self._source_input_revision += 1
        revision = self._source_input_revision

        def resolve_later() -> None:
            if revision != self._source_input_revision or self._ui_mode != "idle":
                return
            current_value = self._normalize_pasted_path(self._get_source())
            if current_value != candidate or not current_value:
                return
            target = self._pick_external_target([current_value])
            if target is not None:
                self._apply_source_path(target)

        self.set_timer(0.3, resolve_later)

    def _set_result_columns(self, *columns: str) -> DataTable:
        table = self.query_one("#results", DataTable)
        table.clear(columns=True)
        for column in columns:
            table.add_column(column, width=self._COL_WIDTHS.get(column))
        return table

    def _render_rename_results(self, results: list[dict]) -> None:
        table = self._set_result_columns("状态", "原文件名", "新文件名", "日期", "规则来源", "错误原因")
        for row in results[:2000]:
            table.add_row(
                str(row.get("status", "")),
                Path(str(row.get("original", ""))).name,
                str(row.get("new_name", "")),
                str(row.get("date", "") or "")[:16],
                str(row.get("source", "")),
                str(row.get("error", "") or ""),
            )

    def _make_options(self, mode: str) -> RenameJobOptions:
        return RenameJobOptions(
            source_dir=self._get_source(),
            mode=mode,
            recursive=self._get_recursive(),
            fmt_arg=self._get_format(),
            csv_path="",
        )

    def _render_undo_details(self, summary: dict, report_path: str) -> None:
        self._summary(
            "撤销完成\n"
            f"记录：{summary.get('rows', 0)}\n"
            f"恢复：{summary.get('restored', 0)}    跳过：{summary.get('skipped', 0)}    错误：{summary.get('errors', 0)}\n"
            f"撤销报告：{report_path}"
        )
        table = self._set_result_columns("状态", "目标路径", "恢复到", "说明")
        for row in summary.get("details", [])[:2000]:
            table.add_row(
                str(row.get("status", "")),
                str(row.get("dst", "") or row.get("new_name", "")),
                str(row.get("original", "")),
                str(row.get("undo_error", "") or ""),
            )

    def _undo_from_csv_path(self, csv_path: str) -> None:
        if not csv_path:
            self._status("没有可撤销的 CSV 记录。")
            return
        self._ui_mode = "undo"
        self._status(f"正在撤销：{csv_path}")
        try:
            summary = undo_from_csv(csv_path)
            report_path = write_undo_report(csv_path, summary["details"])
        except Exception as exc:
            self._status(f"撤销失败：{exc}")
            return
        self._render_undo_details(summary, report_path)
        self._last_csv_path = ""
        self._status("撤销完成，详情见右侧表格。")

    @on(Select.Changed, "#format_select")
    def _on_format_changed(self, event: Select.Changed) -> None:
        fmt = str(event.value) if event.value else ""
        if not fmt:
            return
        self._summary(
            "格式预览\n"
            f"格式：{fmt}\n"
            f"示例：IMG_20240101_120000.jpg -> {_format_example(fmt)}.jpg\n"
            "如果需要记住这个格式，请点击“设为启动默认”。"
        )

    @on(events.Paste)
    def _on_paste(self, event: events.Paste) -> None:
        if self._accept_external_path(event.text):
            event.stop()

    @on(Input.Changed, "#source_input")
    def _on_source_input_changed(self, event: Input.Changed) -> None:
        if self._ui_mode != "idle" or self._suppress_source_change:
            return
        candidate = self._normalize_pasted_path(event.value)
        if not candidate:
            return
        self._schedule_source_path_resolution(candidate)

    def _run_job(self, mode: str) -> None:
        source = self._get_source()
        if not source:
            self._status("请先填写源文件夹路径。")
            self._refocus_source_input()
            return

        label = "预览" if mode == "preview" else "执行"
        self._ui_mode = "rename"
        self._status(f"{label}中，请稍候...")
        try:
            summary = run_rename_job(self._make_options(mode))
        except Exception as exc:
            self._status(f"{label}失败：{exc}")
            self._refocus_source_input()
            return

        csv_path = summary.get("csv_path", "")
        history_path = summary.get("history_path", "")
        if csv_path:
            self._last_csv_path = csv_path

        self._summary_with_csv(
            [
                f"{label}完成",
                f"扫描文件：{summary.get('files_count', 0)}",
                f"成功：{summary.get('ok_count', 0)}    冲突/错误：{summary.get('error_count', 0)}",
                f"格式示例：{_format_example(self._get_format())}.jpg",
            ],
            csv_path=csv_path,
            history_path=history_path or "-",
        )
        self._render_rename_results(summary.get("results", []))
        self._status(f"{label}完成。可用“撤销最近一次”或历史区“撤销命名”。")
        self._refocus_source_input()

    def action_preview(self) -> None:
        self._run_job("preview")

    def action_execute(self) -> None:
        self._run_job("execute")

    def action_undo(self) -> None:
        if not self._last_csv_path:
            self._status("暂无可撤销记录。请先执行一次重命名，或从历史记录中选中一条再撤销。")
            return
        self._undo_from_csv_path(self._last_csv_path)

    @on(Button.Pressed, "#preview_button")
    def _on_preview(self) -> None:
        self.action_preview()

    @on(Button.Pressed, "#execute_button")
    def _on_execute(self) -> None:
        self.action_execute()

    @on(Button.Pressed, "#undo_button")
    def _on_undo(self) -> None:
        self.action_undo()

    @on(Button.Pressed, "#browse_button")
    def _on_browse(self) -> None:
        initial_dir = self._get_source()
        if initial_dir and not Path(initial_dir).exists():
            initial_dir = ""
        try:
            selected = choose_directory(initial_dir)
        except Exception as exc:
            self._status(f"打开目录选择器失败：{exc}")
            self._refocus_source_input()
            return
        if not selected:
            self._status("已取消选择文件夹。")
            self._refocus_source_input()
            return
        self._apply_source_path(Path(selected))

    @on(Button.Pressed, "#recursive_toggle")
    def _on_recursive_toggle(self) -> None:
        self._recursive = not self._recursive
        self._update_recursive_toggle()
        message = "已包含子目录，扫描时会递归进入下级文件夹。" if self._recursive else "已关闭子目录扫描，只处理当前文件夹。"
        self._status(message)
        self._refocus_source_input()

    @on(Button.Pressed, "#rules_button")
    def _on_rules(self) -> None:
        source = self._get_source()
        if not source:
            self._status("请先填写源文件夹路径，再扫描陌生规则。")
            return

        self._ui_mode = "rules"
        self._status("正在扫描陌生规则...")
        try:
            suggestions = discover_rule_suggestions(source, recursive=self._get_recursive())
        except Exception as exc:
            self._status(f"陌生规则扫描失败：{exc}")
            return

        self._rule_suggestions = suggestions
        table = self._set_result_columns("匹配数", "示例文件名", "建议正则", "规则签名")
        for item in suggestions[:200]:
            examples = item.get("examples") or []
            example = examples[0].get("match_text", "") if examples else ""
            regex = (item.get("suggestion") or {}).get("regex", "")
            table.add_row(str(item.get("count", 0)), example, regex, item.get("signature", ""))

        self._summary(
            "陌生规则确认\n"
            f"发现候选规则：{len(suggestions)} 条\n"
            "1. 在右侧表格中选中一条候选规则。\n"
            "2. 查看示例文件名和建议正则。\n"
            "3. 确认无误后点击“加入选中规则”。"
        )
        self._status("扫描完成。选中一条候选规则后再加入。")

    @on(Button.Pressed, "#add_rule_button")
    def _on_add_rule(self) -> None:
        if self._ui_mode != "rules" or not self._rule_suggestions:
            self._status("请先点击“扫描陌生规则”，再选择候选规则。")
            return

        table = self.query_one("#results", DataTable)
        idx = table.cursor_row
        if idx is None or idx < 0 or idx >= len(self._rule_suggestions):
            self._status("请先在右侧表格中选中一条候选规则。")
            return

        item = self._rule_suggestions[idx]
        signature = item.get("signature", "")
        try:
            result = add_pattern_suggestion(signature)
        except Exception as exc:
            self._status(f"写入规则失败：{exc}")
            return
        if not result:
            self._status(f"无法为规则签名生成配置：{signature}")
            return

        self._rule_suggestions.pop(idx)
        self._on_rules()
        self._summary(
            "规则已加入\n"
            f"规则签名：{signature}\n"
            f"规则 ID：{result.get('id', '')}\n"
            f"剩余候选：{len(self._rule_suggestions)} 条\n"
            "新的规则已写入 patterns.json，下次预览会自动生效。"
        )
        self._status(f"规则已加入：{result.get('id', '')}")

    @on(Button.Pressed, "#formats_button")
    def _on_formats(self) -> None:
        self._ui_mode = "formats"
        try:
            profiles = load_format_profiles()
        except Exception as exc:
            self._status(f"格式加载失败：{exc}")
            return

        self._format_profiles = profiles
        table = self._set_result_columns("启动默认", "名称", "格式表达式", "类型", "示例")
        for profile in profiles:
            fmt = profile.get("format", "")
            table.add_row(
                "是" if profile.get("current") else "",
                profile.get("name", ""),
                fmt,
                "内置" if profile.get("builtin") else "自定义",
                _format_example(fmt),
            )

        self._summary(
            "文件名格式管理\n"
            f"格式数量：{len(profiles)}\n"
            "选中一行可自动填入左侧输入框。\n"
            "保存用于新增或覆盖；设为启动默认用于下次启动自动选中。"
        )
        self._status("格式列表已加载。选中一条后可编辑或设为启动默认。")

    @on(DataTable.RowSelected, "#results")
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if idx is None or idx < 0:
            return

        if self._ui_mode == "formats" and idx < len(self._format_profiles):
            profile = self._format_profiles[idx]
            fmt = profile.get("format", "")
            self.query_one("#fmt_name_input", Input).value = profile.get("name", "")
            self.query_one("#fmt_expr_input", Input).value = fmt
            self._summary(
                "格式预览\n"
                f"名称：{profile.get('name', '')}\n"
                f"格式：{fmt}\n"
                f"示例：{_format_example(fmt)}.jpg\n"
                f"类型：{'内置' if profile.get('builtin') else '自定义'}"
            )
            return

        if self._ui_mode == "rules" and idx < len(self._rule_suggestions):
            item = self._rule_suggestions[idx]
            examples = item.get("examples") or []
            example_text = "\n".join(f"- {entry.get('match_text', '')}" for entry in examples[:4])
            regex = (item.get("suggestion") or {}).get("regex", "无建议")
            self._summary(
                "候选规则详情\n"
                f"规则签名：{item.get('signature', '')}\n"
                f"匹配文件：{item.get('count', 0)} 个\n"
                f"建议正则：{regex}\n"
                f"示例文件名：\n{example_text}\n"
                "确认这些示例属于同一种命名规则后，再点击“加入选中规则”。"
            )

    @on(Button.Pressed, "#save_format_button")
    def _on_save_format(self) -> None:
        name = self.query_one("#fmt_name_input", Input).value.strip()
        expr = self.query_one("#fmt_expr_input", Input).value.strip()
        if not name or not expr:
            self._status("格式名称和表达式不能为空。")
            return
        try:
            save_format_profile(name, expr, make_current=False)
        except Exception as exc:
            self._status(f"保存格式失败：{exc}")
            return
        self._refresh_format_select()
        self._on_formats()
        self._status(f"格式已保存：{name}")

    @on(Button.Pressed, "#set_current_button")
    def _on_set_current(self) -> None:
        name = self.query_one("#fmt_name_input", Input).value.strip()
        expr = self.query_one("#fmt_expr_input", Input).value.strip()
        if not name or not expr:
            self._status("请先选择或填写格式名称和表达式。")
            return
        try:
            save_format_profile(name, expr, make_current=True)
        except Exception as exc:
            self._status(f"设置启动默认失败：{exc}")
            return
        self._refresh_format_select()
        self._on_formats()
        self._status(f"启动默认格式已设置为：{name}")

    def _refresh_format_select(self) -> None:
        try:
            options, current = _build_format_options()
            select = self.query_one("#format_select", Select)
            select.set_options(options)
            select.value = current
        except Exception:
            pass

    @on(Button.Pressed, "#history_button")
    def _on_history(self) -> None:
        self._ui_mode = "history"
        try:
            rows = load_history_reports()
        except Exception as exc:
            self._status(f"历史报告加载失败：{exc}")
            return

        self._history_rows = rows
        table = self._set_result_columns("时间", "模式", "文件夹", "文件数", "成功", "错误", "CSV 路径")
        for row in rows[-500:]:
            table.add_row(
                row.get("timestamp", ""),
                row.get("mode", ""),
                row.get("folder", ""),
                str(row.get("files_count", "")),
                str(row.get("ok_count", "")),
                str(row.get("error_count", "")),
                row.get("csv_path", ""),
            )

        self._summary(
            "历史报告\n"
            f"记录数量：{len(rows)}\n"
            "选中一行后点击“撤销命名”，会按该次操作生成的 CSV 直接执行撤销。\n"
            "这一步会真正改回文件名。"
        )
        self._status("历史报告已加载。选中一条记录后可执行“撤销命名”。")

    @on(Button.Pressed, "#history_undo_button")
    def _on_history_undo(self) -> None:
        if self._ui_mode != "history":
            self._status("请先打开历史报告并选中一条记录。")
            return
        table = self.query_one("#results", DataTable)
        idx = table.cursor_row
        if idx is None or idx < 0:
            self._status("请先在历史表格中选中一条记录。")
            return
        offset = max(0, len(self._history_rows) - 500)
        real_idx = offset + idx
        if real_idx >= len(self._history_rows):
            self._status("选中行超出历史记录范围。")
            return

        csv_path = self._history_rows[real_idx].get("csv_path", "")
        if not csv_path:
            self._status("该历史记录没有 CSV 路径。")
            return
        self._last_csv_path = csv_path
        self._undo_from_csv_path(csv_path)


def main() -> None:
    PhotoRenamerApp().run()


if __name__ == "__main__":
    main()
