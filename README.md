# Photo & Video Renamer v2.6

照片和视频**批量按日期重命名**工具。自动从多种来源提取拍摄时间，统一命名为 `YYYY.MM.DD_HHMM` 格式，支持 33 种媒体格式，针对网络盘（群晖 NAS 等）场景做了专项适配。

---

## 目录

1. [功能特性与优势](#功能特性与优势)
2. [架构流程](#架构流程)
3. [环境依赖与安装](#环境依赖与安装)
4. [部署到新电脑](#部署到新电脑)
5. [使用方法](#使用方法)
6. [识别算法详解](#识别算法详解)
7. [注意事项](#注意事项)
8. [版本历史](#版本历史)

---

## 功能特性与优势

### 核心能力

| 特性 | 说明 |
|------|------|
| **4 级日期提取优先级** | EXIF → 文件名日期模式 → Unix 时间戳 → 文件修改时间，层层兜底 |
| **19 种文件名模式** | 覆盖全品牌安卓手机、iPhone、大疆、单反微单、微信/QQ/微博/抖音等设备和应用 |
| **可编辑模式配置** | `patterns.json` 外部 JSON 配置，手动添加/修改日期规则，即改即生效 |
| **智能模式发现** | 处理完成后自动扫描未匹配文件，发现新命名规则并提示用户确认写入 patterns.json |
| **错误弹窗容错** | GUI 环境下 tkinter 弹窗显示错误信息，交互模式自动回到菜单，不会闪退 |
| **冲突智能处理** | 同名文件分钟自动 +1，含级联碰撞保护 |
| **Windows 副本处理** | 自动识别 `文件名 (2).jpg` 等合并文件夹产生的副本，分配邻近空闲时间槽 |
| **重复处理保护** | 检测已重命名目录，执行模式自动中止并提示，防止视频时分信息丢失 |
| **全格式支持** | 33 种媒体格式：20 种图片（含 RAW、HEIC）+ 13 种视频（含 MP4、MOV） |

### 技术优势

| 优势 | 说明 |
|------|------|
| **网络盘抗卡顿** | 全链路超时保护（EXIF 读取/stat/复制/重命名），适合群晖等 NAS 环境 |
| **渐进式 EXIF 读取** | 只读文件头部 256 KB，无 EXIF 立即回退，避免大文件下载浪费流量 |
| **哈希文件名防误判** | 32 字符以上的哈希类文件名自动跳过时间戳匹配 |
| **零依赖核心** | Pillow 是唯一可选依赖，缺失时 EXIF 不可用，但文件名/时间戳功能完全正常 |
| **绿色 exe 发布** | PyInstaller 打包的独立 exe（约 15 MB），无需安装 Python，双击即用 |
| **实时进度条** | 终端内联进度条，显示处理进度、耗时、超时跳过数 |
| **核心与 CLI 分离** | `PhotoRenamer` 类可直接被 GUI 调用，不依赖 argparse |
| **单文件架构** | 整个项目 1 个 Python 文件，复制即用 |

---

## 架构流程

```
用户双击 launch.bat / photo_renamer.exe（GBK 编码，CMD 原生稳定）
          │
          ├─ 检查 Python → 安装指引
          ├─ 选择模式（预览 / 执行 / 副本整理 / 自定义）
          ├─ 输入或拖放文件夹路径
          │
          ▼
python photo_renamer.py -s <文件夹> -m <模式>
          │
          ├─ DateExtractor     日期提取引擎（4 级优先级链）
          │    ├─ EXIF DateTimeOriginal  ← 相机写入，最准确
          │    ├─ 文件名日期（19 种模式）← patterns.json 配置
          │    ├─ Unix 时间戳（10位/13位 + App 前缀）
          │    └─ 文件修改时间           ← 最终兜底
          │
          ├─ PatternDiscoverer  智能模式发现（--discover / 交互模式自动触发）
          │    6 阶段启发式扫描 → 生成建议 JSON 条目 → 用户确认 → 写入 patterns.json
          │
          ├─ PhotoRenamer      重命名引擎
          │    ├─ scan_files()      扫描所有媒体文件
          │    ├─ process()         冲突处理 + 邻近 slot 分配
          │    └─ execute()         原地重命名 / 复制到 output
          │
          ├─ _show_error()     错误弹窗（tkinter messagebox，防止闪退）
          │
          └─ run_with_timeout()  全链路 I/O 超时保护
               ThreadPoolExecutor，默认 15 s，可通过环境变量调整
```

### 核心类职责

| 类 / 函数 | 职责 | 关键特性 |
|-----------|------|---------|
| `DateExtractor` | 日期提取引擎 | 4 级优先级链，纯类方法，延迟加载模式 |
| `PatternDiscoverer` | 智能模式发现 | 6 阶段启发式扫描，支持泛化分隔符 / 时间戳 / App 前缀 |
| `PhotoRenamer` | 重命名引擎 | 扫描、冲突处理（分钟递增 + 级联保护）、CSV 导出 |
| `ProgressBar` | 终端进度条 | 零依赖，`\r` 原地刷新，非交互模式逐行输出 |
| `run_with_timeout()` | 超时保护 | ThreadPoolExecutor，默认 15 s，环境变量配置 |
| `_show_error()` | 错误弹窗 | tkinter messagebox 显示错误，终端 fallback 到 print |
| `_offer_new_patterns()` | 智能模式追加 | 处理后扫描未匹配文件，用户确认后写入 patterns.json |

---

## 环境依赖与安装

以下是在**全新 Windows 电脑**上从零配置运行本工具的全部步骤。

### 依赖汇总

| 依赖 | 用途 | 必需 | 安装命令 |
|------|------|------|----------|
| Python 3.8 或更高版本 | 运行环境 | **是** | Microsoft Store 或 python.org |
| Pillow | 图片 EXIF 读取 | 强烈推荐 | `pip install Pillow` |
| pillow-heif | iPhone HEIC 照片解码 | 可选 | `pip install pillow-heif` |

> 除上述之外，**零额外依赖**——所有功能（文件名解析、时间戳识别、进度条、CSV 导出、网络超时保护）均使用 Python 标准库实现。

### 步骤 1：安装 Python

本工具需要 **Python 3.8 或更高版本**（推荐 3.10+，`typing` 注解和 `pathlib` 行为更稳定）。

**推荐方式（Microsoft Store）**：
- 打开 Microsoft Store，搜索 `Python 3.12` 或 `Python 3.13`，点击安装
- Store 版本会自动配置 PATH，安装后无需额外操作

**备选方式（官网安装包）**：
1. 访问 https://www.python.org/downloads/，下载最新 Windows Installer
2. 运行安装程序，**务必勾选底部的 "Add Python to PATH"** 复选框
3. 点击 "Install Now" 完成安装

**验证安装**（打开命令提示符或 PowerShell）：

```cmd
python --version
```

应输出类似 `Python 3.12.x` 的信息。如果提示找不到命令，重启电脑后再试。

> **版本说明**：Python 3.8 是理论最低版本（代码使用了 `typing.Optional`、`typing.Tuple` 等，3.8 以上均可运行）。实际测试在 3.10 / 3.12 / 3.13 上进行，推荐使用这些版本。

### 步骤 2：安装 Pillow（强烈推荐）

Pillow 用于读取照片的 EXIF 拍摄时间，是唯一需要额外安装的第三方包。**缺失时程序仍可运行，但所有图片的 EXIF 信息会被跳过**，日期提取将回退到文件名模式或修改时间。

```cmd
pip install Pillow
```

验证安装：

```cmd
python -c "from PIL import Image; print('Pillow OK')"
```

### 步骤 3：下载工具文件

将以下 3 个文件放到同一个文件夹即可：

```
photo_renamer/
├── photo_renamer.py    # 核心脚本（DateExtractor + PhotoRenamer + CLI）
├── launch.bat          # Windows 交互式启动器（GBK 编码，中文菜单）
├── patterns.json       # 日期模式配置文件（19 种模式，可手动编辑）
```

> `README.md` 为说明文档，`.workbuddy/` 和 `测试集/` 为本地开发数据，均不需要复制。

首次运行时，若 `patterns.json` 不存在，程序会自动生成默认配置文件。

### 步骤 4：（可选）HEIC 照片支持

如果照片中含有 `.heic` 格式（iPhone 默认拍摄格式），需要额外安装 HEIC 解码插件：

```cmd
pip install pillow-heif
```

安装后无需任何配置，程序自动识别并处理 HEIC 文件。

---

## 部署到新电脑

### 前置条件

| 条件 | 说明 | 必需 |
|------|------|------|
| Windows / macOS / Linux | 全平台支持（launch.bat 仅限 Windows） | ✅ |
| Python 3.8+ | https://python.org/downloads/ | ✅ |
| Pillow | `pip install Pillow`，用于 EXIF 读取 | 推荐 |
| pillow-heif | `pip install pillow-heif`，用于 iPhone HEIC | 可选 |

### 一键部署

**方式 A：绿色 exe（推荐，免安装 Python）**

```
win/
├── photo_renamer.exe    # 独立可执行文件（约 15 MB，已内含 Python）
└── patterns.json        # 日期模式配置文件（19 种模式，可手动编辑）
```

将 `win/` 文件夹复制到任意位置，**双击 `photo_renamer.exe`** 即可进入交互菜单。无需安装 Python 或任何依赖。

> 注意：exe 内不含 Pillow，因此**图片 EXIF 读取不可用**。文件名模式和时间戳提取功能正常。如需 EXIF 支持，请使用方式 B（Python 运行）。

**方式 B：Python 源码运行**

```bash
# 1. 复制项目文件（共 3 个文件）
#    photo_renamer.py  launch.bat  patterns.json

# 2. 安装依赖（推荐）
pip install Pillow

# 3a. 双击 launch.bat（Windows 推荐，交互式菜单）
# 3b. 或命令行运行
python photo_renamer.py -s "D:\照片" -m preview
```

> **方式 A vs B 对比**：exe 适合快速在不同电脑上使用（无需安装），但缺少 EXIF 支持。Python 方式功能更完整，推荐长期使用。

### 网络盘（群晖 NAS）使用

本工具针对网络盘场景做了特殊适配：

- **I/O 超时保护**：所有文件操作（EXIF 读取、stat、复制、重命名）均带超时，默认 15 秒
- **渐进式 EXIF**：只读取文件头部 256 KB，避免大文件下载浪费流量
- **超时计数**：进度条显示 `⏱超时:N`，可了解有多少文件因网络延迟被跳过

**自定义超时时长**：

```cmd
REM CMD（单位：秒）
set PHOTO_RENAMER_TIMEOUT=30
python photo_renamer.py -s "\\NAS\照片" -m preview
```

```powershell
# PowerShell
$env:PHOTO_RENAMER_TIMEOUT = "60"
python photo_renamer.py -s "\\NAS\照片" -m preview
```

---

## 使用方法

### launch.bat / exe 交互式菜单（Windows 推荐）

**双击 exe**（`win/photo_renamer.exe`）或 **launch.bat**，均进入交互菜单：

```
[1] 预览 - 单个文件夹
[2] 预览 - 含所有子文件夹
[3] 执行 - 单个文件夹（直接重命名）
[4] 执行 - 含所有子文件夹（直接重命名）
[5] 自定义参数运行
[6] 整理重复文件名 - 预览
[7] 整理重复文件名 - 执行
[0] 退出
```

> `launch.bat` 为 **GBK 编码**，在 Windows 简体中文系统上双击即可正常显示中文菜单。在非中文 Windows 上菜单可能乱码，但不影响功能——直接使用 CLI 命令即可。

选项 [3]/[4] 执行时，若检测到目录含已重命名文件，bat 会自动询问"是否强制继续？"，输入 `yes` 后自动追加 `--force` 重新执行，无需手动输入完整命令。

### CLI 参数

```
python photo_renamer.py -s <源文件夹> [选项]

必需参数:
  -s, --source       源文件夹路径

可选参数:
  -m, --mode         模式: preview（预览，默认）/ execute（执行）
  -r, --recursive    递归处理子目录
  -f, --format       输出格式，预设名或自定义 strftime 格式字符串
  -o, --output-dir   输出目录（复制模式），不指定则原地重命名
  --csv              导出 CSV 路径（默认自动生成）
  -e, --ext          限制扩展名，逗号分隔，如 ".jpg,.mp4"
  -F, --force        强制执行，跳过"已重命名目录"安全检查
  --discover         智能发现模式：扫描未匹配文件中的潜在日期格式
  --dedup            副本整理模式：将 (1)/(2) 副本分配到邻近空闲时间槽
  --pattern-config   自定义 patterns.json 路径（默认自动查找）
  --generate-config  在当前目录生成默认 patterns.json 后退出
```

### 输出格式预设

| 预设名 | Python strftime 格式 | 示例输出 |
|--------|---------------------|----------|
| `默认` | `%Y.%m.%d_%H%M` | `2026.05.26_1021.jpg` |
| `精确到秒` | `%Y.%m.%d_%H%M%S` | `2026.05.26_102130.jpg` |
| `中划线分隔` | `%Y-%m-%d_%H-%M-%S` | `2026-05-26_10-21-30.jpg` |
| `紧凑型` | `%Y%m%d_%H%M%S` | `20260526_102130.jpg` |

也支持自定义 Python strftime 格式，例如 `%%Y年%%m月%%d日_%%H%%M`（Windows CMD 下 `%` 需双写为 `%%`）。

### 常用示例

```bash
# 预览当前文件夹（不改文件，输出对照表 + CSV）
python photo_renamer.py -s "D:\照片" -m preview

# 预览 + 递归子目录 + 精确到秒格式
python photo_renamer.py -s "D:\照片" -m preview -r -f "精确到秒"

# 执行重命名（原地修改）
python photo_renamer.py -s "D:\照片" -m execute

# 复制到 output 目录（原文件保持不动）
python photo_renamer.py -s "D:\照片" -m execute -o "D:\已整理"

# 只处理 .jpg 和 .mp4
python photo_renamer.py -s "D:\照片" -m preview -e ".jpg,.mp4"

# 强制执行（对已重命名过的目录重新处理时需加 --force）
python photo_renamer.py -s "D:\已整理" -m execute --force
```

### 重复处理保护

当目录中检测到文件已被本工具重命名过时：

- **预览模式**：显示警告 ⚠，但仍正常运行（方便检查结果）
- **执行模式**：自动中止 ⛔，要求使用 `--force` 确认后才能继续

这是为了防止视频文件因缺少 EXIF、回退到文件名解析时时分信息丢失（如 `2025.06.30_1402.mp4` 被错误重命名为 `2025.06.30_0000.mp4`）。

```bash
# 场景：目录已处理过，需要重新整理
python photo_renamer.py -s "D:\已整理" -m preview        # 先预览，检查结果
python photo_renamer.py -s "D:\已整理" -m execute --force  # 确认无误后执行
```

### 模式配置文件（patterns.json）

v2.0 起，日期识别模式改为外部 JSON 配置，支持手动编辑：

```json
{
  "version": "2.6",
  "patterns": [
    {
      "id": 16,
      "regex": "(\\d{4})(\\d{2})(\\d{2})",
      "group_count": 3,
      "description": "YYYYMMDD（纯 8 位日期，如 20220502（1））",
      "is_own_output": false
    }
  ]
}
```

**字段说明**：

| 字段 | 说明 |
|------|------|
| `regex` | Python 正则表达式，捕获组按 **年/月/日/时/分/秒** 顺序（或通过 `group_order` 指定） |
| `group_count` | 3 = 仅日期；5 = 日期+时分；6 = 日期+时分秒 |
| `is_own_output` | `true` 表示该格式用于"已重命名"检测（精确锚定匹配） |
| `group_order` | 可选，捕获组语义顺序：`Y`年 `M`月 `D`日 `h`时 `m`分 `s`秒，默认 `YMDhms` |

添加新模式时，按优先级排列（精确的在前），保存后重新运行即可生效。

**管理命令**：

```bash
# 生成默认配置文件
python photo_renamer.py --generate-config

# 使用自定义配置文件路径
python photo_renamer.py -s "D:\照片" -m preview --pattern-config "D:\my_patterns.json"
```

### 智能模式发现

本工具支持两种智能发现方式：

**方式 1：CLI `--discover` 模式**（批量扫描 + 导出 CSV）

自动扫描未匹配文件，检测潜在日期格式，生成建议 JSON 条目：

```bash
# 扫描目录，发现未知日期格式
python photo_renamer.py --discover -s "D:\照片" -r

# 导出发现报告到指定 CSV
python photo_renamer.py --discover -s "D:\照片" -r --csv discover.csv
```

工作流程：

1. 先用现有 19 种模式匹配，统计覆盖率
2. 对未匹配文件用启发式算法扫描（Y-M-D / D-M-Y / 紧凑型 / 混合分隔符 / Unix 时间戳）
3. 按模式签名分组展示，标注歧义格式 ⚠
4. 自动生成建议的 JSON 配置条目
5. 导出 CSV 供人工核对

核查无误后，将建议条目复制到 `patterns.json` 的 `"patterns"` 数组中，重新运行即可识别新模式。

**方式 2：交互模式自动触发**（exe 双击 / 无参数运行）

使用交互菜单或双击 exe 处理完文件后，程序会自动扫描未匹配的文件。如果发现潜在的日期格式，会展示给用户确认——输入 `y` 即可自动写入 `patterns.json` 并热加载，下次运行直接生效。无需手动编辑配置文件。

---

## 识别算法详解

### 日期提取优先级链

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1 | EXIF DateTimeOriginal | 相机/手机写入的原始拍摄时间，最准确；仅图片文件读取，视频跳过 |
| 2 | 文件名日期模式 | 自动识别 19 种预设模式（见下表），按优先级顺序逐个匹配 |
| 3 | Unix 时间戳 | 文件名中的 13 位毫秒或 10 位秒级时间戳，年份限定在 2000-2099 |
| 4 | 文件修改时间 | 以上全部失败时的兜底方案 |

> **视频文件**不通过 Pillow 读取元数据，日期完全依赖文件名模式、Unix 时间戳和修改时间，因此即使视频文件体积很大也不会消耗下载流量。

### 文件名日期模式（优先级 2）

按精度从高到低排列，先匹配到的直接使用：

| ID | 格式 | 示例文件名 |
|----|------|-----------|
| 1–3 | 自有输出格式（完整锚定） | 本工具已重命名文件，高精度回读 |
| 4 | `YYYYMMDD_HHMMSS` | `petal_20230928_143520.mp4` |
| 5 | `YYYYMMDD_HHMM` | `PIC_20240831_102102854.jpg` |
| 6 | `YYYY-MM-DD_HHMMSS` | `微信图片_2025-06-30_140243_253.jpg` |
| 7 | `YYYY-MM-DD_HHMM` | — |
| 8 | `YYYYMMDD-HHMMSS` | — |
| 9 | `YYYY-MM-DD-HH-MM-SS` | `Screenshot_2024-06-15-14-30-22.png` |
| 10 | `YYYY-MM-DD HH-MM-SS` | `2015-12-04 15-39-11-desc.jpg`（空格分隔） |
| 11 | `YYYY_MM_DD_HHMMSS` | `HwVideoEditor_2020_08_22_175855.mp4` |
| 17 | `YYYY-MM-DD-HHhMMmSS` | `2016-04-03-20h52m50.JPG` |
| 18 | `YYYY-MM-DD-HHMMSSmmm` | `2019-12-16-181315963.mp4` |
| 12 | `YYYY年MM月DD日 HH点MM分` | `2023年07月25日 07点39分_2.jpg` |
| 13 | `YYYY.MM.DD` | `2025.01.15-desc.mp4`（仅日期，时分默认 `0000`） |
| 14 | `YYYY-MM-DD` | `2023-07-10-desc.mp4`（仅日期，时分默认 `0000`） |
| 15 | `YYYYMMDDHHMMSS` | `IMG20220625102520.jpg`、`faceu_*_20201024204541523.jpg` |
| 16 | `YYYYMMDD` | `20220502（1）.mp4`、`20220502.mp4`（仅日期） |
| 19 | `DD-MM-YYYY HHMMSS` | `ScreenRecording_02-06-2026 154022.mp4`（iPhone 录屏，`group_order: DMYhms`） |

> **date-only 模式**（ID 13、14、16）：仅提取到日期时，时分默认设为 `0000`。

### Unix 时间戳支持（优先级 3）

| 类型 | 位数 | 示例文件名 |
|------|------|-----------|
| 毫秒级 | 13 位 | `mmexport1664505286518.mp4`、`wx_camera_1718166753538.jpg` |
| 秒级 | 10 位 | 较少见，同样兼容 |
| App 前缀嵌入 | 13 位（含前缀） | `Camera_XHS_1779195734185xxx.jpg`（小红书相机） |

> **防误判**：32 字符以上的哈希文件名（如 `2d204843ed04114377f24af0c49045cb.jpg`）不会被误匹配为时间戳。

### 模式排序规则（重要：避免被低精度模式截获）

模式匹配按 **JSON 数组中的出现顺序**执行。代码对每个模式用 `finditer()` 扫描文件名，**一旦找到合法日期就立即返回**，不再继续尝试后续模式。

因此：**宽泛的模式排在精确模式前面，会"截获"本应被后面精确模式匹配的文件名，导致时分信息丢失。**

#### 核心规则

| 规则 | 说明 |
|------|------|
| **精度优先** | 6 组（秒级）> 5 组（分级）> 3 组（日期级）。精确模式必须排在模糊模式前 |
| **自有格式置顶** | `is_own_output: true` 的模式（ID 1–4）排在最前，使用 `^...$` 完整锚定 |
| **分隔符区分** | 同精度下，分隔符特征越独特越靠前 |

#### 示例：为什么模式 17 必须在模式 14 前面？

```
文件名: 2016-04-03-20h52m50.JPG

❌ 错误排列（模式 14 在 17 之前）：
  → 模式 14 先匹配到 "2016-04-03" → 提取 2016-04-03 00:00:00（时分秒丢失！）

✅ 正确排列（模式 17 在 14 之前）：
  → 模式 17 匹配到 "2016-04-03-20h52m50" → 提取 2016-04-03 20:52:50
  → 模式 14 不再被尝试
```

#### 当前 19 个模式的正确排列顺序

```
[自有输出格式]  ← 精确锚定，最先匹配
  ID 1:  YYYY.MM.DD_HHMMSS        (6组, ^...$)
  ID 2:  YYYY.MM.DD_HHMM          (5组, ^...$)
  ID 3:  YYYY-MM-DD_HH-MM-SS      (6组, ^...$)
  ID 4:  YYYYMMDD_HHMMSS          (6组, ^...$)  ← 自有格式结束

[外部 6 组精确模式]  ← 时分秒完整
  ID 5:  YYYYMMDD_HHMM            (5组)
  ID 6:  YYYY-MM-DD_HHMMSS        (6组)
  ID 7:  YYYY-MM-DD_HHMM          (5组)
  ID 8:  YYYYMMDD-HHMMSS          (6组)
  ID 9:  YYYY-MM-DD-HH-MM-SS      (6组)  ← 全中划线
  ID 10: YYYY-MM-DD HH-MM-SS      (6组)  ← 空格分隔
  ID 11: YYYY_MM_DD_HHMMSS        (6组)  ← 全下划线
  ID 17: YYYY-MM-DD-HHhMMmSS      (6组)  ← h/m 标记
  ID 18: YYYY-MM-DD-HHMMSSmmm     (6组)  ← 毫秒后缀
  ID 15: YYYYMMDDHHMMSS           (6组)  ← 全紧凑型，放最后（纯数字，误匹配风险最高）
  ID 19: DD-MM-YYYY HHMMSS       (6组)  ← iPhone录屏（group_order: DMYhms）

[5 组 / 3 组低精度模式]  ← 只有日期+分或纯日期
  ID 12: YYYY年MM月DD日 HH点MM分   (5组)
  ID 13: YYYY.MM.DD               (3组)  ← date-only
  ID 14: YYYY-MM-DD               (3组)  ← date-only，与所有中划线模式前缀重叠，放最后
  ID 16: YYYYMMDD                 (3组)  ← date-only
```

#### 新增模式时的插入自查清单

1. **这是自有输出格式吗？** → 插入到 ID 4 之后、ID 5 之前，并设 `is_own_output: true`
2. **group_count 是多少？** → 按优先级段插入：6 组 → 外部精确模式段，5 组 → 5 组段，3 组 → date-only 段末尾
3. **是否与已有模式存在正则前缀重叠？** → 如果新模式是某已有模式的子集，新模式必须排在已有模式**之后**
4. **添加后运行回归测试**：`python photo_renamer.py -s "测试集" -m preview`，确认无回归

> 心智模型：**"能匹配到更精确时间的模式更靠前"**。6 组 > 5 组 > 3 组，同组内特征越独特越靠前。

### 冲突处理机制

同一目录下，多个文件映射到相同 `YYYY.MM.DD_HHMM` 时：

- **v1.1+ 逻辑**：分钟自动递增（`_1020` → `_1021` → `_1022`）
- ~~旧逻辑：加序号后缀 `_1020(2).jpg`~~（v1.1 废弃）

**级联保护**：如果递增后的 stem 已被其他文件占用，继续递增直到唯一——例如 `1258` 和 `1259` 同时冲突时，第三个文件会跳过 `1259` 直接分配到 `1300`。

### 支持的媒体格式

**图片（20 种）**：`.jpg` `.jpeg` `.png` `.gif` `.bmp` `.tiff` `.tif` `.webp` `.heic` `.heif` `.raw` `.cr2` `.nef` `.arw` `.dng` `.orf` `.rw2` `.raf` `.sr2` `.pef`

**视频（13 种）**：`.mp4` `.mov` `.avi` `.mkv` `.3gp` `.wmv` `.flv` `.webm` `.m4v` `.mts` `.m2ts` `.ts` `.mxf`

### 设备与应用兼容性

本工具通过文件名模式、Unix 时间戳和 EXIF 三条路径提取日期，兼容以下设备与应用：

#### 安卓手机（全品牌通用）

| 品牌 | 相机照片/视频 | 系统截图 | 系统录屏 | 提取方式 |
|------|-------------|---------|---------|---------|
| 小米 / Redmi | `IMG_YYYYMMDD_HHMMSS`、`PXL_YYYYMMDD_HHMMSS_XXXX` | `Screenshot_YYYYMMDD-HHMMSS` | `Screenrecorder_YYYYMMDD_HHMMSS` | 文件名模式 |
| 华为 / 荣耀 | `IMG_YYYYMMDD_HHMMSS` | `Screenshot_YYYYMMDD-HHMMSS` | `ScreenRecord_YYYYMMDD_HHMMSS` | 文件名模式 |
| vivo / iQOO | `IMG_YYYYMMDD_HHMMSS` | `Screenshot_YYYYMMDD-HHMMSS` | `ScreenRecord_YYYYMMDD_HHMMSS` | 文件名模式 |
| OPPO / 一加 / realme | `IMG_YYYYMMDD_HHMMSS` | `Screenshot_YYYYMMDD-HHMMSS` | `ScreenRecord_YYYYMMDD_HHMMSS`（一加特例：`YYYY-MM-DD-HH-MM-SS`） | 文件名模式 |
| 中兴 / 魅族 | `IMG_YYYYMMDD_HHMMSS` | `Screenshot_YYYYMMDD-HHMMSS` | `ScreenRecord_YYYYMMDD_HHMMSS` | 文件名模式 |

> 安卓全品牌共享 `IMG_`/`VID_`/`Screenshot_`/`ScreenRecord` 前缀规范，工具已完整覆盖。

#### 苹果 iPhone

| 类型 | 命名规则 | 提取方式 |
|------|---------|---------|
| 相机照片 | `IMG_XXXX.HEIC`（4 位序列号，无日期） | **EXIF**（需 Pillow + pillow-heif） |
| 相机视频 | `IMG_XXXX.MP4`（无日期） | **文件修改时间**（无 EXIF） |
| 系统截图 | `IMG_XXXX.PNG`（无日期） | **EXIF**（iOS 15+ 截图含 EXIF） |
| 系统录屏 | `ScreenRecording_DD-MM-YYYY HHMMSS.mp4` | **文件名模式**（ID 19，`DMYhms`） |

> iPhone 原生相机照片/视频文件名不含日期信息，依赖 EXIF 或文件修改时间。建议安装 Pillow + pillow-heif 以获得最佳效果。

#### 三星手机

| 类型 | 命名规则 | 提取方式 |
|------|---------|---------|
| 相机/视频 | `IMG_YYYYMMDD_HHMMSS` | 文件名模式 |
| 截图 | `Screenshot_YYYYMMDD-HHMMSS` | 文件名模式 |
| 录屏 | `ScreenRecord_YYYYMMDD_HHMMSS` | 文件名模式 |

#### 大疆（DJI）运动相机 / 无人机

| 类型 | 命名规则 | 提取方式 |
|------|---------|---------|
| 新款（Action 3/4/5、Pocket 3 等） | `DJI_YYYYMMDDHHMMSS_XXXX_D` | 文件名模式（14 位紧凑时间戳） |
| 老款（Action 2、Pocket 2 等） | `DJI_XXXX`（纯序列号） | **EXIF** / 文件修改时间 |

#### 单反 / 微单相机

| 品牌 | 命名规则 | 提取方式 |
|------|---------|---------|
| 佳能 EOS/R | `IMG_XXXX.CR3`、`MVI_XXXX.MOV` | **EXIF** |
| 尼康 D/Z | `DSC_XXXX.NEF` | **EXIF** |
| 索尼 A/RX | `DSCXXXX.ARW` | **EXIF** |
| 富士 X/GFX | `DSCF_XXXX.RAF` | **EXIF** |
| 松下 GH/S | `PXXXXXXX.RW2` | **EXIF** |

> 单反/微单文件名均为序列号，不含日期。工具通过 EXIF 提取拍摄时间，需安装 Pillow。

#### 社交软件保存资源

| 应用 | 图片命名规则 | 视频命名规则 | 提取方式 |
|------|------------|------------|---------|
| 微信 | `mmexport` + 13 位时间戳 | 13 位纯数字 | Unix 时间戳（ms） |
| 微信拍摄 | `wx_camera_` + 13 位时间戳 | 同左 | Unix 时间戳（ms） |
| QQ | `QQImage_YYYYMMDD_HHMMSS` | `QQVideo_YYYYMMDD_HHMMSS` | 文件名模式 |
| 微博 | `weibo_YYYYMMDD_HHMMSS` | `weibo_video_YYYYMMDD_HHMMSS` | 文件名模式 |
| 抖音 | `douyin_YYYYMMDD_HHMMSS` | `抖音_YYYYMMDD_HHMMSS` | 文件名模式 |
| 小红书 | `Camera_XHS_` + 13 位时间戳（嵌入长数字串） | — | Unix 时间戳（ms，App 前缀） |

> QQ 截图保存为 `Screenshot_随机字符.png`，不含日期信息，需依赖文件修改时间。

---

## 注意事项

- **预览优先**：建议始终先跑 `preview` 模式，确认结果无误后再执行 `execute`
- **扩展名统一小写**：所有输出文件扩展名强制转为小写（如 `.JPG` → `.jpg`）
- **视频 EXIF**：视频文件不通过 Pillow 读取元数据，日期完全依赖文件名模式或修改时间
- **年份范围**：有效性检查限定 1970–2099，超出范围的数字串不会被识别为日期
- **bat 编码**：`launch.bat` 为 GBK 编码，在非中文 Windows 系统上菜单可能乱码，但不影响功能，可改用 CLI 命令
- **重复处理**：对已重命名的目录重新执行需加 `--force`，防止视频时分信息被错误覆盖
- **Pillow 降级**：未安装 Pillow 时，图片 EXIF 不可用，但文件名 / 时间戳提取功能完全正常
- **HEIC 支持**：需要额外安装 `pillow-heif`，见"环境依赖与安装"章节
- **exe 绿色版**：`win/photo_renamer.exe` 内不含 Pillow，图片 EXIF 不可用；文件名模式和时间戳提取功能正常

---

## 版本历史

### v2.6（2026-06-02）

- 新增 `_show_error()` 错误弹窗：GUI 环境下用 tkinter messagebox 显示错误，防止双击 exe 时闪退看不到原因
- 新增 `_offer_new_patterns()` 智能模式追加：处理完成后自动扫描未匹配文件，发现新命名规则后提示用户确认写入 patterns.json
- `main()` 重构为 `while True` 循环：交互模式下错误/完成都回到菜单，三层异常兜底（try/except → SystemExit → `__name__` guard）
- 新增交互式菜单（内置到 Python 脚本），双击 exe 直接进入菜单，无需 launch.bat
- 新增 `build.bat` + `photo_renamer.spec`，支持一键构建绿色 exe
- 修复多处不可达代码（`sys.exit()` 在 `is_interactive` 检查之前导致后者永远不执行）

### v2.5（2026-06-02）

- 新增 iPhone 录屏 `ScreenRecording_DD-MM-YYYY HHMMSS` 格式支持（`group_order: DMYhms`）
- 引入 `group_order` 机制，支持非标准日期顺序的捕获组映射（`Y/M/D/h/m/s` 语义编码）
- 模式总数增至 19 种，新增设备兼容性文档
- patterns.json 版本号升至 v2.5

### v2.4（2026-05-26）

- 新增小红书 `Camera_XHS_` 前缀的 13 位毫秒 Unix 时间戳提取（时间戳嵌入在更长数字串中）
- `--discover` 模式新增 Unix 时间戳自动检测（10 位秒级 / 13 位毫秒级 / App 前缀嵌入）
- patterns.json 版本号升至 v2.4

### v2.3.1 hotfix

**Bug**：v2.3 引入缩进错误，主循环 `for fp in files:` 缺失，导致主循环体嵌套在副本预扫描循环内。非副本文件在预扫描 `continue` 后被跳过，大量文件无法处理。

**修复**：在 `# ── 主循环 ──` 注释后补回 `for fp in files:`，使预扫描和主循环成为两个独立的循环。

### v2.3

- 副本整理改为**双向邻近搜索**（+1, -1, +2, -2...），找到最近的空闲时间槽

### v2.2

- 新增独立副本整理模式（菜单 [6] [7]）
- 格式自适应：自动识别 `YYYY.MM.DD_HHMM` / `YYYY.MM.DD_HHMMSS` / 中划线 / 紧凑型
- 全半角兼容：同时支持 `(1)` 半角和 `（1）` 全角括号后缀

### v2.1

- Windows 副本智能处理：自动识别 `文件名 (2).jpg` 等合并文件夹产生的副本
- 副本空闲 slot 查找：在原始时间附近查找未被占用的分钟位
- BAT 交互式 force：检测到已重命名目录时自动询问"是否强制继续？"

### v2.0

- JSON 模式配置：18 种日期模式移至 `patterns.json`，支持手动编辑
- 智能模式发现（`--discover`）：启发式扫描，自动生成建议 JSON 条目
- 全中划线模式 `YYYY-MM-DD-HH-MM-SS`，泛化分隔符扫描

### v1.5

- 新增空格分隔 `YYYY-MM-DD HH-MM-SS`、下划线分隔 `YYYY_MM_DD_HHMMSS`
- faceu 格式兼容
- 进度条编码修复（`█░` → `#-` ASCII，CMD 字体不再错位）

### v1.4

- 新增手动修正格式：`YYYYMMDD`（8 位纯日期）、`YYYY.MM.DD`（点分隔日期+描述）

### v1.3

- 重复处理保护：扫描阶段检测已重命名文件，执行模式需 `--force` 确认
- 输出格式完整匹配：新增 3 种自有输出格式的精确匹配

### v1.2

- 渐进式 EXIF：256 KB → 1 MB，无 EXIF 文件不浪费下载流量
- 网络超时保护：所有 I/O 操作带超时（默认 15 s）
- 进度条、环境变量配置 `PHOTO_RENAMER_TIMEOUT`

### v1.1

- 冲突处理从序号后缀改为分钟自动递增，含级联保护
