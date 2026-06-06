# Rename Organize Pictures 后续交接任务

更新时间：2026-06-06

## 当前状态

本轮已经优先完成了底层安全审计修复和第一版 Textual TUI 骨架。当前仓库仍处于未提交状态，主要改动文件包括：

- `.gitignore`
- `photo_renamer.py`
- `photo_renamer_tui.py`
- `requirements-tui.txt`
- `tests/`

已验证通过：

```powershell
python -m unittest discover -s tests
python -m py_compile photo_renamer.py photo_renamer_tui.py tests\test_safety.py tests\test_tui.py
python photo_renamer.py -s 测试集 -m preview --csv preview_report.csv
```

注意：`preview_report.csv`、`rename_history.csv` 等运行产物应保持忽略，不要提交。

## 已完成内容

### 1. 安全审计修复

已在 `photo_renamer.py` 中完成：

- 超时执行不再被 `ThreadPoolExecutor.__exit__` 阻塞。
- 输出文件名格式增加安全校验，阻止路径穿越、路径分隔符、Windows 保留名、尾部空格/点、控制字符。
- CSV 写入增加公式注入防护。
- `patterns.json` 增加正则配置校验，降低灾难性回溯和错误分组风险。
- 复制模式下如果目标文件已存在，会标记冲突，不再静默覆盖。
- 交互菜单退出逻辑修复。
- 重命名日志增加 `original,new_name,date,source,status,dst,error` 字段。

### 2. 撤销能力

已实现：

- `undo_from_csv(csv_path, force=False)`
- `write_undo_report(csv_path, details)`
- CLI 参数：
  - `--undo-csv`
  - `--undo-force`

撤销逻辑按 CSV 逆序恢复，只处理成功重命名记录。旧 CSV 如果没有 `dst` 字段，会尝试通过原目录和 `new_name` 推导目标路径。

### 3. TUI 服务层

已实现供 Textual 调用的服务层：

- `RenameJobOptions`
- `run_rename_job(options)`
- `discover_rule_suggestions(source_dir, recursive=False, ext_arg='')`
- `add_pattern_suggestion(signature, config_path='')`
- `load_format_profiles(config_path='')`
- `save_format_profile(name, fmt, config_path='', make_current=False)`
- `append_history_report(summary, history_path='')`
- `load_history_reports(history_path='')`

### 4. 第一版 Textual TUI

已新增 `photo_renamer_tui.py`，并在 `photo_renamer.py` 中加入：

- `--tui`
- `launch_tui()`

当前界面已有：

- 源文件夹输入框。
- “包含子目录”复选框，默认选中。
- 文件名格式输入框。
- CSV 日志路径输入框。
- 预览按钮。
- 重命名按钮。
- 撤销 CSV 按钮。
- 未知规则确认按钮。
- 文件名格式管理按钮。
- 历史报告按钮。
- 结果表格。
- 状态区和摘要区。

## 后续必须完成的任务

### 任务 1：修复 TUI 文件中的中文乱码

当前 `photo_renamer_tui.py` 里有明显乱码文本，原因大概率是前面工具输出或补丁过程中编码显示异常。需要把所有界面中文替换为正常 UTF-8 文本。

验收标准：

- `photo_renamer_tui.py` 文件保存为 UTF-8。
- 界面按钮、提示、快捷键说明、表头全部显示正常中文。
- `python -m py_compile photo_renamer_tui.py` 通过。

### 任务 2：补齐未知规则逐条确认并写入 JSON

当前状态：

- `discover_rule_suggestions()` 已能找出候选规则。
- `add_pattern_suggestion()` 已能写入配置。
- TUI 目前只展示候选规则，还没有逐条确认动作。

需要实现：

- 规则候选列表可选择当前行。
- 增加“加入规则”按钮或快捷键。
- 用户选中某条候选规则后，调用 `add_pattern_suggestion(signature)` 写入 `patterns.json`。
- 写入后刷新列表，避免重复添加。
- 状态区显示写入结果和配置文件路径。

验收标准：

- 在测试目录放入未知命名格式文件后，TUI 能发现候选规则。
- 选中候选规则并确认后，`patterns.json` 中出现对应规则。
- 再次扫描时不会重复写入同一 signature。
- 增加单元测试或 Textual `run_test()` 测试覆盖。

### 任务 3：补齐文件名格式管理

当前状态：

- `load_format_profiles()` 可读取内置和自定义格式。
- `save_format_profile()` 可保存格式并设置当前格式。
- TUI 目前只展示列表，不能新增、保存、设为当前。

需要实现：

- 增加格式名称输入框。
- 增加格式表达式输入框，或复用已有 `format_input`。
- 增加“保存格式”按钮。
- 增加“设为当前”按钮或快捷键。
- 选中已有格式时，应能填充到当前格式输入框。
- 保存时调用 `save_format_profile(name, fmt, make_current=True/False)`。

建议支持的表达式说明：

- 继续使用项目现有格式语法，不要另造一套 DSL。
- 界面里可以用短提示展示示例，但不要写成长篇说明。

验收标准：

- 用户可以新增多个自定义格式。
- 用户可以选择一个作为当前使用格式。
- 当前格式能被 `run_rename_job()` 使用。
- `patterns.json` 或当前配置文件中能持久化保存。
- 增加测试覆盖保存、读取、设为当前。

### 任务 4：补齐历史报告的“跳转/索引”体验

当前状态：

- `append_history_report()` 会记录轻报告。
- `load_history_reports()` 可以读取历史。
- TUI 能显示历史表格，但 CSV 路径只是普通文本。

需要实现：

- 历史报告表格显示：
  - 命名时间
  - 模式
  - 文件夹
  - 文件数量
  - 成功数量
  - 问题数量
  - CSV 路径
- 选中历史行后，支持打开 CSV 所在位置或复制/显示完整路径。

跨平台注意：

- Windows 可用 `os.startfile(path.parent)`。
- macOS 可用 `open <folder>`。
- Linux 可用 `xdg-open <folder>`。
- 这类外部打开动作要封装成函数，便于测试；测试里不要真的打开文件管理器。

验收标准：

- 历史报告能稳定加载。
- 选中某行后可定位到 CSV 文件所在文件夹。
- Windows/macOS/Linux 分支不互相影响。
- 对不存在的 CSV 路径给出清晰错误提示。

### 任务 5：优化进度、预览、执行结果的视觉结构

用户明确反馈当前进度条、预览和结果提醒比较凌乱。Textual 界面需要更规整。

建议布局：

- 左侧：输入和操作区。
- 右上：本次任务摘要。
- 右中：结果表格。
- 右下：状态/错误详情。

结果表格建议字段：

- 状态
- 原文件名
- 新文件名
- 日期
- 规则来源
- 错误原因

摘要区建议固定展示：

- 扫描文件数
- 可重命名数
- 冲突/错误数
- CSV 路径
- 历史记录路径

验收标准：

- 预览、重命名、撤销后的摘要格式一致。
- 错误和冲突不混在普通成功结果里。
- 表格列宽和内容在常见终端宽度下不明显溢出。
- 中文显示正常。

### 任务 6：跨平台打包方案整理与最小配置

用户希望后续扩展为 Windows、Linux、macOS 都可用。

建议方案：

- 短期：继续使用 PyInstaller。
- 每个平台在对应系统上构建对应平台产物。
- 用 GitHub Actions matrix 分别在：
  - `windows-latest`
  - `macos-latest`
  - `ubuntu-latest`
  上构建。

需要补充：

- `requirements.txt` 或 `requirements-tui.txt` 的依赖边界。
- PyInstaller spec 或明确的构建命令。
- GitHub Actions workflow 草案。
- 产物命名规则，例如：
  - `photo-renamer-windows-x64.zip`
  - `photo-renamer-macos-arm64.zip`
  - `photo-renamer-linux-x64.tar.gz`

注意：

- 不要承诺 Windows 可以直接交叉编译 macOS/Linux 可执行文件。通常要在目标平台或对应 CI runner 上构建。
- macOS 分发可能涉及签名和 notarization；个人内部使用可以先不做。
- Linux 桌面环境差异较大，TUI 版本比 GUI 版本更容易跨平台。

验收标准：

- 至少提供一份 `.github/workflows/build.yml` 草案。
- 本地 Windows PyInstaller 构建命令可运行。
- README 说明多平台构建机制和限制。

## 建议新增或调整的测试

已有测试在 `tests/test_safety.py` 和 `tests/test_tui.py`。

后续建议补充：

- TUI 规则确认写入 JSON。
- TUI 格式新增、设为当前、应用到预览。
- 历史报告路径打开函数的跨平台分支测试。
- 格式表达式非法时 TUI 能阻止执行并显示错误。
- 撤销 CSV 缺字段、路径不存在、目标冲突时的提示。

每次改完至少运行：

```powershell
python -m unittest discover -s tests
python -m py_compile photo_renamer.py photo_renamer_tui.py tests\test_safety.py tests\test_tui.py
```

如果改动 TUI，建议额外用：

```powershell
python photo_renamer.py --tui
```

手动检查中文显示、按钮布局、预览和撤销流程。

## 不要踩的坑

- 不要把 TUI 逻辑和底层重命名逻辑重新写两套。TUI 应调用 `photo_renamer.py` 的服务函数。
- 不要让重命名操作静默覆盖已有文件。
- 不要移除 CSV 注入防护。
- 不要把运行产物提交进 git。
- 不要强行用 PowerShell 写入中文文件，避免编码污染。优先使用 UTF-8 编辑器或 `apply_patch`。
- `launch.bat` 可能是 GBK 编码，之前没有强行修改，后续如需修改应先确认编码并整体转换策略。

## 给审核人的检查重点

后续其他 AI 完成后，需要重点审核：

- 是否破坏了已通过的安全修复。
- 是否绕过服务层，在 TUI 里重复实现重命名逻辑。
- 是否存在路径穿越、覆盖、CSV 注入、错误正则导致卡死等回归。
- Textual UI 是否真的可操作，而不是只做了静态展示。
- 多平台打包说明是否真实可执行，是否把“跨平台构建”和“交叉编译”混为一谈。
