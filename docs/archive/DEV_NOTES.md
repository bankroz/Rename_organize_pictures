# PyInstaller 项目打包 & Python 脚本改造踩坑总结

> 历史归档：下文的 win/、旧脚本与版本路径不再代表当前结构。当前构建见 ../RELEASING.md。

> 基于 photo_renamer.py v2.6 开发过程中的真实教训，2026-06-02

---

## 1. Bash 内嵌 Python 脚本的引号冲突（最耗时）

### 问题

在 Bash 工具中用 `python -c "..."` 执行多行 Python 代码时，三层引号互相打架：

```bash
# ❌ 致命错误：Bash 的双引号 + Python 的字符串引号 + 中文 % 格式化
python -c "
    print(f'  可用预设: {\", \".join(FORMAT_PRESETS.keys())}')
    print(f'  python photo_renamer.py -s \"{args.source}\"')
"
```

**三层嵌套**：Bash `"` → Python `f'...'` → 转义 `\"` → 中文 `\"` 转义……

结果：Bash 解析时直接截断字符串，Python 报 SyntaxError 或逻辑被篡改。

### 正确做法

**方案 A：写 .py 文件再执行（推荐）**

```bash
# 先写文件
cat > _helper.py << 'PYEOF'
# Python 代码原样写入，Bash 不做任何展开
content = """
def main():
    print(f'可用预设: {", ".join(FORMAT_PRESETS.keys())}')
"""
PYEOF

# 再执行
python _helper.py
```

**方案 B：用 Write 工具写 .py，Bash 只负责执行**

```python
# 用 Write 工具创建 _helper.py（完全避开 Bash 引号问题）
Write(file_path="_helper.py", content="正常的 Python 代码")

# Bash 只做一件事：执行
Bash(command="python _helper.py")
```

**方案 C：单引号包裹 Bash 参数（中文安全）**

```bash
# 单引号内 Bash 不做任何展开，$var 和 `cmd` 都不会执行
python -c '
    import json
    print("hello")  # 安全
'
# 缺点：无法传入 Bash 变量，无法用管道
```

### 经验法则

| 场景 | 推荐方案 |
|------|----------|
| 简单单行（无引号嵌套） | `python -c '简单代码'` |
| 多行代码含中文 | Write 工具写 .py → Bash 执行 |
| 需要修改现有文件 | 用 Edit 工具，不要用 Python 脚本 |
| 大段代码替换 | Write 工具写临时 .py 脚本 → 执行 |

---

## 2. Python 文件读写编码问题（Windows + 中文）

### 问题

在沙箱环境中，Bash 工具的 stdout 使用 GBK 编码。Python 脚本的 print 输出包含中文时：

```python
# ❌ 终端显示乱码（GBK 无法显示某些 Unicode 字符）
print('智能发现：检测到 3 个文件可能使用新的命名规则')
```

### 解决

在 `main()` 入口强制 reconfigure：

```python
import sys

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, LookupError):
        pass  # Python < 3.7 无 reconfigure 方法
```

### .py 文件本身的编码

- `open(path, 'w', encoding='utf-8')` — 写文件时**必须指定 encoding='utf-8'**
- `open(path, 'r', encoding='utf-8')` — 读文件时同理
- 不指定时 Python 使用系统默认编码（Windows GBK），中文字符会被乱码
- `.bat` 文件要用 `encoding='gbk'` 写入（Windows CMD 原生编码）

---

## 3. sys.exit() 与交互模式的逻辑冲突

### 问题

重构 `main()` 为 `while True` 循环后，多处代码写了：

```python
# ❌ 错误顺序：sys.exit() 直接抛异常，后面的代码永远不会执行
sys.exit(1)
if is_interactive:
    args.source = ''
    continue  # ← 永远不会到达
sys.exit(1)  # ← 这行也永远不会到达
```

### 正确顺序

```python
# ✅ 正确：先检查交互模式，再决定退出
if is_interactive:
    args.source = ''
    continue        # 回到菜单
sys.exit(1)         # CLI 模式才真正退出
```

### 排查方法

用 Grep 找所有 sys.exit 调用，逐一检查前面是否有 is_interactive 拦截：

```bash
# 在项目中搜索所有 sys.exit 调用
grep -n 'sys.exit' photo_renamer.py
# 对每一处，检查前面几行是否有 if is_interactive:
```

---

## 4. PyInstaller 构建注意事项

### exe 文件被占用

```
# ❌ 报错：文件被占用，Permission denied
cp dist/photo_renamer.exe win/photo_renamer.exe
```

**原因**：旧的 exe 正在运行（用户双击后没关闭）

**解决**：先让用户关闭 exe，再复制。或者构建时直接指定输出到 `win/` 目录。

### 构建后文件位置

```
project/
├── photo_renamer.py
├── photo_renamer.spec      # PyInstaller 配置
├── build/                   # 中间文件（构建后可删除）
├── dist/
│   └── photo_renamer.exe   # 构建产物
└── win/                     # 发布目录（手动维护）
    ├── photo_renamer.exe
    └── patterns.json
```

### spec 文件排除不必要的依赖

```python
# photo_renamer.spec 关键配置
excludes=['tkinter', 'unittest', 'xmlrpc', 'pydoc',
          'numpy', 'matplotlib', 'pandas']
# 注意：不要排除 Pillow——但本项目 exe 故意不含 Pillow（EXIF 不可用）
```

### 清理构建中间文件

```bash
# 每次构建后清理，避免 git 污染
rm -rf build dist __pycache__
```

---

## 5. 测试策略教训

### 测试文件被已有模式匹配

创建 `Photo_20230615_143022.jpg` 来测试 PatternDiscoverer，结果被 patterns.json 中的模式 4（无锚定的 `\d{14}` 匹配器）匹配了。

**教训**：测试"未匹配"场景前，先确认现有模式不会覆盖你的测试文件名。

### exe 测试要先于交付

> "你能不能先测一下再让我用，这个错误一闪而过，我很难截屏的。"

**教训**：
1. 构建后先用 CLI 测试一遍（`python photo_renamer.py -s 测试集 -m preview`）
2. 再用模拟输入测试交互模式（`echo "1\n路径" | python photo_renamer.py`）
3. 最后才交付给用户

---

## 6. Edit 工具替换大段代码的技巧

### 单次替换的字符上限

Edit 工具用字符串匹配做替换。当 `old_string` 很长时：
- 容易因空白字符（空格 vs 制表符）不匹配而失败
- 中文内容需要和文件中完全一致

### 最佳实践

```python
# ✅ 用包含足够上下文的 old_string 确保唯一性
Edit(
    old_string="if not source_dir.is_dir():\n                print(f'[ERROR] 源文件夹不存在: {args.source}')\n                sys.exit(1)",
    new_string="if not source_dir.is_dir():\n                print(f'[ERROR] 源文件夹不存在: {args.source}')\n                if is_interactive:\n                    args.source = ''\n                    continue\n                sys.exit(1)"
)

# ❌ old_string 太短可能匹配到多处
old_string="sys.exit(1)"  # 文件中可能有多处
```

### 替换函数定义

替换整个函数时，old_string 要包含函数签名 + 函数体首行，new_string 要包含完整新函数。如果函数太长，考虑：
1. 用 Write 工具写临时 Python 脚本来做程序化替换
2. 或分多次 Edit，每次替换一小段

---

## 7. 效率优化清单

每次会话应避免的浪费：

| 浪费行为 | 节约方法 |
|----------|----------|
| 反复 `Read` 同一个大文件的不同位置 | 先用 `Grep` 定位行号，再 `Read` 精确范围 |
| Bash 中嵌套复杂 Python 代码 | 用 Write 写 .py 文件再 Bash 执行 |
| 一次 Edit 失败后盲目重试 | 先 Read 确认当前内容再 Edit |
| 不检查 gitignore 就 add | 先 `git status` 确认状态 |
| 构建 exe 后不清理中间文件 | 每次 build 后 `rm -rf build dist` |
| 测试"未匹配"时不检查现有模式 | 先跑一遍 `DateExtractor.extract()` 确认 |
