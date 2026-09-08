"""Desktop preview/rename/undo workflow. Business rules remain in photo_renamer."""
import html
import sys
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QThread, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QProgressBar, QPushButton, QStyle, QTableView,
    QVBoxLayout, QWidget, QHeaderView,
)

from photo_renamer import (
    RenameJobOptions, load_format_profiles, run_rename_job, undo_from_csv,
    write_undo_report, save_format_profile,
)


class ResultModel(QAbstractTableModel):
    columns = [('status', '状态'), ('original', '原文件'), ('new_name', '新文件名'),
               ('date', '日期'), ('source', '日期来源'), ('error', '详情')]
    labels = {'ok': '成功', 'unchanged': '无需改名', 'error': '失败',
              'conflict': '冲突', 'restored': '已撤销', 'skipped': '已跳过'}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def replace(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.columns[section][1]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        key = self.columns[index.column()][0]
        value = str(row.get(key, ''))
        if role == Qt.ItemDataRole.ToolTipRole:
            return value
        if role == Qt.ItemDataRole.DisplayRole:
            if key == 'status':
                if value == 'ok' and row.get('operation') == 'preview':
                    return '可执行'
                return self.labels.get(value, value)
            return Path(value).name if key == 'original' else value
        if role == Qt.ItemDataRole.ForegroundRole and key == 'status':
            return QColor('#b42318' if value in ('error', 'conflict') else '#14765b')


class JobThread(QThread):
    progress = Signal(dict)
    result = Signal(dict)
    failed = Signal(str)

    def __init__(self, mode, options=None, csv_path='', parent=None):
        super().__init__(parent)
        self.mode, self.options, self.csv_path = mode, options, csv_path

    def run(self):
        try:
            if self.mode == 'undo':
                summary = undo_from_csv(self.csv_path, progress_callback=self.progress.emit)
                summary['csv_path'] = str(write_undo_report(self.csv_path, summary['details']))
            else:
                self.options.progress_callback = self.progress.emit
                summary = run_rename_job(self.options)
            self.result.emit(summary)
        except Exception as exc:
            self.failed.emit(str(exc))


class RenamerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('照片与视频整理')
        self.resize(1180, 760)
        self.setMinimumSize(800, 540)
        self.setAcceptDrops(True)
        self.worker = None
        self.last_execution_csv = ''
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel('照片与视频整理')
        title.setObjectName('title')
        header.addWidget(title)
        header.addStretch()
        edition = QLabel('桌面预览版')
        edition.setObjectName('muted')
        header.addWidget(edition)
        layout.addLayout(header)

        location = QHBoxLayout()
        self.source = QLineEdit()
        self.source.setPlaceholderText('选择或拖入文件夹')
        self.source.setClearButtonEnabled(True)
        self.source.setAcceptDrops(False)
        self.browse = QPushButton('选择文件夹')
        self.browse.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.browse.clicked.connect(self.choose_source)
        location.addWidget(self.source, 1)
        location.addWidget(self.browse)
        layout.addLayout(location)

        options = QHBoxLayout()
        self.recursive = QCheckBox('包含子目录')
        self.recursive.setChecked(True)
        options.addWidget(self.recursive)
        options.addSpacing(24)
        options.addWidget(QLabel('命名格式'))
        self.formats = QComboBox()
        self.formats.setMinimumWidth(230)
        options.addWidget(self.formats, 1)
        self.example = QLabel()
        self.example.setObjectName('muted')
        options.addWidget(self.example)
        layout.addLayout(options)

        actions = QHBoxLayout()
        self.preview = QPushButton('预览')
        self.execute = QPushButton('执行重命名')
        self.execute.setObjectName('primary')
        self.undo = QPushButton('撤销最近一次')
        self.undo.setEnabled(False)
        self.recover = QPushButton('选择撤销日志')
        for button, icon in [(self.preview, QStyle.StandardPixmap.SP_FileDialogContentsView),
                             (self.execute, QStyle.StandardPixmap.SP_DialogApplyButton),
                             (self.undo, QStyle.StandardPixmap.SP_ArrowBack)]:
            button.setIcon(self.style().standardIcon(icon))
            actions.addWidget(button)
        actions.addStretch()
        actions.addWidget(self.recover)
        layout.addLayout(actions)
        self.preview.clicked.connect(lambda: self.start_job('preview'))
        self.execute.clicked.connect(lambda: self.start_job('execute'))
        self.undo.clicked.connect(lambda: self.start_job('undo'))
        self.recover.clicked.connect(self.choose_log)

        self.summary = QLabel('尚未开始')
        self.summary.setWordWrap(True)
        self.summary.setObjectName('summary')
        layout.addWidget(self.summary)
        self.csv_link = QLabel()
        self.csv_link.setWordWrap(True)
        self.csv_link.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        layout.addWidget(self.csv_link)

        self.table = QTableView()
        self.model = ResultModel(self)
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for col, width in enumerate([95, 235, 210, 165, 200, 220]):
            self.table.setColumnWidth(col, width)
        layout.addWidget(self.table, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFixedHeight(24)
        layout.addWidget(self.progress)
        self.status = QLabel('就绪')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.formats.currentIndexChanged.connect(self.update_example)
        self.formats.activated.connect(self.remember_format)
        try:
            current = 0
            for idx, profile in enumerate(load_format_profiles()):
                self.formats.addItem(profile['name'], profile['format'])
                if profile.get('current'):
                    current = idx
            self.formats.setCurrentIndex(current)
            self.update_example()
        except Exception as exc:
            self.status.setText(f'配置读取失败：{exc}')
        self.setStyleSheet('''
            QMainWindow, QWidget { background: #f5f6f8; color: #20252c;
                font-family: "Microsoft YaHei UI", "Noto Sans", sans-serif; font-size: 14px; }
            QLabel#title { font-size: 24px; font-weight: 600; }
            QLabel#muted { color: #65717e; }
            QLabel#summary { font-weight: 600; font-size: 15px; }
            QPushButton, QLineEdit, QComboBox { min-height: 34px; padding: 3px 12px;
                border: 1px solid #cbd1d8; border-radius: 5px; background: #ffffff; }
            QPushButton:hover { background: #eaf0f4; border-color: #8c9dab; }
            QPushButton#primary { background: #14765b; border-color: #14765b; color: white; }
            QPushButton#primary:hover { background: #105e49; }
            QPushButton:disabled, QPushButton#primary:disabled { background: #e7eaee;
                color: #858d96; border-color: #d5d9de; }
            QLineEdit:focus, QComboBox:focus { border: 1px solid #14765b; }
            QCheckBox { spacing: 8px; }
            QCheckBox::indicator { width: 20px; height: 20px; }
            QTableView { background: white; alternate-background-color: #f7f9fb;
                gridline-color: #e5e9ee; border: 1px solid #d4dbe2;
                selection-background-color: #d6e9e2; selection-color: #183c30; }
            QHeaderView::section { background: #edf0f4; padding: 8px;
                border: 0; border-bottom: 1px solid #d4dbe2; font-weight: 600; }
            QProgressBar { border: 1px solid #d4dbe2; background: #e9edf2;
                border-radius: 4px; text-align: center; color: #20252c; }
            QProgressBar::chunk { background: #81c8af; border-radius: 3px; }
        ''')

    def update_example(self):
        from datetime import datetime
        try:
            self.example.setText(datetime(2024, 6, 15, 14, 30).strftime(self.formats.currentData() or '') + '.jpg')
        except ValueError:
            self.example.setText('格式无效')

    def remember_format(self):
        try:
            save_format_profile(self.formats.currentText(), self.formats.currentData(), make_current=True)
        except (OSError, ValueError) as exc:
            self.status.setText(f'启动默认格式保存失败：{exc}')

    def choose_source(self):
        path = QFileDialog.getExistingDirectory(self, '选择文件夹', self.source.text())
        if path:
            self.source.setText(path)

    def choose_log(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择执行日志', self.source.text(), 'CSV (*.csv)')
        if path:
            self.start_job('undo', path)

    def dragEnterEvent(self, event):
        if self.worker is None and event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if len(urls) == 1 and urls[0].isLocalFile():
                event.acceptProposedAction()

    def dropEvent(self, event):
        if self.worker is not None:
            return
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            self.source.setText(urls[0].toLocalFile())
            event.acceptProposedAction()

    def start_job(self, mode, csv_path=''):
        if self.worker is not None:
            return
        options = RenameJobOptions(source_dir=self.source.text().strip().strip('"'),
                                   recursive=self.recursive.isChecked(), mode=mode,
                                   fmt_arg=self.formats.currentData() or '')
        if mode != 'undo' and not options.source_dir:
            self.status.setText('请先选择文件夹')
            return
        self.progress.setRange(0, 0)
        self.status.setText('正在准备…')
        self.summary.setText({'preview': '正在预览', 'execute': '正在重命名', 'undo': '正在撤销'}[mode])
        self.csv_link.clear()
        self.set_busy(True)
        self.worker = JobThread(mode, options, csv_path or self.last_execution_csv, self)
        self.worker.progress.connect(self.on_progress)
        self.worker.result.connect(self.on_result)
        self.worker.failed.connect(self.on_failure)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def set_busy(self, busy):
        for widget in (self.source, self.browse, self.recursive, self.formats,
                       self.preview, self.execute, self.recover):
            widget.setEnabled(not busy)
        self.undo.setEnabled(not busy and bool(self.last_execution_csv))

    def on_progress(self, payload):
        total = payload.get('total', 0)
        current = payload.get('current', 0)
        if total:
            stage = payload.get('stage')
            percent = current / total * 100
            if self.worker.mode == 'execute':
                percent = percent * .6 if stage == 'analyze' else 60 + percent * .4
            self.progress.setRange(0, 100)
            self.progress.setValue(min(99, int(percent)))
        label = {'scan': '扫描目录', 'preview': '分析日期', 'analyze': '分析日期',
                 'execute': '重命名', 'undo': '撤销'}.get(payload.get('stage'), '处理中')
        self.status.setText(f'{label}  {current} / {total or "…"}   {payload.get("info", "")}')

    def on_result(self, summary):
        mode = self.worker.mode
        if mode == 'undo':
            rows = [{**r, 'status': r['undo_status'], 'error': r.get('undo_error', '')}
                    for r in summary['details']]
            self.summary.setText(f'撤销完成   恢复 {summary["restored"]}   跳过 {summary["skipped"]}   失败 {summary["errors"]}')
            self.last_execution_csv = ''
        else:
            rows = summary['results']
            if mode == 'execute':
                self.last_execution_csv = summary['csv_path']
            label = '预览完成' if mode == 'preview' else '重命名完成'
            count_label = '可执行' if mode == 'preview' else '成功'
            self.summary.setText(f'{label}   共 {summary["files_count"]} 个文件   {count_label} {summary["ok_count"]}   冲突 / 失败 {summary["error_count"]}')
        self.model.replace(rows)
        path = Path(summary['csv_path']).resolve()
        self.csv_link.setText(f'CSV 已生成：<a href="{html.escape(QUrl.fromLocalFile(str(path)).toString(), quote=True)}">{html.escape(path.name)}</a>')
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.status.setText(summary.get('warning') or '完成')

    def on_failure(self, message):
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.summary.setText('任务未完成')
        self.status.setText(message)

    def on_finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.set_busy(False)

    def closeEvent(self, event):
        if self.worker is not None:
            event.ignore()
            self.status.setText('任务仍在进行，请等待结果确定后关闭窗口。')
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = RenamerWindow()
    window.show()
    if '--smoke-test' in sys.argv:
        from desktop_smoke import run_smoke
        return run_smoke(app, window, sys.argv[sys.argv.index('--smoke-test') + 1])
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
