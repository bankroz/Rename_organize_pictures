#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Photo & Video Renamer v2.8
按日期重命名照片和视频，优先级：内部日期 → Unix时间戳文件名 → 手动日期文件名 → 文件属性时间

用法:
  python photo_renamer.py --source "D:\图片" --mode preview
  python photo_renamer.py --source "D:\图片" --mode execute
  python photo_renamer.py -s "D:\图片" -m preview -r --csv preview.csv
"""

import argparse
import concurrent.futures
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ─── 超时保护（网络盘/损坏文件抗卡顿） ─────────────────

# 全局超时配置（秒），可根据网络环境调整
NETWORK_TIMEOUT = float(os.environ.get('PHOTO_RENAMER_TIMEOUT', '15'))
DEFAULT_VIDEO_METADATA_TIMEOUT = min(3.0, NETWORK_TIMEOUT)

INVALID_FILENAME_CHARS = set('<>:"/\\|?*')
WINDOWS_RESERVED_NAMES = {
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{i}' for i in range(1, 10)),
    *(f'LPT{i}' for i in range(1, 10)),
}


def _safe_stdout_write(text: str):
    """Write text without crashing on legacy Windows console encodings."""
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        sys.stdout.write(text.encode('ascii', errors='replace').decode('ascii'))


def run_with_timeout(func: Callable, *args, timeout: float = None,
                     default: Any = None, **kwargs) -> Any:
    """
    在独立线程中执行 func(*args, **kwargs)，超时返回 default。
    超时后不等待卡住的 I/O 线程结束，避免网络盘/损坏文件拖死主流程。
    """
    t = timeout if timeout is not None else NETWORK_TIMEOUT
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func, *args, **kwargs)
    try:
        return future.result(timeout=t)
    except (concurrent.futures.TimeoutError, Exception):
        future.cancel()
        return default
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def is_safe_filename_component(name: str) -> bool:
    """Return True when name is safe to use as a single filename stem."""
    if not name or name in ('.', '..'):
        return False
    if any(ch in INVALID_FILENAME_CHARS or ord(ch) < 32 for ch in name):
        return False
    if name.rstrip(' .') != name:
        return False
    if name.upper() in WINDOWS_RESERVED_NAMES:
        return False
    if '..' in name:
        return False
    return True


def _escape_csv_cell(value):
    """Prevent spreadsheet formula execution when opening CSV reports."""
    if isinstance(value, str) and value[:1] in ('=', '+', '-', '@'):
        return "'" + value
    return value


_MONTH_NAME_LOOKUP = {
    'jan': 1, 'january': 1,
    'feb': 2, 'february': 2,
    'mar': 3, 'march': 3,
    'apr': 4, 'april': 4,
    'may': 5,
    'jun': 6, 'june': 6,
    'jul': 7, 'july': 7,
    'aug': 8, 'august': 8,
    'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10,
    'nov': 11, 'november': 11,
    'dec': 12, 'december': 12,
}

MONTH_NAME_RE = (
    r'Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
    r'Jul(?:y)?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?'
)
AMPM_RE = r'AM|PM|am|pm|A\.M\.|P\.M\.|a\.m\.|p\.m\.|上午|下午'


def _coerce_year(value: Any) -> int:
    text = str(value).strip()
    year = int(text)
    if len(text) == 2:
        return 2000 + year if year <= 69 else 1900 + year
    return year


def _coerce_month(value: Any) -> int:
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    key = re.sub(r'[^A-Za-z]', '', text).lower()
    if key in _MONTH_NAME_LOOKUP:
        return _MONTH_NAME_LOOKUP[key]
    raise ValueError(f'unsupported month value: {value}')


def _coerce_datetime_part(field: str, value: Any) -> int:
    if field == 'Y':
        return _coerce_year(value)
    if field == 'M':
        return _coerce_month(value)
    return int(str(value).strip())


def _apply_ampm(hour: int, ampm: Any) -> int:
    marker = str(ampm or '').strip().lower().replace('.', '')
    if not marker:
        return hour
    if not (1 <= hour <= 12):
        raise ValueError('12-hour time must use hour 1-12')
    if marker in ('am', 'a', '上午'):
        return 0 if hour == 12 else hour
    if marker in ('pm', 'p', '下午'):
        return hour if hour == 12 else hour + 12
    raise ValueError(f'unsupported AM/PM marker: {ampm}')


def _escape_csv_row(row: dict) -> dict:
    return {key: _escape_csv_cell(value) for key, value in row.items()}


# ─── 进度条（零依赖，自适应终端宽度） ──────────────────────

class ProgressBar:
    """终端内联进度条，支持 \r 原地刷新"""

    def __init__(self, total: int, desc: str = '处理中', width: int = 30,
                 disable: bool = False, callback=None, stage: str = ''):
        self.total = max(total, 1)
        self.desc = desc
        self.width = width
        self.disable = disable or not sys.stdout.isatty()
        self.callback = callback
        self.stage = stage or desc
        self.current = 0
        self._last_len = 0
        self._start_time = time.time()
        self.timeouts = 0  # 超时跳过计数

    def _emit(self, done: bool = False, info: str = ''):
        if not self.callback:
            return
        elapsed = time.time() - self._start_time
        self.callback({
            'stage': self.stage,
            'desc': self.desc,
            'current': self.current,
            'total': self.total,
            'percent': min(100, int(self.current * 100 / self.total)),
            'elapsed': elapsed,
            'timeouts': self.timeouts,
            'info': info,
            'done': done,
        })

    def update(self, n: int = 1, info: str = ''):
        """增加进度并刷新显示"""
        self.current += n
        self._emit(done=False, info=info)
        elapsed = time.time() - self._start_time
        elapsed_str = f'{int(elapsed // 60)}m{int(elapsed % 60)}s'
        if self.disable:
            if self.callback:
                return
            # 非交互终端：每 20% 打印一行
            if self.current == 1 or self.current >= self.total or self.current % max(1, self.total // 5) == 0:
                pct = self.current * 100 // self.total
                _safe_stdout_write(f'  {self.desc}: {self.current}/{self.total} ({pct}%) [{elapsed_str}]')
                if self.timeouts > 0:
                    _safe_stdout_write(f' 超时跳过: {self.timeouts}')
                _safe_stdout_write('\n')
                sys.stdout.flush()
            return

        pct = self.current * 100 // self.total
        filled = int(self.width * self.current / self.total)
        bar = '#' * filled + '-' * (self.width - filled)
        info_str = f' - {info}' if info else ''
        timeout_str = f' ⏱超时:{self.timeouts}' if self.timeouts > 0 else ''
        line = f'\r  {self.desc}: [{bar}] {pct}% ({self.current}/{self.total}) [{elapsed_str}]{timeout_str}{info_str}'

        # 用空格填充覆盖上一行的多余字符
        pad = self._last_len - len(line)
        if pad > 0:
            line += ' ' * pad
        self._last_len = len(line)

        _safe_stdout_write(line)
        sys.stdout.flush()

    def close(self):
        """完成时换行"""
        self._emit(done=True)
        elapsed = time.time() - self._start_time
        elapsed_str = f'{int(elapsed // 60)}m{int(elapsed % 60)}s'
        if self.disable and self.callback:
            return
        if not self.disable:
            _safe_stdout_write('\n')
            sys.stdout.flush()
        _safe_stdout_write(f'  ✓ {self.desc}完成: {self.current}/{self.total} [{elapsed_str}]')
        if self.timeouts > 0:
            _safe_stdout_write(f'  超时跳过: {self.timeouts}')
        _safe_stdout_write('\n')
        sys.stdout.flush()

# ─── 依赖检测 ───────────────────────────────────────────
try:
    from PIL import Image
    from PIL.ExifTags import TAGS as EXIF_TAGS
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ─── 支持的格式 ──────────────────────────────────────────
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif',
              '.webp', '.heic', '.heif', '.raw', '.cr2', '.nef', '.arw',
              '.dng', '.orf', '.rw2', '.raf', '.sr2', '.pef'}
VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.mkv', '.3gp', '.wmv', '.flv',
              '.webm', '.m4v', '.mts', '.m2ts', '.ts', '.mxf'}

# 默认要处理的扩展名
DEFAULT_EXTS = IMAGE_EXTS | VIDEO_EXTS


# ─── 模式配置管理 ──────────────────────────────────────

# 默认模式（JSON 文件缺失时的 fallback）
_DEFAULT_PATTERNS_CONFIG = [
    {"id": 1, "regex": r'^(\d{4})\.(\d{2})\.(\d{2})_(\d{2})(\d{2})(\d{2})$',
     "group_count": 6, "description": "YYYY.MM.DD_HHMMSS（自有输出格式-精确到秒）", "is_own_output": True},
    {"id": 2, "regex": r'^(\d{4})\.(\d{2})\.(\d{2})_(\d{2})(\d{2})$',
     "group_count": 5, "description": "YYYY.MM.DD_HHMM（自有输出格式-默认）", "is_own_output": True},
    {"id": 3, "regex": r'^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})$',
     "group_count": 6, "description": "YYYY-MM-DD_HH-MM-SS（自有输出格式-中划线）", "is_own_output": True},
    {"id": 4, "regex": r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})',
     "group_count": 6, "description": "YYYYMMDD_HHMMSS（自有输出格式-紧凑型，如 petal_20230928_143520）", "is_own_output": True},
    {"id": 5, "regex": r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(?!\d)',
     "group_count": 5, "description": "YYYYMMDD_HHMM（下划线分隔-精确到分）", "is_own_output": False},
    {"id": 6, "regex": r'(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(\d{2})',
     "group_count": 6, "description": "YYYY-MM-DD_HHMMSS（中划线日期+下划线时分秒，如 微信图片_2025-06-30_140243）", "is_own_output": False},
    {"id": 7, "regex": r'(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(?!\d)',
     "group_count": 5, "description": "YYYY-MM-DD_HHMM（中划线日期+下划线时分）", "is_own_output": False},
    {"id": 8, "regex": r'(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})',
     "group_count": 6, "description": "YYYYMMDD-HHMMSS（日期紧凑+中划线时分秒）", "is_own_output": False},
    {"id": 9, "regex": r'(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})',
     "group_count": 6, "description": "YYYY-MM-DD-HH-MM-SS（全中划线分隔，如 Screenshot_2024-06-15-14-30-22）", "is_own_output": False},
    {"id": 10, "regex": r'(\d{4})-(\d{2})-(\d{2}) (\d{2})-(\d{2})-(\d{2})',
     "group_count": 6, "description": "YYYY-MM-DD HH-MM-SS（空格分隔，如 2015-12-04 15-39-11-desc）", "is_own_output": False},
    {"id": 11, "regex": r'(\d{4})_(\d{2})_(\d{2})_(\d{2})(\d{2})(\d{2})',
     "group_count": 6, "description": "YYYY_MM_DD_HHMMSS（全下划线分隔，如 HwVideoEditor_2020_08_22_175855）", "is_own_output": False},
    {"id": 17, "regex": r'(\d{4})-(\d{2})-(\d{2})-(\d{2})h(\d{2})m(\d{2})',
     "group_count": 6, "description": "YYYY-MM-DD-HHhMMmSS（中划线日期+h/m时分秒，如 2016-04-03-20h52m50）", "is_own_output": False},
    {"id": 18, "regex": r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})\d{3}',
     "group_count": 6, "description": "YYYY-MM-DD-HHMMSSmmm（中划线日期+紧凑时分秒毫秒，如 2019-12-16-181315963）", "is_own_output": False},
    {"id": 12, "regex": r'(\d{4})年(\d{2})月(\d{2})日 (\d{2})点(\d{2})分',
     "group_count": 5, "description": "YYYY年MM月DD日 HH点MM分", "is_own_output": False},
    {"id": 13, "regex": r'(\d{4})\.(\d{2})\.(\d{2})',
     "group_count": 3, "description": "YYYY.MM.DD（点分隔，如 2025.01.15-三人跳舞）", "is_own_output": False},
    {"id": 14, "regex": r'(\d{4})-(\d{2})-(\d{2})',
     "group_count": 3, "description": "YYYY-MM-DD（纯中划线日期）", "is_own_output": False},
    {"id": 15, "regex": r'(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})',
     "group_count": 6, "description": "YYYYMMDDHHMMSS（紧凑型全部连接，如 IMG20220625102520, faceu_..._20201024204541523）", "is_own_output": False},
    {"id": 16, "regex": r'(\d{4})(\d{2})(\d{2})',
     "group_count": 3, "description": "YYYYMMDD（纯8位日期，如 20220502（1））", "is_own_output": False},
    {"id": 19, "regex": r'(\d{2})-(\d{2})-(\d{4}) (\d{2})(\d{2})(\d{2})',
     "group_count": 6, "description": "DD-MM-YYYY HHMMSS（iPhone录屏，如 ScreenRecording_02-06-2026 154022）",
     "is_own_output": False, "group_order": "DMYhms"},
]

# 全局模式配置路径
_PATTERN_CONFIG_PATH: Optional[str] = None
# 从 patterns.json 加载的默认输出格式（可选，用户可自定义）
_DEFAULT_OUTPUT_FORMAT: Optional[str] = None


def _coerce_timeout_seconds(value: Any, default: float) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return default
    if timeout <= 0:
        return default
    return min(timeout, NETWORK_TIMEOUT)


def set_pattern_config_path(path: str):
    """设置自定义 patterns.json 路径"""
    global _PATTERN_CONFIG_PATH
    _PATTERN_CONFIG_PATH = path


def _get_exe_dir() -> Path:
    """获取可执行文件所在目录（兼容 PyInstaller --onefile 打包）"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _find_config_path() -> Path:
    """查找 patterns.json：优先自定义路径；绿色 EXE 优先使用同目录配置。"""
    if _PATTERN_CONFIG_PATH:
        return Path(_PATTERN_CONFIG_PATH)
    exe_dir = _get_exe_dir()
    exe_config = exe_dir / 'patterns.json'
    if getattr(sys, 'frozen', False) and exe_config.exists():
        return exe_config
    cwd = Path.cwd() / 'patterns.json'
    if cwd.exists():
        return cwd
    return exe_config


def get_video_metadata_timeout() -> float:
    """视频元数据短读超时：patterns.json 最高优先级，运行中修改后下次读取生效。"""
    config_path = _find_config_path()
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            configured = config.get('video_metadata_timeout_seconds')
            if configured is not None:
                return _coerce_timeout_seconds(configured, DEFAULT_VIDEO_METADATA_TIMEOUT)
        except Exception:
            pass
    env_value = os.environ.get('PHOTO_RENAMER_VIDEO_TIMEOUT')
    if env_value:
        return _coerce_timeout_seconds(env_value, DEFAULT_VIDEO_METADATA_TIMEOUT)
    return DEFAULT_VIDEO_METADATA_TIMEOUT


def _from_default_patterns() -> list:
    """从默认配置编译模式列表"""
    return [(re.compile(e['regex']), dict(e)) for e in _DEFAULT_PATTERNS_CONFIG]


def _has_nested_quantifier(regex: str) -> bool:
    """Detect obvious nested quantifiers that can cause catastrophic backtracking."""
    return re.search(r'\((?:\\.|[^()])*[+*](?:\\.|[^()])*\)\s*(?:[+*]|\{\d)', regex) is not None


def _validate_pattern_entry(entry: dict, index: int):
    """Validate one user-editable pattern before compiling it."""
    regex = entry.get('regex')
    if not isinstance(regex, str) or not regex:
        raise ValueError(f'第 {index} 个模式缺少 regex')
    if len(regex) > 300:
        raise ValueError(f'第 {index} 个模式 regex 过长')
    if _has_nested_quantifier(regex):
        raise ValueError(f'第 {index} 个模式包含危险嵌套量词')

    group_count = entry.get('group_count')
    if group_count not in (3, 5, 6):
        raise ValueError(f'第 {index} 个模式 group_count 必须为 3/5/6')

    group_order = entry.get('group_order', 'YMDhms')
    if not isinstance(group_order, str) or len(group_order) < group_count:
        raise ValueError(f'第 {index} 个模式 group_order 长度不足')
    if any(ch not in 'YMDhms' for ch in group_order[:group_count]):
        raise ValueError(f'第 {index} 个模式 group_order 含无效字段')
    ampm_group = entry.get('ampm_group')
    if ampm_group is not None and (not isinstance(ampm_group, int) or ampm_group < 1):
        raise ValueError(f'第 {index} 个模式 ampm_group 必须为正整数')


def _load_patterns_from_json(json_path: Path) -> list:
    """从 JSON 文件加载并编译模式，返回 [(compiled_regex, entry_dict), ...]"""
    global _DEFAULT_OUTPUT_FORMAT
    with open(json_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    # 读取用户自定义的默认输出格式
    _DEFAULT_OUTPUT_FORMAT = config.get('default_output_format', None)

    patterns = []
    for idx, entry in enumerate(config.get('patterns', []), start=1):
        _validate_pattern_entry(entry, idx)
        compiled = re.compile(entry['regex'])
        if compiled.groups < entry['group_count']:
            raise ValueError(f'第 {idx} 个模式捕获组数量不足')
        ampm_group = entry.get('ampm_group')
        if ampm_group is not None and ampm_group > compiled.groups:
            raise ValueError(f'第 {idx} 个模式 ampm_group 超出捕获组数量')
        patterns.append((compiled, dict(entry)))
    return patterns


def generate_default_config(json_path: Path = None):
    """生成默认 patterns.json（首次使用或 --generate-config 时调用）"""
    if json_path is None:
        json_path = _find_config_path()
    config = {
        "version": "2.8",
        "default_output_format": "%Y.%m.%d_%H%M",
        "video_metadata_timeout_seconds": DEFAULT_VIDEO_METADATA_TIMEOUT,
        "_instructions_video_metadata_timeout": (
            "视频元数据读取短超时，单位秒。用于 MOV/MP4/3GP 等容器日期探测；"
            "数值越大越可能读到网盘慢文件，但预览/重命名等待更久。"
            "程序运行中修改此值，下一次预览或执行会直接生效。"
        ),
        "_instructions": (
            "每个 pattern 包含: regex(正则表达式), group_count(捕获组数:3/5/6), "
            "description(描述), is_own_output(是否自有输出格式)。"
            "捕获组默认按 年、月、日、时、分、秒 顺序（YMDhms）。"
            "若文件名日期顺序不同（如 iPhone 录屏 DD-MM-YYYY），可添加 "
            "group_order 字段指定顺序（如 'DMYhms'，字符含义: Y年 M月 D日 h时 m分 s秒）。"
            "添加新模式时按优先级排列（精确的在前），保存后重新运行即可生效。"
        ),
        "patterns": _DEFAULT_PATTERNS_CONFIG,
    }
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f'[INFO] 默认模式配置已生成: {json_path}')


# ╔══════════════════════════════════════════════════════════╗
# ║              日期提取引擎                                 ║
# ╚══════════════════════════════════════════════════════════╝

class DateExtractor:
    """按优先级链提取日期：内部日期 → Unix时间戳文件名 → 手动日期文件名 → 文件属性时间"""

    _patterns: list = []          # [(compiled_regex, entry_dict), ...]
    _renamed_patterns: list = []  # [compiled_regex, ...]
    _initialized: bool = False

    @classmethod
    def _ensure_initialized(cls):
        """延迟加载模式配置（首次调用时自动触发）"""
        if cls._initialized:
            return
        cls.reload_patterns()

    @classmethod
    def reload_patterns(cls, config_path: str = ''):
        """
        重新加载模式配置。
        config_path: 自定义 JSON 路径，留空则自动查找
        """
        path = _find_config_path() if not config_path else Path(config_path)
        if path.exists():
            try:
                cls._patterns = _load_patterns_from_json(path)
                if not cls._patterns:
                    raise ValueError('patterns.json 中没有有效模式')
            except Exception as e:
                print(f'[WARN] 加载模式配置失败 ({path}): {e}')
                print('        将使用内置默认模式。可用 --generate-config 重新生成。')
                cls._patterns = _from_default_patterns()
        else:
            # 首次使用：自动生成默认配置
            generate_default_config(path)
            cls._patterns = _from_default_patterns()

        # 提取自有输出格式（用于已重命名检测，强制锚定到完整 stem）
        cls._renamed_patterns = [
            re.compile('^' + entry['regex'] + '$')
            for _, entry in cls._patterns
            if entry.get('is_own_output')
        ]
        cls._initialized = True

    # ── 保留类变量引用（向后兼容：_from_filename 中通过 cls._patterns 访问） ──

    # ── Unix 时间戳模式 ──
    # 13位毫秒（2000年后）：mmexport1664505286518, wx_camera_1718166753538
    # 匹配约束：两端均为非数字字符/边界，且年份在 2000-2099 之间
    TIMESTAMP_MS = re.compile(r'(?<!\d)(\d{13})(?!\d)')
    # 10位秒级（2000年后）
    TIMESTAMP_S = re.compile(r'(?<!\d)(\d{10})(?!\d)')

    # ── 已知 App 前缀 + 嵌入时间戳（时间戳后紧跟更多数字/哈希串） ──
    # 这些模式下 13 位毫秒时间戳右侧不一定有 \D 边界
    PREFIXED_MS_PATTERNS: list = [
        (re.compile(r'Camera_XHS_(\d{13})'), '小红书相机'),     # Camera_XHS_1779195734185...
        (re.compile(r'^(\d{13})_\d+_edit_'), '编辑导出'),      # 1728267073523_100_edit_...
    ]

    # 哈希文件名检测阈值
    HASH_MIN_LEN = 32  # 32字符以上
    HASH_HEX_RATIO = 0.9  # 90% 以上为十六进制字符

    # Windows 副本后缀检测：匹配半角 (1) (2) / 全角（1）（2）/ 有无空格变体
    DUP_SUFFIX_RE = re.compile(r'^(.+?)\s*[（(]\s*(\d+)\s*[）)]\s*$')

    @classmethod
    def extract(cls, filepath: Path) -> Tuple[Optional[datetime], str]:
        """
        返回 (datetime对象, 来源描述)
        来源描述: 'EXIF', 'VideoMetadata(字段名)', 'UnixTimestamp(ms)', 'Filename(模式名)', 'FileCreateTime'
        若所有方法均失败且有超时发生，来源标注 '(timeout)'
        """
        had_timeout = False
        is_video = filepath.suffix.lower() in VIDEO_EXTS

        # 优先级1: 文件内部日期。图片读 EXIF，视频读容器元数据；命中即停。
        dt, source = cls._from_exif(filepath)
        if 'timeout' in source:
            had_timeout = True
        if dt:
            return dt, source

        if is_video:
            dt, source = cls._from_video_metadata(filepath)
            if 'timeout' in source:
                had_timeout = True
            if dt:
                return dt, source

        # 优先级2: Unix 时间戳文件名。比手动标注文件名更不容易被随手篡改。
        dt, source = cls._from_timestamp(filepath)
        if dt:
            return dt, source

        # 优先级3: 手动/设备明文日期文件名。
        dt, source = cls._from_filename(filepath)
        if dt:
            return dt, source

        # 优先级4: 文件属性时间。Windows/macOS 优先创建时间，Linux 无创建时间时回退修改时间。
        dt, source = cls._from_file_property_time(filepath)
        if 'timeout' in source:
            had_timeout = True
        if dt:
            return dt, source

        if had_timeout:
            return None, 'Unknown(timeout)'
        return None, 'Unknown'

    @classmethod
    def _read_file_head(cls, filepath: Path, max_bytes: int) -> Optional[bytes]:
        """读取文件头部字节（用于 EXIF 解析）"""
        try:
            with open(filepath, 'rb') as f:
                return f.read(max_bytes)
        except Exception:
            return None

    @classmethod
    def _from_exif(cls, filepath: Path) -> Tuple[Optional[datetime], str]:
        """
        渐进式 EXIF 读取策略：
        1. 只读 256KB 头部（EXIF 通常在前几十 KB）
        2. PIL 成功解析但无 EXIF → 立即返回（不浪费更多网络 I/O）
        3. PIL 解析失败（截断）→ 再试 1MB
        4. 仍失败 → 回退到其他日期提取方式
        """
        if not HAS_PIL:
            return None, ''
        ext = filepath.suffix.lower()
        if ext not in IMAGE_EXTS:
            return None, ''

        # 渐进式读取：从小到大，找到 EXIF 立刻停，确认没有也立刻停
        for chunk_size in [256 * 1024, 1024 * 1024]:   # 256KB → 1MB
            data = run_with_timeout(cls._read_file_head, filepath, max_bytes=chunk_size,
                                    timeout=NETWORK_TIMEOUT, default=None)
            if data is None:
                return None, 'EXIF(timeout)'

            try:
                img = Image.open(BytesIO(data))
                exif = img._getexif()
                if exif:
                    # 有 EXIF → 提取日期
                    date_fields = ['DateTimeOriginal', 'DateTimeDigitized', 'DateTime']
                    for field in date_fields:
                        for tag_id, value in exif.items():
                            tag_name = EXIF_TAGS.get(tag_id, '')
                            if tag_name == field and value:
                                value_str = str(value).strip()
                                # 提取标准日期时间部分，去除时区偏移和本地化后缀
                                # 如：'2017:02:15 21:19:44下午' → '2017:02:15 21:19:44'
                                m = re.match(r'(\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2})', value_str)
                                if m:
                                    value_str = m.group(1)
                                else:
                                    # fallback: 老方法去掉时区偏移
                                    value_str = re.sub(r'[+-]\d{2}:\d{2}$', '', value_str)
                                try:
                                    dt = datetime.strptime(value_str, '%Y:%m:%d %H:%M:%S')
                                    return dt, f'EXIF({field})'
                                except ValueError:
                                    continue
                    # 有 EXIF 但无日期字段 → 确认无效，不继续读
                    return None, ''

                # PIL 解析成功但无 EXIF → 这个文件确实没有 EXIF，立即回退
                # 检查是否真的解析成功了（有 width/height 属性）
                if hasattr(img, 'width') and img.width > 0:
                    return None, ''

                # 无 width（可能截断导致）→ 继续下一个 chunk_size

            except Exception:
                # PIL 解析失败（文件截断或损坏）→ 继续下一个 chunk_size
                pass

        return None, ''

    @classmethod
    def _from_filename(cls, filepath: Path) -> Tuple[Optional[datetime], str]:
        cls._ensure_initialized()
        stem = filepath.stem  # 不含扩展名的文件名

        # ── 副本后缀检测（Windows 合并文件夹产生的 (1)/(2)/（1）/（2）等） ──
        # 优先级最高：在模式匹配前检测，确保时分不被 date-only 模式截断
        dup_m = cls.DUP_SUFFIX_RE.match(stem)
        if dup_m:
            clean_stem = dup_m.group(1).strip()
            # 尝试用自有输出格式解析 clean_stem
            for pattern in cls._renamed_patterns:
                m = pattern.match(clean_stem)
                if not m:
                    continue
                try:
                    groups = [int(g) for g in m.groups()]
                    y, mo, d = groups[0], groups[1], groups[2]
                    if not (1970 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31):
                        continue
                    hh = groups[3] if len(groups) >= 5 else 0
                    mm = groups[4] if len(groups) >= 5 else 0
                    ss = groups[5] if len(groups) >= 6 else 0
                    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
                        continue
                    dt = datetime(y, mo, d, hh, mm, ss)
                    return dt, '已重命名(副本)'
                except (ValueError, IndexError):
                    continue

        # ── 标准模式匹配 ──
        info = cls._match_filename_rule(filepath)
        if info:
            return info['datetime'], f"Filename({info['description']})"

        return None, ''

    @classmethod
    def _datetime_from_rule_groups(cls, entry: dict, groups: tuple) -> datetime:
        group_order = entry.get('group_order', 'YMDhms')
        gc = entry.get('group_count', len(groups))

        field_values = {}
        for i, ch in enumerate(group_order[:gc]):
            field_values[ch] = _coerce_datetime_part(ch, groups[i])

        y = field_values['Y'] if 'Y' in field_values else _coerce_year(groups[0])
        mo = field_values['M'] if 'M' in field_values else (_coerce_month(groups[1]) if gc >= 2 else 1)
        d = field_values['D'] if 'D' in field_values else (int(groups[2]) if gc >= 3 else 1)
        hh = field_values.get('h', 0)
        mm = field_values.get('m', 0)
        ss = field_values.get('s', 0)
        if entry.get('ampm_group') is not None:
            hh = _apply_ampm(hh, groups[entry['ampm_group'] - 1])

        return datetime(y, mo, d, hh, mm, ss)

    @classmethod
    def _match_filename_rule(cls, filepath: Path) -> Optional[dict]:
        cls._ensure_initialized()
        stem = filepath.stem
        for idx, (pattern, entry) in enumerate(cls._patterns):
            for match in pattern.finditer(stem):
                try:
                    dt = cls._datetime_from_rule_groups(entry, match.groups())
                    if 1970 <= dt.year <= 2099 and 1 <= dt.month <= 12 and 1 <= dt.day <= 31:
                        return {
                            'index': idx,
                            'id': entry.get('id', idx + 1),
                            'description': entry.get('description', f'模式{idx + 1}'),
                            'regex': entry.get('regex', ''),
                            'group_count': entry.get('group_count', ''),
                            'is_own_output': bool(entry.get('is_own_output')),
                            'match_text': match.group(),
                            'datetime': dt,
                        }
                except (ValueError, TypeError, KeyError, IndexError):
                    continue
        return None

    @classmethod
    def _is_hash_stem(cls, stem: str) -> bool:
        """检测文件名 stem 是否为哈希值（避免时间戳误匹配）"""
        if len(stem) < cls.HASH_MIN_LEN:
            return False
        hex_chars = sum(1 for c in stem if c in '0123456789abcdefABCDEF')
        return hex_chars / len(stem) >= cls.HASH_HEX_RATIO

    @classmethod
    def _is_already_renamed_stem(cls, stem: str) -> bool:
        """检测文件名 stem 是否匹配我们自己的输出格式（含日期合理性验证）"""
        cls._ensure_initialized()
        for pattern in cls._renamed_patterns:
            m = pattern.match(stem)
            if not m:
                continue
            try:
                groups = [int(g) for g in m.groups()]
                y, mo, d = groups[0], groups[1], groups[2]
                if not (1970 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31):
                    continue
                # 验证时间部分
                if len(groups) >= 5:
                    h, mi = groups[3], groups[4]
                    if not (0 <= h <= 23 and 0 <= mi <= 59):
                        continue
                if len(groups) >= 6:
                    s = groups[5]
                    if not (0 <= s <= 59):
                        continue
                return True
            except (ValueError, IndexError):
                continue
        return False

    @classmethod
    def _from_timestamp(cls, filepath: Path) -> Tuple[Optional[datetime], str]:
        stem = filepath.stem

        # 哈希文件名直接跳过（避免数字串误匹配为时间戳）
        if cls._is_hash_stem(stem):
            return None, ''

        # 优先级0: 已知 App 前缀（13位毫秒时间戳嵌入在更长的数字串中）
        for pattern, app_name in cls.PREFIXED_MS_PATTERNS:
            m = pattern.search(stem)
            if m:
                ts = int(m.group(1)) / 1000.0
                try:
                    dt = datetime.fromtimestamp(ts)
                    if 2000 <= dt.year <= 2099:
                        return dt, f'UnixTimestamp(ms,{app_name})'
                except (OSError, ValueError):
                    pass

        # 优先13位毫秒（限定 2000-2099 年，排除异常值）
        match_ms = cls.TIMESTAMP_MS.search(stem)
        if match_ms:
            ts = int(match_ms.group(1)) / 1000.0
            try:
                dt = datetime.fromtimestamp(ts)
                if 2000 <= dt.year <= 2099:
                    return dt, 'UnixTimestamp(ms)'
            except (OSError, ValueError):
                pass

        # 回退10位秒（限定 2000-2099 年）
        match_s = cls.TIMESTAMP_S.search(stem)
        if match_s:
            ts = int(match_s.group(1))
            try:
                dt = datetime.fromtimestamp(ts)
                if 2000 <= dt.year <= 2099:
                    return dt, 'UnixTimestamp(s)'
            except (OSError, ValueError):
                pass

        return None, ''

    @classmethod
    def _find_ffprobe(cls) -> Optional[str]:
        exe_name = 'ffprobe.exe' if sys.platform == 'win32' else 'ffprobe'
        bundle_dir = getattr(sys, '_MEIPASS', '')
        if bundle_dir:
            bundled = Path(bundle_dir) / exe_name
            if bundled.exists():
                return str(bundled)
        return shutil.which('ffprobe')

    @classmethod
    def _run_ffprobe_json(cls, filepath: Path, entries: str, sections: List[str]) -> Tuple[Optional[dict], str]:
        ffprobe = cls._find_ffprobe()
        if not ffprobe:
            return None, ''

        cmd = [
            ffprobe,
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_entries', entries,
        ]
        cmd.extend(sections)
        cmd.append(str(filepath))
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=get_video_metadata_timeout(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None, 'VideoMetadata(timeout)'
        except Exception:
            return None, ''

        if completed.returncode != 0 or not completed.stdout.strip():
            return None, ''
        try:
            return json.loads(completed.stdout), ''
        except json.JSONDecodeError:
            return None, ''

    @staticmethod
    def _parse_video_datetime(value: str, keep_offset_wall_time: bool = False) -> Optional[datetime]:
        if not value:
            return None
        text = str(value).strip()
        text = re.sub(r'Z$', '+00:00', text)
        text = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', text)

        parsed = None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            for fmt in (
                '%Y:%m:%d %H:%M:%S',
                '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%d',
                '%Y:%m:%d',
                '%Y.%m.%d',
            ):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if parsed is None:
            return None

        if parsed.tzinfo is None:
            return parsed
        if keep_offset_wall_time:
            return parsed.replace(tzinfo=None)
        return parsed.astimezone().replace(tzinfo=None)

    @classmethod
    def _from_video_metadata_probe(cls, probe: dict) -> Tuple[Optional[datetime], str]:
        format_tags = (probe.get('format') or {}).get('tags') or {}
        quicktime_date = format_tags.get('com.apple.quicktime.creationdate')
        dt = cls._parse_video_datetime(quicktime_date, keep_offset_wall_time=True)
        if dt:
            return dt, 'VideoMetadata(com.apple.quicktime.creationdate)'

        format_creation = format_tags.get('creation_time')
        dt = cls._parse_video_datetime(format_creation)
        if dt:
            return dt, 'VideoMetadata(creation_time)'

        for field in ('creationdate', 'date'):
            dt = cls._parse_video_datetime(format_tags.get(field), keep_offset_wall_time=True)
            if dt:
                return dt, f'VideoMetadata({field})'

        for stream in probe.get('streams') or []:
            stream_tags = (stream or {}).get('tags') or {}
            dt = cls._parse_video_datetime(stream_tags.get('creation_time'))
            if dt:
                return dt, 'VideoMetadata(stream.creation_time)'

        return None, ''

    @classmethod
    def _from_video_metadata(cls, filepath: Path) -> Tuple[Optional[datetime], str]:
        ext = filepath.suffix.lower()
        if ext not in VIDEO_EXTS:
            return None, ''

        # 先只读容器级标签。很多 MOV/MP4/iPhone 视频的日期在这里，
        # 命中后立刻返回，避免网盘文件被不必要地深入分析。
        probe, source = cls._run_ffprobe_json(
            filepath,
            'format_tags=creation_time,com.apple.quicktime.creationdate,creationdate,date',
            ['-show_format'],
        )
        if source:
            return None, source
        if probe:
            dt, meta_source = cls._from_video_metadata_probe(probe)
            if dt:
                return dt, meta_source

        # 容器级没有日期时，再读流级 creation_time 作为补充。
        probe, source = cls._run_ffprobe_json(
            filepath,
            'stream_tags=creation_time',
            ['-show_streams'],
        )
        if source:
            return None, source
        if not probe:
            return None, ''
        return cls._from_video_metadata_probe(probe)

    @classmethod
    def _from_file_property_time(cls, filepath: Path) -> Tuple[Optional[datetime], str]:
        try:
            stat_result = run_with_timeout(lambda p: p.stat(), filepath,
                                           timeout=NETWORK_TIMEOUT, default=None)
            if stat_result is None:
                return None, 'FileCreateTime(timeout)'
            if hasattr(stat_result, 'st_birthtime'):
                return datetime.fromtimestamp(stat_result.st_birthtime), 'FileCreateTime'
            if sys.platform == 'win32':
                return datetime.fromtimestamp(stat_result.st_ctime), 'FileCreateTime'
            return datetime.fromtimestamp(stat_result.st_mtime), 'FileModifyTime'
        except Exception:
            return None, ''

    @classmethod
    def _from_modify_time(cls, filepath: Path) -> Tuple[Optional[datetime], str]:
        return cls._from_file_property_time(filepath)


# ╔══════════════════════════════════════════════════════════╗
# ║         智能模式发现（PatternDiscoverer）                ║
# ╚══════════════════════════════════════════════════════════╝

class PatternDiscoverer:
    """

    _MONTH_RE = MONTH_NAME_RE
    _AMPM_RE = AMPM_RE
    对未匹配到日期的文件名，用启发式算法检测潜在日期格式。
    按模式签名分组后供用户审核，可导出为 CSV 或生成建议的 JSON 模式条目。
    """

    # 日期候选扫描的正则模板
    _DATE_CANDIDATES = [
        # 8 位紧凑 YYYYMMDD
        (re.compile(r'(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)'), 3, 'YYYYMMDD'),
        # 14 位紧凑 YYYYMMDDHHMMSS
        (re.compile(r'(?<!\d)(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?!\d)'), 6, 'YYYYMMDDHHMMSS'),
    ]

    # 分隔符集合
    _SEPARATORS = r'[.\-/_]'

    # 带时间的不同分隔符日期组合
    _EXTENDED_PATTERNS = [
        # YYYY-MM-DD HH:MM:SS AM/PM
        (re.compile(rf'(\d{{4}})[.\-/_](\d{{1,2}})[.\-/_](\d{{1,2}})[ T_-]+(\d{{1,2}})[:.\-_](\d{{2}})[:.\-_](\d{{2}})\s*({AMPM_RE})'), 6, 'YYYY-MM-DD HH:MM:SS AMPM', 'YMDhms', 7),
        # YYYY-MM-DD HH:MM AM/PM
        (re.compile(rf'(\d{{4}})[.\-/_](\d{{1,2}})[.\-/_](\d{{1,2}})[ T_-]+(\d{{1,2}})[:.\-_](\d{{2}})\s*({AMPM_RE})'), 5, 'YYYY-MM-DD HH:MM AMPM', 'YMDhm', 6),
        # YYYY-MM-DD HHMMSS AM/PM
        (re.compile(rf'(\d{{4}})[.\-/_](\d{{1,2}})[.\-/_](\d{{1,2}})[ T_-]+(\d{{1,2}})(\d{{2}})(\d{{2}})\s*({AMPM_RE})'), 6, 'YYYY-MM-DD HHMMSS AMPM', 'YMDhms', 7),
        # YYYY-MM-DD HHMM AM/PM
        (re.compile(rf'(\d{{4}})[.\-/_](\d{{1,2}})[.\-/_](\d{{1,2}})[ T_-]+(\d{{1,2}})(\d{{2}})\s*({AMPM_RE})'), 5, 'YYYY-MM-DD HHMM AMPM', 'YMDhm', 6),
        # YY-MM-DD HH:MM:SS
        (re.compile(r'(?<!\d)(\d{2})[.\-/_](\d{1,2})[.\-/_](\d{1,2})[ T_-]+(\d{1,2})[:.\-_](\d{2})[:.\-_](\d{2})(?!\d)'), 6, 'YY-MM-DD HH:MM:SS'),
        # YY-MM-DD HH:MM
        (re.compile(r'(?<!\d)(\d{2})[.\-/_](\d{1,2})[.\-/_](\d{1,2})[ T_-]+(\d{1,2})[:.\-_](\d{2})(?!\d)'), 5, 'YY-MM-DD HH:MM'),
        # YY-MM-DD-HHMM
        (re.compile(r'(?<!\d)(\d{2})[.\-/_](\d{1,2})[.\-/_](\d{1,2})[ T_-]+(\d{2})(\d{2})(?!\d)'), 5, 'YY-MM-DD-HHMM'),
        # YYYY-MON-DD / MON-DD-YYYY / DD-MON-YYYY
        (re.compile(rf'(?<![A-Za-z0-9])(\d{{4}})[.\-_\s]+({MONTH_NAME_RE})[.\-_\s]+(\d{{1,2}})(?:st|nd|rd|th)?(?![A-Za-z0-9])', re.IGNORECASE), 3, 'YYYY-MON-DD'),
        (re.compile(rf'(?<![A-Za-z0-9])({MONTH_NAME_RE})[.\-_\s]+(\d{{1,2}})(?:st|nd|rd|th)?[,\-_\s]+(\d{{4}})(?![A-Za-z0-9])', re.IGNORECASE), 3, 'MON-DD-YYYY', 'MDY'),
        (re.compile(rf'(?<![A-Za-z0-9])(\d{{1,2}})(?:st|nd|rd|th)?[.\-_\s]+({MONTH_NAME_RE})[,\-_\s]+(\d{{4}})(?![A-Za-z0-9])', re.IGNORECASE), 3, 'DD-MON-YYYY', 'DMY'),
        # YYYY-MM-DD HH:MM:SS
        (re.compile(r'(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})'), 6, 'YYYY-MM-DD HH:MM:SS'),
        # YYYY-MM-DD HH-MM-SS
        (re.compile(r'(\d{4})-(\d{2})-(\d{2}) (\d{2})-(\d{2})-(\d{2})'), 6, 'YYYY-MM-DD HH-MM-SS'),
        # YYYY-MM-DD-HH-MM-SS
        (re.compile(r'(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})'), 6, 'YYYY-MM-DD-HH-MM-SS'),
        # YYYY-MM-DD-HHMMSS
        (re.compile(r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})(?!\d)'), 6, 'YYYY-MM-DD-HHMMSS'),
        # YYYY-MM-DD-HHMM
        (re.compile(r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(?!\d)'), 5, 'YYYY-MM-DD-HHMM'),
        # YYYY/MM/DD HH:MM:SS
        (re.compile(r'(\d{4})/(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2})'), 6, 'YYYY/MM/DD HH:MM:SS'),
        # YYYY/MM/DD
        (re.compile(r'(\d{4})/(\d{2})/(\d{2})'), 3, 'YYYY/MM/DD'),
        # YYYY.MM.DD HH.MM.SS
        (re.compile(r'(\d{4})\.(\d{2})\.(\d{2}) (\d{2})\.(\d{2})\.(\d{2})'), 6, 'YYYY.MM.DD HH.MM.SS'),
        # YYYY_MM_DD_HH_MM_SS
        (re.compile(r'(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})_(\d{2})'), 6, 'YYYY_MM_DD_HH_MM_SS'),
        # YYYY年MM月DD日 HH:MM:SS
        (re.compile(r'(\d{4})年(\d{2})月(\d{2})日 (\d{2}):(\d{2}):(\d{2})'), 6, 'YYYY年MM月DD日 HH:MM:SS'),
    ]

    _SIGNATURE_SUGGESTIONS = {
        'YYYY-MM-DD-HHMM': {
            "regex": r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(?!\d)',
            "group_count": 5,
            "description": "YYYY-MM-DD-HHMM（自动发现）",
            "is_own_output": False,
        },
        'YYYY-MM-DD HH:MM:SS AMPM': {
            "regex": rf'(\d{{4}})[.\-/_](\d{{1,2}})[.\-/_](\d{{1,2}})[ T_-]+(\d{{1,2}})[:.\-_](\d{{2}})[:.\-_](\d{{2}})\s*({AMPM_RE})',
            "group_count": 6,
            "description": "YYYY-MM-DD HH:MM:SS AMPM（自动发现）",
            "is_own_output": False,
            "ampm_group": 7,
        },
        'YYYY-MM-DD HH:MM AMPM': {
            "regex": rf'(\d{{4}})[.\-/_](\d{{1,2}})[.\-/_](\d{{1,2}})[ T_-]+(\d{{1,2}})[:.\-_](\d{{2}})\s*({AMPM_RE})',
            "group_count": 5,
            "description": "YYYY-MM-DD HH:MM AMPM（自动发现）",
            "is_own_output": False,
            "ampm_group": 6,
        },
        'YYYY-MM-DD HHMMSS AMPM': {
            "regex": rf'(\d{{4}})[.\-/_](\d{{1,2}})[.\-/_](\d{{1,2}})[ T_-]+(\d{{1,2}})(\d{{2}})(\d{{2}})\s*({AMPM_RE})',
            "group_count": 6,
            "description": "YYYY-MM-DD HHMMSS AMPM（自动发现）",
            "is_own_output": False,
            "ampm_group": 7,
        },
        'YYYY-MM-DD HHMM AMPM': {
            "regex": rf'(\d{{4}})[.\-/_](\d{{1,2}})[.\-/_](\d{{1,2}})[ T_-]+(\d{{1,2}})(\d{{2}})\s*({AMPM_RE})',
            "group_count": 5,
            "description": "YYYY-MM-DD HHMM AMPM（自动发现）",
            "is_own_output": False,
            "ampm_group": 6,
        },
        'YYYY-MM-DD-HHMMSS': {
            "regex": r'(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})(?!\d)',
            "group_count": 6,
            "description": "YYYY-MM-DD-HHMMSS（自动发现）",
            "is_own_output": False,
        },
        'YY-MM-DD HH:MM:SS': {
            "regex": r'(?<!\d)(\d{2})[.\-/_](\d{1,2})[.\-/_](\d{1,2})[ T_-]+(\d{1,2})[:.\-_](\d{2})[:.\-_](\d{2})(?!\d)',
            "group_count": 6,
            "description": "YY-MM-DD HH:MM:SS（自动发现）",
            "is_own_output": False,
        },
        'YY-MM-DD HH:MM': {
            "regex": r'(?<!\d)(\d{2})[.\-/_](\d{1,2})[.\-/_](\d{1,2})[ T_-]+(\d{1,2})[:.\-_](\d{2})(?!\d)',
            "group_count": 5,
            "description": "YY-MM-DD HH:MM（自动发现）",
            "is_own_output": False,
        },
        'YY-MM-DD-HHMM': {
            "regex": r'(?<!\d)(\d{2})[.\-/_](\d{1,2})[.\-/_](\d{1,2})[ T_-]+(\d{2})(\d{2})(?!\d)',
            "group_count": 5,
            "description": "YY-MM-DD-HHMM（自动发现）",
            "is_own_output": False,
        },
        'YYYY-MON-DD': {
            "regex": rf'(?<![A-Za-z0-9])(\d{{4}})[.\-_\s]+({MONTH_NAME_RE})[.\-_\s]+(\d{{1,2}})(?:st|nd|rd|th)?(?![A-Za-z0-9])',
            "group_count": 3,
            "description": "YYYY-MON-DD（自动发现）",
            "is_own_output": False,
        },
        'MON-DD-YYYY': {
            "regex": rf'(?<![A-Za-z0-9])({MONTH_NAME_RE})[.\-_\s]+(\d{{1,2}})(?:st|nd|rd|th)?[,\-_\s]+(\d{{4}})(?![A-Za-z0-9])',
            "group_count": 3,
            "description": "MON-DD-YYYY（自动发现）",
            "is_own_output": False,
            "group_order": "MDY",
        },
        'DD-MON-YYYY': {
            "regex": rf'(?<![A-Za-z0-9])(\d{{1,2}})(?:st|nd|rd|th)?[.\-_\s]+({MONTH_NAME_RE})[,\-_\s]+(\d{{4}})(?![A-Za-z0-9])',
            "group_count": 3,
            "description": "DD-MON-YYYY（自动发现）",
            "is_own_output": False,
            "group_order": "DMY",
        },
    }

    # 非 Y-M-D 顺序（需要特别标注，D-M-Y 或 M-D-Y 可能歧义）
    _ALT_ORDER_PATTERNS = [
        # DD-MM-YYYY 或 MM-DD-YYYY（歧义，用 (*) 标注）
        (re.compile(r'(?<!\d)(\d{2})-(\d{2})-(\d{4})(?!\d)'), 3, 'DD/MM-YYYY(*)'),
        # DD/MM/YYYY 或 MM/DD/YYYY（歧义）
        (re.compile(r'(?<!\d)(\d{2})/(\d{2})/(\d{4})(?!\d)'), 3, 'DD/MM/YYYY(*)'),
    ]

    # Unix 时间戳检测（10位秒级 + 13位毫秒级，排除哈希文件名）
    _TIMESTAMP_PATTERNS = [
        (re.compile(r'(?<!\d)(\d{13})(?!\d)'), 'UnixTimestamp(ms)'),
        (re.compile(r'(?<!\d)(\d{10})(?!\d)'), 'UnixTimestamp(s)'),
    ]

    # App 前缀嵌入时间戳（时间戳后紧跟更多数字/哈希）
    _PREFIXED_TS_PATTERNS = [
        (re.compile(r'Camera_XHS_(\d{13})'), 'UnixTimestamp(ms,小红书)'),
        (re.compile(r'^(\d{13})_\d+_edit_'), 'UnixTimestamp(ms,编辑导出)'),
    ]

    @classmethod
    def discover(cls, filepaths: List[Path],
                 existing_extractor=None) -> Dict[str, list]:
        """
        在未匹配文件中发现潜在的日期格式。

        参数:
            filepaths: 文件路径列表
            existing_extractor: DateExtractor 类（用于排除已匹配的文件）

        返回:
            {signature: [{'file': path, 'match_text': str, 'datetime': datetime}, ...], ...}
            signature 如 'YYYY-MM-DD-HH-MM-SS', 'YYYY/MM/DD' 等
        """
        discoveries: Dict[str, list] = {}

        for fp in filepaths:
            # 跳过已有的高置信文件名规则。只有日期的宽松规则不能屏蔽更高精度的候选时间。
            stem = fp.stem
            has_ampm_marker = re.search(rf'(?<![A-Za-z])(?:{AMPM_RE})(?![A-Za-z])', stem, re.IGNORECASE) is not None
            if existing_extractor:
                dt, _ = existing_extractor._from_timestamp(fp)
                if dt:
                    continue
                dt, source = existing_extractor._from_filename(fp)
                if dt and (dt.hour != 0 or dt.minute != 0 or dt.second != 0):
                    if not has_ampm_marker or 'AMPM' in source:
                        continue
            found = None

            # 阶段1: 精确的扩展格式（含时间，优先级高）
            for candidate in cls._EXTENDED_PATTERNS:
                pattern, group_count, signature = candidate[:3]
                group_order = candidate[3] if len(candidate) >= 4 else 'YMDhms'
                ampm_group = candidate[4] if len(candidate) >= 5 else None
                for m in pattern.finditer(stem):
                    groups = m.groups()
                    try:
                        if cls._validate_date_groups(groups, group_count, group_order, ampm_group):
                            dt = cls._groups_to_datetime(groups, group_count, group_order, ampm_group)
                            found = (m.group(), dt, signature, 'standard')
                            break
                    except (ValueError, TypeError):
                        continue
                if found:
                    break

            # 阶段2: 基本紧凑格式（8位/14位）
            if not found:
                for pattern, group_count, signature in cls._DATE_CANDIDATES:
                    for m in pattern.finditer(stem):
                        groups = m.groups()
                        try:
                            if cls._validate_date_groups(groups, group_count):
                                dt = cls._groups_to_datetime(groups, group_count)
                                found = (m.group(), dt, signature, 'standard')
                                break
                        except (ValueError, TypeError):
                            continue
                    if found:
                        break

            # 阶段3: 泛化分隔符扫描（尝试所有常见分隔符的 Y-M-D 组合）
            if not found:
                found = cls._scan_generic_separators(stem)

            # 阶段4: 非标准顺序（D-M-Y / M-D-Y，标注歧义）
            if not found:
                for pattern, group_count, signature in cls._ALT_ORDER_PATTERNS:
                    for m in pattern.finditer(stem):
                        groups = m.groups()
                        try:
                            if cls._validate_alt_order(groups):
                                # 统一按 D-M-Y 解释
                                d, mo, y = int(groups[0]), int(groups[1]), int(groups[2])
                                dt = datetime(y, mo, d, 0, 0, 0)
                                found = (m.group(), dt, f'{signature}(*)', 'alt_order')
                                break
                        except (ValueError, TypeError):
                            continue
                    if found:
                        break

            # 阶段5: App 前缀嵌入时间戳（13位毫秒嵌入在更长的数字串中）
            if not found:
                for pattern, sig in cls._PREFIXED_TS_PATTERNS:
                    m = pattern.search(stem)
                    if m:
                        try:
                            ts = int(m.group(1)) / 1000.0
                            dt = datetime.fromtimestamp(ts)
                            if 2000 <= dt.year <= 2099:
                                found = (m.group(), dt, sig, 'timestamp')
                                break
                        except (OSError, ValueError):
                            pass

            # 阶段6: 标准 Unix 时间戳（10位/13位，两端非数字边界）
            if not found:
                for pattern, sig in cls._TIMESTAMP_PATTERNS:
                    m = pattern.search(stem)
                    if m:
                        try:
                            ts_str = m.group(1)
                            ts = int(ts_str) / 1000.0 if len(ts_str) == 13 else int(ts_str)
                            dt = datetime.fromtimestamp(ts)
                            if 2000 <= dt.year <= 2099:
                                found = (m.group(), dt, sig, 'timestamp')
                                break
                        except (OSError, ValueError):
                            pass

            if found:
                match_text, dt, sig, category = found
                if sig not in discoveries:
                    discoveries[sig] = []
                discoveries[sig].append({
                    'file': fp,
                    'match_text': match_text,
                    'datetime': dt,
                    'category': category,
                })

        return discoveries

    @classmethod
    def _validate_date_groups(cls, groups: tuple, group_count: int,
                              group_order: str = 'YMDhms',
                              ampm_group: Optional[int] = None) -> bool:
        """验证 Y-M-D 顺序的捕获组是否构成合法日期"""
        y, mo, d, h, mi, s = cls._datetime_parts_from_groups(groups, group_count, group_order, ampm_group)
        if not (1970 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31):
            return False
        if not (0 <= h <= 23 and 0 <= mi <= 59 and 0 <= s <= 59):
            return False
        return True

    @classmethod
    def _validate_alt_order(cls, groups: tuple) -> bool:
        """验证非标准顺序（D-M-Y 或 M-D-Y）"""
        a, b, y = int(groups[0]), int(groups[1]), int(groups[2])
        if not (1970 <= y <= 2099):
            return False
        # D-M-Y: a=day(1-31), b=month(1-12)
        # M-D-Y: a=month(1-12), b=day(1-31)
        valid_dmy = (1 <= a <= 31 and 1 <= b <= 12)
        valid_mdy = (1 <= a <= 12 and 1 <= b <= 31)
        return valid_dmy or valid_mdy

    @classmethod
    def _datetime_parts_from_groups(cls, groups: tuple, group_count: int,
                                    group_order: str = 'YMDhms',
                                    ampm_group: Optional[int] = None) -> tuple:
        field_values = {}
        for i, ch in enumerate(group_order[:group_count]):
            field_values[ch] = _coerce_datetime_part(ch, groups[i])
        y = field_values['Y'] if 'Y' in field_values else _coerce_year(groups[0])
        mo = field_values['M'] if 'M' in field_values else (_coerce_month(groups[1]) if group_count >= 2 else 1)
        d = field_values['D'] if 'D' in field_values else (int(groups[2]) if group_count >= 3 else 1)
        h = field_values.get('h', 0)
        mi = field_values.get('m', 0)
        s = field_values.get('s', 0)
        if ampm_group is not None:
            h = _apply_ampm(h, groups[ampm_group - 1])
        return y, mo, d, h, mi, s

    @classmethod
    def _groups_to_datetime(cls, groups: tuple, group_count: int,
                            group_order: str = 'YMDhms',
                            ampm_group: Optional[int] = None) -> datetime:
        """将捕获组转为 datetime，支持两位年份、英文月份和 12 小时制。"""
        y, mo, d, h, mi, s = cls._datetime_parts_from_groups(groups, group_count, group_order, ampm_group)
        return datetime(y, mo, d, h, mi, s)

    @classmethod
    def _scan_generic_separators(cls, stem: str):
        """泛化分隔符扫描：尝试常见分隔符的 digits-sep-digits-sep-digits 模式"""
        # YYYY-sep-MM-sep-DD 或 DD-sep-MM-sep-YYYY
        for m in re.finditer(r'(\d{2,4})([\.\-/_])(\d{1,2})\2(\d{1,4})', stem):
            a, sep, b, c = m.group(1), m.group(2), m.group(3), m.group(4)

            # 尝试 YYYY-sep-MM-sep-DD（a=4位）
            if len(a) == 4:
                y, mo, d = int(a), int(b), int(c)
                if 1970 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31:
                    try:
                        dt = datetime(y, mo, d, 0, 0, 0)
                        sep_d = sep
                        return (m.group(), dt, f'YYYY{sep_d}MM{sep_d}DD', 'generic')
                    except ValueError:
                        pass

            # 尝试 DD-sep-MM-sep-YYYY（c=4位）
            if len(c) == 4:
                d, mo, y = int(a), int(b), int(c)
                if 1970 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31:
                    try:
                        dt = datetime(y, mo, d, 0, 0, 0)
                        sep_d = sep
                        return (m.group(), dt, f'DD{sep_d}MM{sep_d}YYYY(*)', 'generic')
                    except ValueError:
                        pass

        # 日期+时间组合（不同分隔符）: YYYY-MM-DD sep HH-MM-SS
        for m in re.finditer(r'(\d{4})[\.\-/_](\d{2})[\.\-/_](\d{2})[\.\-/_\s]+(\d{2})[\.\-/_:](\d{2})[\.\-/_:](\d{2})', stem):
            y, mo, d, h, mi, s = map(int, m.groups())
            if 1970 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31 \
                    and 0 <= h <= 23 and 0 <= mi <= 59 and 0 <= s <= 59:
                try:
                    dt = datetime(y, mo, d, h, mi, s)
                    return (m.group(), dt, 'YYYY-MM-DD+HH-MM-SS(复合)', 'generic')
                except ValueError:
                    pass

        return None

    @classmethod
    def generate_json_suggestion(cls, signature: str) -> Optional[Dict]:
        """
        从模式签名生成建议的 JSON 配置条目。

        支持以下签名类型：
        - YYYYMMDD / YYYYMMDDHHMMSS
        - YYYY{sep}MM{sep}DD
        - YYYY{sep}MM{sep}DD{sep}HH{sep}MM{sep}SS
        - YYYY-MM-DD HH:MM:SS（混合分隔符）
        - 带 (*) 后缀的歧义标注会被自动剥离
        """
        # 剥离歧义标注后缀
        clean_sig = signature.replace('(*)', '').strip()
        if clean_sig in cls._SIGNATURE_SUGGESTIONS:
            return dict(cls._SIGNATURE_SUGGESTIONS[clean_sig])

        # 紧凑格式
        if clean_sig == 'YYYYMMDD':
            return {
                "regex": r'(\d{4})(\d{2})(\d{2})',
                "group_count": 3,
                "description": "YYYYMMDD（手动添加）",
                "is_own_output": False,
            }
        if clean_sig == 'YYYYMMDDHHMMSS':
            return {
                "regex": r'(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})',
                "group_count": 6,
                "description": "YYYYMMDDHHMMSS（手动添加）",
                "is_own_output": False,
            }

        # 含分隔符的格式
        sep_map = {
            '-': '-', '.': '\\.', '/': '/', '_': '_',
            ' ': ' ', ':': ':',
        }

        # 尝试解析签名中的分隔符
        # 例如: YYYY-MM-DD HH:MM:SS → sep1='-', sep2='-', sep3=' ', sep4=':', sep5=':'
        # 例如: YYYY/MM/DD → sep1='/', sep2='/'
        parts = []
        i = 0
        while i < len(clean_sig):
            if clean_sig[i:i + 4] == 'YYYY':
                parts.append(('YYYY', 4))
                i += 4
            elif clean_sig[i:i + 2] in ('MM', 'DD', 'HH', 'SS'):
                parts.append((clean_sig[i:i + 2], 2))
                i += 2
            else:
                # 可能是分隔符
                j = i
                while j < len(clean_sig) and clean_sig[j:j + 2] not in ('YY', 'MM', 'DD', 'HH', 'SS'):
                    if clean_sig[j:j + 4] == 'YYYY':
                        break
                    j += 1
                sep = clean_sig[i:j]
                if sep:
                    parts.append(('SEP', sep))
                i = j

        # 构建正则
        regex_parts = []
        group_count = 0
        description_parts = []
        for kind, val in parts:
            if kind == 'YYYY':
                regex_parts.append(r'(\d{4})')
                description_parts.append('YYYY')
                group_count += 1
            elif kind in ('MM', 'DD', 'HH', 'SS'):
                regex_parts.append(r'(\d{2})')
                description_parts.append(kind)
                group_count += 1
            elif kind == 'SEP':
                escaped = sep_map.get(val, re.escape(val))
                regex_parts.append(escaped)
                description_parts.append(val)

        if group_count not in (3, 5, 6):
            return None

        result = {
            "regex": ''.join(regex_parts),
            "group_count": group_count,
            "description": ''.join(description_parts) + '（自动发现）',
            "is_own_output": False,
        }

        # 检测非标准 group_order（如 DD-MM-YYYY 需要设为 DMYhms）
        field_order = []
        seen_time = False
        for kind, _ in parts:
            if kind == 'YYYY':
                field_order.append('Y')
            elif kind == 'MM':
                field_order.append('m' if seen_time else 'M')
            elif kind == 'DD':
                field_order.append('D')
            elif kind == 'HH':
                field_order.append('h')
                seen_time = True
            elif kind == 'SS':
                field_order.append('s')
        if field_order:
            default_order = 'YMDhms'[:len(field_order)]
            actual_order = ''.join(field_order)
            if actual_order != default_order:
                result['group_order'] = actual_order

        return result


# ╔══════════════════════════════════════════════════════════╗
# ║              重命名引擎                                   ║
# ╚══════════════════════════════════════════════════════════╝

# 预设输出格式
FORMAT_PRESETS = {
    '默认': '%Y.%m.%d_%H%M',
    '精确到秒': '%Y.%m.%d_%H%M%S',
    '中划线分隔': '%Y-%m-%d_%H-%M-%S',
    '紧凑型': '%Y%m%d_%H%M%S',
}

# 自有输出格式 ID → strftime 格式映射（供 dedup 模式自动检测格式使用）
_DEDUP_FORMAT_BY_ID = {
    1: '%Y.%m.%d_%H%M%S',          # YYYY.MM.DD_HHMMSS
    2: '%Y.%m.%d_%H%M',            # YYYY.MM.DD_HHMM
    3: '%Y-%m-%d_%H-%M-%S',        # YYYY-MM-DD_HH-MM-SS
    4: '%Y%m%d_%H%M%S',            # YYYYMMDD_HHMMSS
}


class PhotoRenamer:
    def __init__(self, source_dir: str, recursive: bool = False,
                 fmt: str = '%Y.%m.%d_%H%M', output_dir: str = '',
                 exts: set = None):
        self.source_dir = Path(source_dir)
        self.recursive = recursive
        self.fmt = fmt
        self.output_dir = Path(output_dir) if output_dir else None
        self.exts = exts or DEFAULT_EXTS
        self.results: list = []  # 用于 CSV 导出

    def scan_files(self) -> list:
        """扫描所有待处理文件（带进度指示，适配网络盘）"""
        files = []
        last_print = 0
        scan_start = time.time()
        if self.recursive:
            for root, _, filenames in os.walk(self.source_dir):
                for f in filenames:
                    fp = Path(os.path.join(root, f))
                    if fp.suffix.lower() in self.exts:
                        files.append(fp)
                        # 每 50 个文件或每 5 秒汇报一次进度
                        now = time.time()
                        if len(files) - last_print >= 50 or (now - scan_start > 5 and len(files) > last_print):
                            if sys.stdout.isatty():
                                elapsed = int(now - scan_start)
                                sys.stdout.write(f'\r  扫描中... 已发现 {len(files)} 个文件 [{elapsed}s]')
                                sys.stdout.flush()
                            last_print = len(files)
                            scan_start = now
        else:
            for f in self.source_dir.iterdir():
                if f.is_file() and f.suffix.lower() in self.exts:
                    files.append(f)
        if sys.stdout.isatty() and last_print > 0:
            sys.stdout.write('\n')
            sys.stdout.flush()
        return sorted(files)

    def process(self, mode: str = 'preview', progress: ProgressBar = None) -> list:
        """
        执行重命名处理
        mode: 'preview' 只模拟 | 'execute' 真正重命名/复制
        progress: 可选的进度条对象
        返回 results 列表
        """
        files = self.scan_files()
        self.results = []

        # 初始化进度条
        if progress is None:
            progress = ProgressBar(len(files), desc='处理文件', disable=False)

        # 处理冲突：同目录同 stem 的，分钟自动 +1（而非加序号）
        name_counter = {}       # (dir, stem, ext) → 出现次数
        assigned_stems = set()  # 已分配的所有 (dir, stem, ext)，用于防级联冲突
        dup_targets = {}        # str(fp) → (datetime, new_stem)，副本预分配结果

        # ── 副本文件预扫描 ──
        # 对 Windows 合并文件夹产生的副本 (2)/（2）文件，在原始时间附近
        # 查找第一个未被占用的空闲 slot（检查磁盘文件 + 已分配 stems）
        for fp in files:
            dup_m = DateExtractor.DUP_SUFFIX_RE.match(fp.stem)
            if not dup_m:
                continue
            clean_stem = dup_m.group(1).strip()
            for pattern in DateExtractor._renamed_patterns:
                m = pattern.match(clean_stem)
                if not m:
                    continue
                try:
                    groups = [int(g) for g in m.groups()]
                    y, mo, d = groups[0], groups[1], groups[2]
                    if not (1970 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31):
                        continue
                    hh = groups[3] if len(groups) >= 5 else 0
                    mm = groups[4] if len(groups) >= 5 else 0
                    ss = groups[5] if len(groups) >= 6 else 0
                    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
                        continue
                    dt = datetime(y, mo, d, hh, mm, ss)
                    original_stem = dt.strftime(self.fmt)
                    ext = fp.suffix.lower()
                    dir_key = self.output_dir if self.output_dir else fp.parent
                    conflict_key = (dir_key, original_stem, ext)
                    # bump name_counter：保护原始文件的原位
                    name_counter[conflict_key] = name_counter.get(conflict_key, 0) + 1
                    # 在原始时间附近查找空闲 slot（跳过磁盘已有 + 已分配）
                    found_slot = False
                    for distance in range(1, 1440):
                        # 先向后(+1, +2, ...)再向前(-1, -2, ...)
                        for direction, offset in [(1, distance), (-1, distance)]:
                            candidate_dt = dt + timedelta(minutes=direction * distance)
                            candidate_stem = candidate_dt.strftime(self.fmt)
                            candidate_key = (dir_key, candidate_stem, ext)
                            # 检查磁盘文件是否存在
                            candidate_path = Path(str(dir_key)) / f'{candidate_stem}{ext}'
                            if candidate_path.exists():
                                continue
                            # 检查是否已被其他副本预分配
                            if candidate_key in assigned_stems:
                                continue
                            # 空闲 slot 找到，预分配
                            assigned_stems.add(candidate_key)
                            dup_targets[str(fp)] = (candidate_dt, candidate_stem)
                            found_slot = True
                            break
                        if found_slot:
                            break
                    if not found_slot:
                        # 1440 分钟内没找到空闲 slot，仍靠级联逻辑兜底
                        pass
                    break
                except (ValueError, IndexError):
                    continue

        # ── 主循环 ──
        for fp in files:
            dt, source = DateExtractor.extract(fp)
            if dt is None:
                tag = '[超时]' if 'timeout' in source else '[无法提取日期]'
                if 'timeout' in source:
                    progress.timeouts += 1
                progress.update(info=f'{fp.name} {tag}')
                continue

            original_stem = dt.strftime(self.fmt)
            ext = fp.suffix.lower()
            dir_key = self.output_dir if self.output_dir else fp.parent

            # ── 副本文件：使用预分配的空闲 slot ──
            fp_key = str(fp)
            if fp_key in dup_targets:
                pre_dt, pre_stem = dup_targets[fp_key]
                new_stem = pre_stem
                new_name = f'{new_stem}{ext}'
                src_path = str(fp)
                dst_dir = str(self.output_dir) if self.output_dir else str(fp.parent)
                dst_path = str(Path(dst_dir) / new_name)
                date_str = pre_dt.strftime('%Y-%m-%d %H:%M:%S')
                self.results.append({
                    'original': src_path,
                    'new_name': new_name,
                    'date': date_str,
                    'source': source,
                    'status': 'ok' if dt else 'error',
                    'dst': dst_path,
                })
                progress.update(info=fp.name)
                continue

            # 统计同一原始 stem 的出现次数
            conflict_key = (dir_key, original_stem, ext)
            name_counter[conflict_key] = name_counter.get(conflict_key, 0) + 1
            count = name_counter[conflict_key]

            # 计算候选 stem（第 N 个 → 分钟 + (N-1)）
            adjust_offset = count - 1
            new_stem = (dt + timedelta(minutes=adjust_offset)).strftime(self.fmt)

            # 统一防级联冲突：如果候选 stem 已被任何文件占用，持续 +1 分钟直到唯一
            extra = 0
            while (dir_key, new_stem, ext) in assigned_stems and extra < 1440:
                extra += 1
                new_stem = (dt + timedelta(minutes=adjust_offset + extra)).strftime(self.fmt)

            assigned_stems.add((dir_key, new_stem, ext))

            new_name = f'{new_stem}{ext}'
            src_path = str(fp)
            dst_dir = str(self.output_dir) if self.output_dir else str(fp.parent)
            dst_path = str(Path(dst_dir) / new_name)

            # 确定最终日期时间（用于报告）
            if new_stem != original_stem:
                try:
                    final_dt = datetime.strptime(new_stem, self.fmt)
                except ValueError:
                    final_dt = dt
                date_str = final_dt.strftime('%Y-%m-%d %H:%M:%S')
            else:
                date_str = dt.strftime('%Y-%m-%d %H:%M:%S')

            self.results.append({
                'original': src_path,
                'new_name': new_name,
                'date': date_str,
                'source': source,
                'status': 'ok' if dt else 'error',
                'dst': dst_path,
            })

            progress.update(info=fp.name)

        progress.close()
        return self.results

    def execute(self, progress_callback: Optional[Callable[[dict], None]] = None) -> int:
        """真正执行重命名/复制，返回成功数"""
        # 第一阶段：计算所有目标路径
        progress = ProgressBar(
            len(self.scan_files()),
            desc='分析文件',
            callback=progress_callback,
            stage='analyze',
        )
        results = self.process(mode='execute', progress=progress)
        # process() 已经 close 了 progress，但我们还需要第二阶段进度

        # 第二阶段：执行重命名/复制
        exec_pb = ProgressBar(
            len(results),
            desc='执行重命名',
            disable=progress.disable,
            callback=progress_callback,
            stage='execute',
        )
        success = 0

        for r in results:
            if r['status'] != 'ok':
                exec_pb.update(info='跳过')
                continue

            src = Path(r['original'])
            dst = Path(r['dst'])

            try:
                if self.output_dir:
                    # 复制模式（带超时保护的网络复制）
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if dst.exists():
                        r['status'] = 'conflict'
                        r['error'] = f'目标文件已存在: {dst}'
                        exec_pb.update(info='冲突')
                        continue
                    result = run_with_timeout(shutil.copy2, str(src), str(dst),
                                              timeout=NETWORK_TIMEOUT * 2, default=None)
                    if result is None:
                        r['status'] = 'error'
                        r['error'] = '复制超时（网络延迟）'
                        exec_pb.timeouts += 1
                        exec_pb.update(info='超时')
                        continue
                else:
                    # 重命名模式（同名目录内 rename 通常是原子的，但也加保护）
                    if dst.exists():
                        r['status'] = 'conflict'
                        r['error'] = f'目标文件已存在: {dst}'
                        exec_pb.update(info='冲突')
                        continue

                    def do_rename(s, d):
                        s.rename(d)
                        return True

                    result = run_with_timeout(do_rename, src, dst,
                                              timeout=NETWORK_TIMEOUT, default=None)
                    if result is None:
                        r['status'] = 'error'
                        r['error'] = '重命名超时（网络延迟）'
                        exec_pb.timeouts += 1
                        exec_pb.update(info='超时')
                        continue

                success += 1
                # 执行后更新 original 为实际目标（仅复制模式）
                if self.output_dir:
                    r['new_path'] = str(dst)

                exec_pb.update(info=dst.name)

            except Exception as e:
                r['status'] = 'error'
                r['error'] = str(e)
                exec_pb.update(info='失败')

        exec_pb.close()
        self.results = results
        return success

    def write_csv(self, csv_path: str):
        """将结果写入 CSV 文件"""
        fieldnames = ['original', 'new_name', 'date', 'source', 'status', 'dst', 'error']
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(_escape_csv_row(row) for row in self.results)


def _resolve_undo_destination(row: dict) -> Path:
    """Resolve the renamed path from new logs, with fallback for older CSV files."""
    dst = row.get('dst') or row.get('new_path')
    if dst:
        return Path(dst)
    original = Path(row.get('original', ''))
    new_name = row.get('new_name', '')
    return original.parent / new_name


def undo_from_csv(csv_path: str, force: bool = False) -> dict:
    """
    Undo in-place renames from a rename CSV log.

    Rows are processed in reverse order to avoid chain-conflict issues. By
    default, undo is conservative: if the original path already exists, the row
    is skipped instead of overwriting it.
    """
    path = Path(csv_path)
    summary = {'restored': 0, 'skipped': 0, 'errors': 0, 'rows': 0, 'details': []}
    with open(path, newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))

    summary['rows'] = len(rows)
    for row in reversed(rows):
        if row.get('status') != 'ok':
            summary['skipped'] += 1
            summary['details'].append({**row, 'undo_status': 'skipped', 'undo_error': '非成功记录'})
            continue

        original = Path(row.get('original', ''))
        renamed = _resolve_undo_destination(row)
        if not renamed.exists():
            summary['skipped'] += 1
            summary['details'].append({**row, 'undo_status': 'skipped', 'undo_error': f'目标不存在: {renamed}'})
            continue
        if original.exists() and not force:
            summary['skipped'] += 1
            summary['details'].append({**row, 'undo_status': 'skipped', 'undo_error': f'原路径已存在: {original}'})
            continue

        try:
            if force and original.exists():
                original.unlink()
            original.parent.mkdir(parents=True, exist_ok=True)
            renamed.rename(original)
            summary['restored'] += 1
            summary['details'].append({**row, 'undo_status': 'restored', 'undo_error': ''})
        except Exception as e:
            summary['errors'] += 1
            summary['details'].append({**row, 'undo_status': 'error', 'undo_error': str(e)})

    return summary


def write_undo_report(csv_path: str, details: list):
    """Write an undo report next to the source CSV."""
    path = Path(csv_path)
    report_path = path.with_name(path.stem + '_undo.csv')
    fieldnames = ['original', 'new_name', 'date', 'source', 'status', 'dst', 'undo_status', 'undo_error']
    with open(report_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(_escape_csv_row(row) for row in details)
    return report_path


def _load_config_document(config_path: str = '') -> dict:
    path = Path(config_path) if config_path else _find_config_path()
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'patterns': _DEFAULT_PATTERNS_CONFIG}


def _write_config_document(config: dict, config_path: str = '') -> Path:
    path = Path(config_path) if config_path else _find_config_path()
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    return path


def get_pattern_config_path(config_path: str = '') -> str:
    """Return the patterns.json path currently used by config-backed features."""
    return str(Path(config_path) if config_path else _find_config_path())


def _format_rule_suggestions(discoveries: Dict[str, list]) -> list:
    suggestions = []
    for sig in sorted(discoveries.keys(), key=lambda s: (-len(discoveries[s]), s)):
        items = discoveries[sig]
        suggestions.append({
            'signature': sig,
            'count': len(items),
            'examples': [
                {
                    'file': str(item['file']),
                    'match_text': item['match_text'],
                    'datetime': item['datetime'].strftime('%Y-%m-%d %H:%M:%S'),
                    'category': item.get('category', ''),
                }
                for item in items[:5]
            ],
            'suggestion': PatternDiscoverer.generate_json_suggestion(sig),
        })
    return suggestions


def _collect_existing_rule_coverage(files: list) -> list:
    covered: Dict[tuple, dict] = {}
    for fp in files:
        info = DateExtractor._match_filename_rule(fp)
        if not info:
            continue
        key = (info.get('id'), info.get('description'))
        if key not in covered:
            covered[key] = {
                'id': info.get('id'),
                'index': info.get('index'),
                'name': info.get('description', ''),
                'regex': info.get('regex', ''),
                'group_count': info.get('group_count', ''),
                'is_own_output': info.get('is_own_output', False),
                'count': 0,
                'examples': [],
            }
        item = covered[key]
        item['count'] += 1
        if len(item['examples']) < 5:
            item['examples'].append({
                'file': str(fp),
                'match_text': info.get('match_text', ''),
                'datetime': info['datetime'].strftime('%Y-%m-%d %H:%M:%S'),
            })
    return sorted(covered.values(), key=lambda item: (-item['count'], item.get('index', 0)))


def discover_rule_report(source_dir: str, recursive: bool = False,
                         ext_arg: str = '') -> dict:
    """Return unknown rule suggestions plus existing rules that already cover files."""
    DateExtractor._ensure_initialized()
    renamer = PhotoRenamer(source_dir, recursive=recursive, exts=_parse_exts(ext_arg))
    files = renamer.scan_files()
    discoveries = PatternDiscoverer.discover(files, existing_extractor=DateExtractor)
    return {
        'files_count': len(files),
        'suggestions': _format_rule_suggestions(discoveries),
        'covered': _collect_existing_rule_coverage(files),
    }


def discover_rule_suggestions(source_dir: str, recursive: bool = False,
                              ext_arg: str = '') -> list:
    """Return PatternDiscoverer suggestions in a TUI-friendly structure."""
    return discover_rule_report(source_dir, recursive, ext_arg)['suggestions']


def _pattern_identity(entry: dict) -> tuple:
    return (
        _normalize_pattern_regex(entry.get('regex', '')),
        entry.get('group_count'),
        entry.get('group_order', 'YMDhms'),
        entry.get('ampm_group'),
    )


def _normalize_pattern_regex(regex: str) -> str:
    return str(regex).replace(r'(?<!\d)', '').replace(r'(?!\d)', '')


def _pattern_insert_index(patterns: list, suggestion: dict) -> int:
    group_count = suggestion.get('group_count', 0)
    has_ampm = suggestion.get('ampm_group') is not None
    for idx, entry in enumerate(patterns):
        if entry.get('is_own_output'):
            continue
        if has_ampm and entry.get('ampm_group') is None and entry.get('group_count', 0) == group_count:
            return idx
        if entry.get('group_count', 0) < group_count:
            return idx
    return len(patterns)


def add_pattern_suggestion(signature: str, config_path: str = '',
                           suggestion: Optional[dict] = None) -> Optional[dict]:
    """Append one generated pattern suggestion to patterns.json."""
    generated = dict(suggestion or PatternDiscoverer.generate_json_suggestion(signature) or {})
    if not generated:
        return None
    generated['description'] = f'{signature}（用户确认添加）'
    config = _load_config_document(config_path)
    patterns = config.setdefault('patterns', [])
    identity = _pattern_identity(generated)
    existing_ids = [p.get('id') for p in patterns if _pattern_identity(p) == identity and p.get('id') is not None]
    patterns[:] = [p for p in patterns if _pattern_identity(p) != identity]

    if existing_ids:
        generated['id'] = existing_ids[0]
    else:
        max_id = max((p.get('id', 0) for p in patterns), default=0)
        generated['id'] = max_id + 1

    _validate_pattern_entry(generated, len(patterns) + 1)
    patterns.insert(_pattern_insert_index(patterns, generated), generated)
    _write_config_document(config, config_path)
    DateExtractor.reload_patterns(config_path)
    return generated


def load_pattern_rules(config_path: str = '') -> list:
    """Load filename recognition rules from patterns.json in stored order."""
    config = _load_config_document(config_path)
    rules = []
    for index, entry in enumerate(config.get('patterns', [])):
        rules.append({
            'index': index,
            'id': entry.get('id', index + 1),
            'name': entry.get('description', f'规则{index + 1}'),
            'regex': entry.get('regex', ''),
            'group_count': entry.get('group_count', ''),
            'group_order': entry.get('group_order', 'YMDhms'),
            'ampm_group': entry.get('ampm_group'),
            'is_own_output': bool(entry.get('is_own_output')),
        })
    return rules


def _find_pattern_index(patterns: list, rule_id: Any = None,
                        fallback_index: Optional[int] = None) -> int:
    if rule_id is not None:
        for idx, entry in enumerate(patterns):
            if str(entry.get('id', '')) == str(rule_id):
                return idx
    if fallback_index is not None and 0 <= fallback_index < len(patterns):
        return fallback_index
    raise ValueError('未找到要操作的识别规则')


def save_pattern_rule(name: str, regex: str, config_path: str = '',
                      rule_id: Any = None,
                      fallback_index: Optional[int] = None,
                      group_count: Optional[int] = None,
                      group_order: str = '',
                      ampm_group: Optional[int] = None,
                      is_own_output: bool = False) -> dict:
    """Add or update one filename recognition rule in patterns.json."""
    if not name.strip():
        raise ValueError('规则名称不能为空')
    if not regex.strip():
        raise ValueError('规则正则不能为空')

    config = _load_config_document(config_path)
    patterns = config.setdefault('patterns', [])
    compiled = re.compile(regex)

    if rule_id is not None or fallback_index is not None:
        idx = _find_pattern_index(patterns, rule_id, fallback_index)
        updated = dict(patterns[idx])
        updated['description'] = name.strip()
        updated['regex'] = regex.strip()
        if group_count is not None:
            updated['group_count'] = group_count
        if group_order:
            updated['group_order'] = group_order
        if ampm_group is not None:
            updated['ampm_group'] = ampm_group
        elif 'ampm_group' in updated and updated.get('ampm_group') is None:
            updated.pop('ampm_group', None)
        _validate_pattern_entry(updated, idx + 1)
        if compiled.groups < updated['group_count']:
            raise ValueError('正则捕获组数量不足')
        if updated.get('ampm_group') is not None and updated['ampm_group'] > compiled.groups:
            raise ValueError('ampm_group 超出捕获组数量')
        patterns[idx] = updated
        saved = updated
    else:
        inferred_group_count = group_count if group_count is not None else compiled.groups
        saved = {
            'id': max((p.get('id', 0) for p in patterns), default=0) + 1,
            'regex': regex.strip(),
            'group_count': inferred_group_count,
            'description': name.strip(),
            'is_own_output': bool(is_own_output),
        }
        if group_order:
            saved['group_order'] = group_order
        if ampm_group is not None:
            saved['ampm_group'] = ampm_group
        _validate_pattern_entry(saved, len(patterns) + 1)
        if compiled.groups < saved['group_count']:
            raise ValueError('正则捕获组数量不足')
        if saved.get('ampm_group') is not None and saved['ampm_group'] > compiled.groups:
            raise ValueError('ampm_group 超出捕获组数量')
        patterns.insert(_pattern_insert_index(patterns, saved), saved)

    _write_config_document(config, config_path)
    DateExtractor.reload_patterns(config_path)
    return saved


def delete_pattern_rule(config_path: str = '', rule_id: Any = None,
                        fallback_index: Optional[int] = None) -> dict:
    """Delete one filename recognition rule from patterns.json."""
    config = _load_config_document(config_path)
    patterns = config.setdefault('patterns', [])
    idx = _find_pattern_index(patterns, rule_id, fallback_index)
    removed = patterns.pop(idx)
    _write_config_document(config, config_path)
    DateExtractor.reload_patterns(config_path)
    return removed


def load_format_profiles(config_path: str = '') -> list:
    """Load built-in and custom output filename formats."""
    config = _load_config_document(config_path)
    current_name = config.get('current_output_format_name', '')
    current_format = config.get('default_output_format') or FORMAT_PRESETS['默认']
    profiles = []
    selected = False
    for name, fmt in FORMAT_PRESETS.items():
        is_current = name == current_name if current_name else fmt == current_format
        selected = selected or is_current
        profiles.append({
            'name': name,
            'format': fmt,
            'builtin': True,
            'current': is_current,
        })
    for item in config.get('output_formats', []):
        fmt = item.get('format', '')
        name = item.get('name', fmt)
        is_current = name == current_name if current_name else (not selected and fmt == current_format)
        selected = selected or is_current
        profiles.append({
            'name': name,
            'format': fmt,
            'builtin': False,
            'current': is_current,
        })
    if profiles and not selected:
        profiles[0]['current'] = True
    return profiles


def save_format_profile(name: str, fmt: str, config_path: str = '',
                        make_current: bool = False,
                        original_name: str = '') -> dict:
    """Add or update a custom filename format profile."""
    if not name.strip():
        raise ValueError('格式名称不能为空')
    _, valid = resolve_format(fmt)
    if not valid:
        raise ValueError(f'非法文件名格式: {fmt}')

    config = _load_config_document(config_path)
    profiles = config.setdefault('output_formats', [])
    if name in FORMAT_PRESETS and FORMAT_PRESETS[name] == fmt:
        saved = {'name': name, 'format': fmt, 'builtin': True}
    else:
        lookup_name = original_name or name
        existing = next((item for item in profiles if item.get('name') == lookup_name), None)
        if existing:
            existing['name'] = name
            existing['format'] = fmt
            saved = existing
        else:
            saved = {'name': name, 'format': fmt}
            profiles.append(saved)

    if make_current:
        config['default_output_format'] = fmt
        config['current_output_format_name'] = name
        global _DEFAULT_OUTPUT_FORMAT
        _DEFAULT_OUTPUT_FORMAT = fmt

    _write_config_document(config, config_path)
    return saved


def delete_format_profile(name: str, config_path: str = '') -> dict:
    """Delete one custom output filename format profile."""
    if name in FORMAT_PRESETS:
        raise ValueError('内置输出格式不能删除')
    config = _load_config_document(config_path)
    profiles = config.setdefault('output_formats', [])
    for idx, item in enumerate(profiles):
        if item.get('name') == name:
            removed = profiles.pop(idx)
            if config.get('current_output_format_name') == name:
                config['current_output_format_name'] = '默认'
                config['default_output_format'] = FORMAT_PRESETS['默认']
                global _DEFAULT_OUTPUT_FORMAT
                _DEFAULT_OUTPUT_FORMAT = FORMAT_PRESETS['默认']
            _write_config_document(config, config_path)
            return removed
    raise ValueError('未找到要删除的输出格式')


def _default_history_path() -> Path:
    return _get_exe_dir() / 'rename_history.csv'


def append_history_report(summary: dict, history_path: str = '') -> Path:
    """Append a lightweight operation history row."""
    path = Path(history_path) if history_path else _default_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    fieldnames = ['timestamp', 'mode', 'folder', 'files_count', 'ok_count', 'error_count', 'csv_path']
    row = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'mode': summary.get('mode', ''),
        'folder': summary.get('source_dir', ''),
        'files_count': summary.get('files_count', 0),
        'ok_count': summary.get('ok_count', 0),
        'error_count': summary.get('error_count', 0),
        'csv_path': summary.get('csv_path', ''),
    }
    with open(path, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(_escape_csv_row(row))
    return path


def load_history_reports(history_path: str = '') -> list:
    path = Path(history_path) if history_path else _default_history_path()
    if not path.exists():
        return []
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


@dataclass
class RenameJobOptions:
    source_dir: str
    mode: str = 'preview'
    recursive: bool = False
    fmt_arg: str = ''
    output_dir: str = ''
    csv_path: str = ''
    ext_arg: str = ''
    pattern_config: str = ''
    progress_callback: Optional[Callable[[dict], None]] = None


def _parse_exts(ext_arg: str) -> Optional[set]:
    if not ext_arg:
        return None
    return {e.strip().lower() if e.strip().startswith('.') else f'.{e.strip().lower()}'
            for e in ext_arg.split(',') if e.strip()}


def _resolve_job_format(fmt_arg: str) -> str:
    fmt_source = fmt_arg if fmt_arg else (_DEFAULT_OUTPUT_FORMAT or '默认')
    date_fmt, fmt_valid = resolve_format(fmt_source)
    if not fmt_valid:
        raise ValueError(f'无法识别的输出格式: {fmt_source}')
    if '%%' in date_fmt:
        date_fmt = date_fmt.replace('%%', '%')
    return date_fmt


def run_rename_job(options: RenameJobOptions) -> dict:
    """
    Callable orchestration layer for CLI/TUI frontends.

    It returns structured summary data while keeping PhotoRenamer as the core
    engine. Textual should call this layer instead of driving main().
    """
    if options.mode not in ('preview', 'execute'):
        raise ValueError('mode 必须为 preview 或 execute')

    if options.pattern_config:
        set_pattern_config_path(options.pattern_config)
        DateExtractor.reload_patterns(options.pattern_config)
    else:
        DateExtractor._ensure_initialized()

    source_dir = Path(options.source_dir)
    if not source_dir.is_dir():
        raise FileNotFoundError(f'源文件夹不存在: {source_dir}')

    date_fmt = _resolve_job_format(options.fmt_arg)
    renamer = PhotoRenamer(
        source_dir=str(source_dir),
        recursive=options.recursive,
        fmt=date_fmt,
        output_dir=options.output_dir,
        exts=_parse_exts(options.ext_arg),
    )

    files = renamer.scan_files()
    if options.mode == 'preview':
        preview_progress = ProgressBar(
            len(files),
            desc='预览分析',
            disable=True,
            callback=options.progress_callback,
            stage='preview',
        )
        results = renamer.process(mode='preview', progress=preview_progress)
        csv_path = options.csv_path or str(source_dir / 'preview_report.csv')
        renamer.write_csv(csv_path)
        ok_count = sum(1 for r in results if r.get('status') == 'ok')
    else:
        ok_count = renamer.execute(progress_callback=options.progress_callback)
        results = renamer.results
        csv_path = options.csv_path or str(source_dir / 'rename_log.csv')
        renamer.write_csv(csv_path)

    return {
        'history_path': str(append_history_report({
            'mode': options.mode,
            'source_dir': str(source_dir),
            'files_count': len(files),
            'ok_count': ok_count,
            'error_count': sum(1 for r in results if r.get('status') not in ('ok',)),
            'csv_path': csv_path,
        })) if options.mode == 'execute' else '',
        'mode': options.mode,
        'source_dir': str(source_dir),
        'recursive': options.recursive,
        'date_fmt': date_fmt,
        'output_dir': options.output_dir,
        'files_count': len(files),
        'ok_count': ok_count,
        'error_count': sum(1 for r in results if r.get('status') not in ('ok',)),
        'csv_path': csv_path,
        'results': results,
    }


def launch_tui():
    """Launch the optional Textual UI."""
    try:
        from photo_renamer_tui import PhotoRenamerApp
    except ModuleNotFoundError as e:
        if e.name == 'textual':
            print('[ERROR] Textual 图形终端界面依赖未安装。')
            print('        请先运行: pip install -r requirements-tui.txt')
            return 1
        raise

    PhotoRenamerApp().run()
    return 0


# ╔══════════════════════════════════════════════════════════╗
# ║              CLI 入口                                    ║
# ╚══════════════════════════════════════════════════════════╝

def resolve_format(fmt_arg: str) -> tuple:
    """解析格式参数：预设名 / 预设值 / 自定义 Python 日期格式

    返回值: (format_str, is_valid)
    - format_str: 解析后的 strftime 格式字符串
    - is_valid:   True=合法格式, False=无法解析（将被拒绝）
    """
    # 先看是否是预设名
    if fmt_arg in FORMAT_PRESETS:
        return FORMAT_PRESETS[fmt_arg], True
    # 再看是否是预设值（短路径匹配）
    for preset_name, preset_fmt in FORMAT_PRESETS.items():
        if fmt_arg == preset_fmt:
            return preset_fmt, True
    # 自定义格式：验证是否是合法 strftime 格式
    # 合法格式至少应包含一个 % 格式符（%Y/%m/%d/%H/%M/%S 等）
    if not re.search(r'%[a-zA-Z]', fmt_arg):
        return fmt_arg, False
    # 用一个测试日期实际调用 strftime，确认格式合法
    try:
        sample_name = datetime(2026, 6, 2, 15, 20, 30).strftime(fmt_arg)
    except (ValueError, TypeError):
        return fmt_arg, False
    if not is_safe_filename_component(sample_name):
        return fmt_arg, False
    return fmt_arg, True


def _run_discover_mode(source_dir: Path, files: list, date_fmt: str, csv_arg: str):
    """
    智能发现模式：扫描未匹配文件中的潜在日期格式。
    1. 用现有模式尝试提取日期
    2. 对未能匹配的文件用启发式算法发现潜在格式
    3. 按模式签名分组展示
    4. 导出预览 CSV
    """
    print(f'\n{"=" * 60}')
    print(f'  🔍 智能模式发现')
    print(f'{"=" * 60}')

    # 第一阶段：统计现有模式覆盖情况
    matched = 0
    unmatched = []
    for fp in files:
        dt, source = DateExtractor._from_filename(fp)
        if dt:
            matched += 1
        else:
            unmatched.append(fp)

    print(f'\n  文件总数:     {len(files)}')
    print(f'  已有模式匹配: {matched} ({matched * 100 // max(len(files), 1)}%)')
    print(f'  未匹配文件:   {len(unmatched)}')

    if not unmatched:
        print(f'\n  ✓ 所有文件均能被现有模式覆盖，无需发现新模式。')
        return

    # 第二阶段：启发式发现
    print(f'\n  正在扫描未匹配文件中的潜在日期格式...')
    discoveries = PatternDiscoverer.discover(unmatched, existing_extractor=DateExtractor)

    if not discoveries:
        print(f'\n  ✗ 未在 {len(unmatched)} 个文件中发现可识别的日期格式。')
        print(f'    以下文件可能需要手动命名：')
        for fp in unmatched[:10]:
            print(f'      - {fp.name}')
        if len(unmatched) > 10:
            print(f'      ... 还有 {len(unmatched) - 10} 个文件')
        return

    discovered_count = sum(len(v) for v in discoveries.values())
    print(f'  发现 {discovered_count} 个文件包含潜在日期（{len(discoveries)} 种模式签名）')

    # 第三阶段：按模式签名分组展示
    print(f'\n{"─" * 80}')
    sig_order = sorted(discoveries.keys(),
                       key=lambda s: (-len(discoveries[s]), s))

    csv_rows = []

    for sig in sig_order:
        items = discoveries[sig]
        print(f'\n  📋 模式签名: {sig}（{len(items)} 个文件）')
        print(f'  {"─" * 40}')

        # 生成建议的 JSON 条目
        suggestion = PatternDiscoverer.generate_json_suggestion(sig)
        if suggestion:
            print(f'  建议 JSON 配置:')
            print(f'    {{')
            print(f'      "regex": "{suggestion["regex"]}",')
            print(f'      "group_count": {suggestion["group_count"]},')
            print(f'      "description": "{suggestion["description"]}",')
            print(f'      "is_own_output": false')
            print(f'    }}')

        # 显示匹配示例（最多5个）
        for item in items[:5]:
            fname = item['file'].name
            dt_str = item['datetime'].strftime('%Y-%m-%d %H:%M:%S')
            match = item['match_text']
            cat_note = ' ⚠歧义' if item.get('category') == 'alt_order' else ''
            print(f'    {fname}')
            print(f'    → 匹配: "{match}" → {dt_str}{cat_note}')

            csv_rows.append({
                'original': str(item['file']),
                'match_text': match,
                'detected_date': dt_str,
                'signature': sig,
                'category': item.get('category', ''),
            })

        if len(items) > 5:
            print(f'    ... 还有 {len(items) - 5} 个文件')

    # 导出 CSV
    csv_path = csv_arg if csv_arg else str(source_dir / 'discover_patterns.csv')
    if csv_rows:
        import csv
        fieldnames = ['original', 'match_text', 'detected_date', 'signature', 'category']
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(_escape_csv_row(row) for row in csv_rows)
        print(f'\n{"─" * 80}')
        print(f'  发现报告已保存: {csv_path}')

    print(f'\n{"=" * 60}')
    print(f'  使用方法:')
    print(f'  1. 核对上面的匹配结果，确认日期是否正确')
    print(f'  2. 查看 CSV 文件: {csv_path}')
    print(f'  3. 将建议的 JSON 条目复制到 patterns.json 的 "patterns" 数组中')
    print(f'  4. 重新运行 photo_renamer.py 即可识别新模式')
    print(f'{"=" * 60}')


def _run_dedup_mode(source_dir: Path, files: list, date_fmt: str,
                    mode: str, csv_arg: str):
    """
    独立副本整理模式：自动检测文件夹命名规律，
    将 (1)/(2) 等 Windows 副本文件重新分配到邻近空闲时间槽。
    保持命名格式与文件夹内其他文件一致。
    """
    print(f'\n{"=" * 60}')
    print(f'  📋 副本文件整理模式')
    print(f'{"=" * 60}')

    # ── Step 1: 检测文件夹主流命名格式 ──
    # 构建 renamed_entries: [(entry, compiled_regex), ...] 保持与 _renamed_patterns 同序
    renamed_entries = []
    for _, entry in DateExtractor._patterns:
        if entry.get('is_own_output'):
            compiled = re.compile('^' + entry['regex'] + '$')
            renamed_entries.append((entry, compiled))

    if not renamed_entries:
        print(f'\n  ✗ 未找到自有输出格式定义，无法检测命名规律。')
        return

    # 统计每种格式匹配的文件数（含副本的 clean_stem）
    pattern_hits = {}     # entry_id → count
    pattern_samples = {}  # entry_id → [stem, ...]

    for fp in files:
        stem = fp.stem
        # 对副本文件，使用去掉后缀后的 clean_stem 进行格式检测
        dup_m = DateExtractor.DUP_SUFFIX_RE.match(stem)
        check_stem = dup_m.group(1).strip() if dup_m else stem

        for entry, compiled in renamed_entries:
            m = compiled.match(check_stem)
            if not m:
                continue
            try:
                groups = [int(g) for g in m.groups()]
                y, mo, d = groups[0], groups[1], groups[2]
                if not (1970 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31):
                    continue
                if len(groups) >= 5:
                    hh, mm = groups[3], groups[4]
                    if not (0 <= hh <= 23 and 0 <= mm <= 59):
                        continue
                if len(groups) >= 6:
                    ss = groups[5]
                    if not (0 <= ss <= 59):
                        continue
                eid = entry.get('id', 0)
                pattern_hits[eid] = pattern_hits.get(eid, 0) + 1
                if eid not in pattern_samples:
                    pattern_samples[eid] = []
                if len(pattern_samples[eid]) < 3:
                    pattern_samples[eid].append(check_stem)
                break
            except (ValueError, IndexError):
                continue

    if not pattern_hits:
        print(f'\n  ✗ 未能检测到文件夹内的命名格式。')
        print(f'    请确保文件夹中包含已按标准格式命名的文件。')
        print(f'    支持格式: YYYY.MM.DD_HHMM / YYYY.MM.DD_HHMMSS /')
        print(f'              YYYY-MM-DD_HH-MM-SS / YYYYMMDD_HHMMSS')
        return

    # 取匹配数最多的格式
    best_id = max(pattern_hits, key=pattern_hits.get)
    best_entry = None
    best_compiled = None
    for entry, compiled in renamed_entries:
        if entry.get('id') == best_id:
            best_entry = entry
            best_compiled = compiled
            break

    if best_entry is None:
        print(f'\n  ✗ 内部错误：无法定位最佳格式条目。')
        return

    dedup_fmt = _DEDUP_FORMAT_BY_ID.get(best_id, date_fmt)
    pattern_desc = best_entry.get('description', f'模式{best_id}')

    print(f'\n  检测到主流命名格式: {pattern_desc}')
    print(f'  匹配文件数:         {pattern_hits[best_id]} 个')
    if best_id in pattern_samples:
        print(f'  示例:               {", ".join(pattern_samples[best_id])}')
    print(f'  副本将使用格式:     {dedup_fmt}')

    # ── Step 2: 筛选副本文件 ──
    dup_files = []
    for fp in files:
        dup_m = DateExtractor.DUP_SUFFIX_RE.match(fp.stem)
        if not dup_m:
            continue
        clean_stem = dup_m.group(1).strip()
        m = best_compiled.match(clean_stem)
        if not m:
            continue
        try:
            groups = [int(g) for g in m.groups()]
            y, mo, d = groups[0], groups[1], groups[2]
            if not (1970 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31):
                continue
            hh = groups[3] if len(groups) >= 5 else 0
            mm = groups[4] if len(groups) >= 5 else 0
            ss = groups[5] if len(groups) >= 6 else 0
            if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
                continue
            dt = datetime(y, mo, d, hh, mm, ss)
            dup_files.append({
                'file': fp,
                'clean_stem': clean_stem,
                'datetime': dt,
            })
        except (ValueError, IndexError):
            continue

    if not dup_files:
        print(f'\n  ✓ 未发现副本文件（无 (1)/(2)/（1）/（2）等后缀）。')
        return

    print(f'\n  发现 {len(dup_files)} 个副本文件:')
    for d in dup_files:
        print(f'    - {d["file"].name}')

    # ── Step 3: 收集已被占用的文件名 ──
    occupied_stems = set()  # (parent, stem, ext)
    for fp in files:
        m = best_compiled.match(fp.stem)
        if not m:
            continue
        try:
            groups = [int(g) for g in m.groups()]
            dt = datetime(
                groups[0], groups[1], groups[2],
                groups[3] if len(groups) >= 5 else 0,
                groups[4] if len(groups) >= 5 else 0,
                groups[5] if len(groups) >= 6 else 0,
            )
            stem = dt.strftime(dedup_fmt)
            occupied_stems.add((fp.parent, stem, fp.suffix.lower()))
        except (ValueError, IndexError):
            continue

    # ── Step 4: 为每个副本查找邻近空闲时间槽 ──
    results = []
    assigned = set()  # 本次已分配的 (parent, stem, ext)

    for dup in dup_files:
        fp = dup['file']
        original_dt = dup['datetime']
        ext = fp.suffix.lower()
        parent = fp.parent

        found = False
        for distance in range(1, 1440):  # 最多搜 24 小时，邻近优先
            # 先向后(+1)再向前(-1)，交替搜索
            for direction, label in [(1, '+'), (-1, '-')]:
                offset = direction * distance
                candidate_dt = original_dt + timedelta(minutes=offset)
                candidate_stem = candidate_dt.strftime(dedup_fmt)
                key = (parent, candidate_stem, ext)

                # 磁盘已有 → 跳过
                if (parent / f'{candidate_stem}{ext}').exists():
                    continue
                # 已被占用或已分配 → 跳过
                if key in occupied_stems or key in assigned:
                    continue

                assigned.add(key)
                new_name = f'{candidate_stem}{ext}'
                dst = str(parent / new_name)
                results.append({
                    'original': str(fp),
                    'new_name': new_name,
                    'date': candidate_dt.strftime('%Y-%m-%d %H:%M:%S'),
                    'source': f'副本整理({label}{distance}分钟)',
                    'status': 'ok',
                    'dst': dst,
                    'offset': offset,
                })
                found = True
                break
            if found:
                break

        if not found:
            results.append({
                'original': str(fp),
                'new_name': fp.name,
                'date': original_dt.strftime('%Y-%m-%d %H:%M:%S'),
                'source': '副本整理(未找到空闲槽)',
                'status': 'error',
                'dst': str(fp),
                'offset': 0,
            })

    # ── Step 5: 展示 / 执行 ──
    if mode == 'preview':
        print(f'\n{"─" * 80}')
        print(f'  {"原文件名":<50} {"→ 新文件名":>30}')
        print(f'{"─" * 80}')
        ok_count = 0
        for r in results:
            src_name = Path(r['original']).name
            if r['status'] == 'ok':
                ok_count += 1
                print(f'  ✓ {src_name:<47} → {r["new_name"]}')
                print(f'    [{r["date"]}] {r["source"]}')
            else:
                print(f'  ✗ {src_name:<47} → 未找到可用时间槽')
        print(f'{"─" * 80}')
        print(f'  可处理: {ok_count}/{len(results)}')

        # 导出 CSV
        csv_path = csv_arg if csv_arg else str(source_dir / 'dedup_preview.csv')
        if results:
            import csv as _csv
            fieldnames = ['original', 'new_name', 'date', 'source', 'status', 'offset']
            with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = _csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(_escape_csv_row(row) for row in results)
            print(f'\n  预览报告已保存: {csv_path}')

    elif mode == 'execute':
        print(f'\n{"─" * 80}')
        success = 0
        for r in results:
            if r['status'] != 'ok':
                src_name = Path(r['original']).name
                print(f'  ✗ {src_name} → 未找到可用时间槽，跳过')
                continue
            src = Path(r['original'])
            dst = Path(r['dst'])
            try:
                if dst.exists():
                    r['status'] = 'conflict'
                    r['error'] = f'目标已存在: {dst}'
                    print(f'  ✗ {src.name} → 冲突（目标已存在）')
                    continue
                src.rename(dst)
                success += 1
                print(f'  ✓ {src.name} → {dst.name}')
            except Exception as e:
                r['status'] = 'error'
                r['error'] = str(e)
                print(f'  ✗ {src.name}: {e}')

        print(f'\n{"─" * 80}')
        print(f'  完成: {success}/{len(results)} 个文件已处理')

        # 导出日志
        csv_path = csv_arg if csv_arg else str(source_dir / 'dedup_log.csv')
        if results:
            import csv as _csv
            fieldnames = ['original', 'new_name', 'date', 'source', 'status', 'offset']
            with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = _csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(_escape_csv_row(row) for row in results)
            print(f'  日志已保存: {csv_path}')


def _input_path(prompt: str) -> str:
    """交互式输入路径（处理拖放引号、空路径回退 exe 目录）"""
    raw = input(prompt).strip()
    raw = raw.strip('"\'')
    if not raw:
        # 空路径 -> 使用 exe/脚本所在目录
        return str(_get_exe_dir())
    return raw


def _show_error(title: str, message: str):
    """显示错误弹窗（GUI 环境下使用 tkinter，否则打印到终端）"""
    print(f'\n[ERROR] {title}: {message}')
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        pass  # tkinter 不可用时静默失败


def _offer_new_patterns(source_dir: Path, files: list, date_fmt: str):
    """处理完成后，检查未匹配文件，智能发现新模式并让用户确认添加到 patterns.json"""
    # 收集未能提取日期的文件
    unmatched = []
    for fp in files:
        dt, source = DateExtractor.extract(fp)
        if dt is None:
            unmatched.append(fp)

    if not unmatched:
        return

    # 用启发式算法发现潜在日期格式
    discoveries = PatternDiscoverer.discover(unmatched, existing_extractor=DateExtractor)

    if not discoveries:
        n = len(unmatched)
        print(f'\n  [提示] 有 {n} 个文件未能提取日期，也未发现可识别的日期模式。')
        if n <= 10:
            for fp in unmatched:
                print(f'    - {fp.name}')
        return

    discovered_count = sum(len(v) for v in discoveries.values())
    print(f'\n{"=" * 60}')
    print(f'  智能发现：检测到 {discovered_count} 个文件可能使用新的命名规则')
    print(f'{"=" * 60}')

    sig_order = sorted(discoveries.keys(), key=lambda s: (-len(discoveries[s]), s))

    for sig in sig_order:
        items = discoveries[sig]
        print(f'\n  [{len(items)} 个文件] {sig}')
        for item in items[:3]:
            fname = item['file'].name
            dt_str = item['datetime'].strftime('%Y-%m-%d %H:%M:%S')
            print(f'    {fname} -> {dt_str}')
        if len(items) > 3:
            print(f'    ... 还有 {len(items) - 3} 个')

    print(f'\n  是否将以上 {len(sig_order)} 种模式添加到 patterns.json？')
    confirm = input('  输入 y 确认添加，其他键跳过: ').strip().lower()
    if confirm != 'y':
        print('  已跳过，下次运行仍会提示。')
        return

    # 添加到 patterns.json
    config_path = _find_config_path()
    if not config_path.exists():
        print('  [ERROR] patterns.json 不存在，无法添加。')
        return

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        patterns = config.get('patterns', [])
        max_id = max((p.get('id', 0) for p in patterns), default=0)

        added = 0
        for sig in sig_order:
            suggestion = PatternDiscoverer.generate_json_suggestion(sig)
            if not suggestion:
                print(f'  [SKIP] {sig}: 无法生成有效正则')
                continue
            max_id += 1
            suggestion['id'] = max_id
            # 用实际签名替换通用描述
            suggestion['description'] = sig + '（自动发现）'
            patterns.append(suggestion)
            added += 1
            print(f'  [+] 已添加模式 {max_id}: {sig}')

        if added > 0:
            config['patterns'] = patterns
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            print(f'\n  已成功添加 {added} 个新模式到 {config_path}')
            print(f'  模式已重新加载，下次处理将自动识别这些文件。')
            DateExtractor.reload_patterns()
        else:
            print('\n  未添加任何新模式。')

    except Exception as e:
        print(f'  [ERROR] 写入 patterns.json 失败: {e}')


def _interactive_menu():
    """无参数时的交互式菜单（双击 exe 时进入）"""
    while True:
        print()
        print('=' * 58)
        print('   Photo & Video Renamer v2.8')
        print('=' * 58)
        print()
        print('   [1] 预览 - 单个文件夹')
        print('   [2] 预览 - 含所有子文件夹')
        print('   [3] 执行 - 单个文件夹（直接重命名）')
        print('   [4] 执行 - 含所有子文件夹（直接重命名）')
        print('   [5] 整理重复文件名 - 预览')
        print('   [6] 整理重复文件名 - 执行')
        print('   [7] 自定义参数运行')
        print('   [8] 生成默认 patterns.json')
        print('   [0] 退出')
        print()
        choice = input('   请选择 (0-8): ').strip()
        print()

        if choice == '0':
            return None

        if choice == '8':
            config_path = Path.cwd() / 'patterns.json'
            generate_default_config(config_path)
            print('   已生成 patterns.json，请编辑后重新运行。')
            input('   按回车返回菜单...')
            continue

        if choice == '7':
            src = _input_path('   源文件夹路径（可直接拖拽文件夹到此处）: ')
            recursive = input('   包含子文件夹？(y/n，默认n): ').strip().lower() == 'y'
            mode = input('   模式 (preview/execute，默认preview): ').strip()
            if mode not in ('preview', 'execute'):
                mode = 'preview'
            fmt = input('   日期格式（直接回车=默认）: ').strip()
            outdir = input('   输出目录（留空=原地重命名）: ').strip()
            csv_path = input('   CSV报告路径（留空=自动）: ').strip()
            return {
                'source': src,
                'mode': mode,
                'recursive': recursive,
                'format': fmt,
                'output_dir': outdir,
                'csv': csv_path,
                'ext': '',
                'force': False,
                'discover': False,
                'pattern_config': '',
                'dedup': False,
            }

        if choice in ('1', '2', '3', '4', '5', '6'):
            src = _input_path('   源文件夹路径（可直接拖拽文件夹到此处）: ')
            recursive = choice in ('2', '4')
            mode = 'preview' if choice in ('1', '2', '5') else 'execute'
            dedup = choice in ('5', '6')

            if mode == 'execute':
                print()
                print('   ' + '!' * 52)
                print('   警告！将对以下路径中的文件直接重命名：')
                print(f'   {src}')
                print('   此操作不可撤销！')
                print('   ' + '!' * 52)
                print()
                confirm = input('   输入 yes 确认执行: ').strip().lower()
                if confirm != 'yes':
                    print('   已取消。')
                    input('   按回车返回菜单...')
                    continue

            csv_auto = ''
            if mode == 'preview' and not dedup:
                csv_auto = str(Path(src) / 'preview_report.csv')
            elif mode == 'preview' and dedup:
                csv_auto = str(Path(src) / 'dedup_preview.csv')
            elif dedup:
                csv_auto = str(Path(src) / 'dedup_log.csv')

            return {
                'source': src,
                'mode': mode,
                'recursive': recursive,
                'format': '',
                'output_dir': '',
                'csv': csv_auto,
                'ext': '',
                'force': False,
                'discover': False,
                'pattern_config': '',
                'dedup': dedup,
            }

        print('   无效选项，请重新选择。')


def main():
    # Windows GBK 终端下 emoji/Unicode 字符会触发 UnicodeEncodeError
    # 强制 stdout/stderr 使用 UTF-8 输出
    if sys.platform == 'win32':
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, LookupError):
            pass  # Python < 3.7 或无 reconfigure 方法，忽略

    parser = argparse.ArgumentParser(
        description='Photo & Video Renamer - 按日期重命名照片和视频',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  python photo_renamer.py -s "D:\\照片" -m preview
  python photo_renamer.py -s "D:\\照片" -m execute -r -o "D:\\已整理"
  python photo_renamer.py -s "D:\\照片" -m preview -f "精确到秒" --csv preview.csv
  python photo_renamer.py -s "D:\\照片" -m execute -f "%%Y-%%m-%%d_%%H%%M%%S"
  python photo_renamer.py -s "D:\\照片" -m execute --force     （跳过已重命名检查）
        ''')

    parser.add_argument('-s', '--source', default='', help='源文件夹路径（不指定则进入交互菜单；交互模式下可直接拖拽）')
    parser.add_argument('-m', '--mode', choices=['preview', 'execute'], default='preview',
                        help='模式: preview=模拟预览(不改文件), execute=真正重命名')
    parser.add_argument('-r', '--recursive', action='store_true', help='递归处理子目录')
    parser.add_argument('-f', '--format', default='',
                        help=f'输出格式（不指定则使用 patterns.json 中的默认格式）。预设: {", ".join(FORMAT_PRESETS.keys())}；或自定义 Python 日期格式')
    parser.add_argument('-o', '--output-dir', default='', help='输出目录（复制模式），不指定则在原位置重命名')
    parser.add_argument('--csv', default='', help='预览模式导出 CSV 文件路径')
    parser.add_argument('-e', '--ext', default='', help='限制扩展名（逗号分隔），如 ".jpg,.png,.mp4"')
    parser.add_argument('-F', '--force', action='store_true',
                        help='强制执行，跳过"已重命名目录"检查')
    parser.add_argument('--discover', action='store_true',
                        help='智能发现模式：扫描未匹配文件中的潜在日期格式，导出预览 CSV')
    parser.add_argument('--pattern-config', default='',
                        help='自定义 patterns.json 路径（默认自动查找）')
    parser.add_argument('--dedup', action='store_true',
                        help='副本整理模式：自动检测文件夹命名规律，将 (1)/(2) 副本文件插入到邻近空闲时间槽')
    parser.add_argument('--generate-config', action='store_true',
                        help='在当前位置生成默认 patterns.json 配置文件（然后退出）')
    parser.add_argument('--undo-csv', default='',
                        help='根据 rename_log.csv 撤销原地重命名（按日志倒序恢复）')
    parser.add_argument('--undo-force', action='store_true',
                        help='撤销时允许覆盖已存在的原路径（谨慎使用）')
    parser.add_argument('--tui', action='store_true',
                        help='启动 Textual 图形终端界面（需安装 requirements-tui.txt）')

    args = parser.parse_args()

    # 无任何 CLI 参数（如双击 exe）时，直接启动 TUI 图形界面
    if args.tui or (
        not args.source
        and not args.undo_csv
        and not args.generate_config
        and not args.discover
        and not args.dedup
    ):
        sys.exit(launch_tui())

    # ── 撤销重命名（不需要源目录） ─────────────────────
    if args.undo_csv:
        summary = undo_from_csv(args.undo_csv, force=args.undo_force)
        report_path = write_undo_report(args.undo_csv, summary['details'])
        print('=' * 60)
        print('  Photo & Video Renamer - 撤销重命名')
        print('=' * 60)
        print(f'  CSV日志:    {args.undo_csv}')
        print(f'  记录数:     {summary["rows"]}')
        print(f'  已恢复:     {summary["restored"]}')
        print(f'  已跳过:     {summary["skipped"]}')
        print(f'  错误:       {summary["errors"]}')
        print(f'  撤销报告:   {report_path}')
        if summary['skipped'] or summary['errors']:
            print('  提示: 请查看撤销报告中的 undo_error 列。')
        sys.exit(0 if summary['errors'] == 0 else 1)

    # ── 生成默认配置（不需要源目录） ──────────────────
    if args.generate_config:
        config_path = Path(args.pattern_config) if args.pattern_config else None
        generate_default_config(config_path)
        print('请编辑该文件后重新运行程序即可。')
        sys.exit(0)

    is_interactive = not args.source

    while True:
        try:

            # ── 无参数时进入交互菜单（双击 exe 时） ────────────
            if not args.source:
                opts = _interactive_menu()
                if opts is None:
                    break
                args.source = opts['source']
                args.mode = opts['mode']
                args.recursive = opts['recursive']
                args.format = opts['format']
                args.output_dir = opts['output_dir']
                args.csv = opts['csv']
                args.ext = opts['ext']
                args.force = opts['force']
                args.discover = opts['discover']
                args.pattern_config = opts['pattern_config']
                args.dedup = opts['dedup']

            # ── 设置自定义模式配置路径 ───────────────────────
            if args.pattern_config:
                set_pattern_config_path(args.pattern_config)

            # ── 提前初始化 DateExtractor（触发模式加载） ─────
            DateExtractor._ensure_initialized()

            # 验证源路径
            source_dir = Path(args.source)
            if not source_dir.is_dir():
                print(f'[ERROR] 源文件夹不存在: {args.source}')
                if is_interactive:
                    args.source = ''
                    continue
                sys.exit(1)

            # 解析格式：优先级 -f 参数 > patterns.json default_output_format > 内置默认
            fmt_arg = args.format if args.format else (_DEFAULT_OUTPUT_FORMAT or '默认')
            date_fmt, fmt_valid = resolve_format(fmt_arg)
            if not fmt_valid:
                print(f'[ERROR] 无法识别的输出格式: {fmt_arg}')
                print(f'  可用预设: {", ".join(FORMAT_PRESETS.keys())}')
                print(f'  自定义格式示例: %Y.%m.%d_%H%M%S  %Y-%m-%d  %Y年%m月%d日')
                if is_interactive:
                    args.source = ''
                    continue
                sys.exit(1)
            # 处理 Windows cmd 下的 % 转义（%%Y → %Y）
            if '%%' in date_fmt:
                date_fmt = date_fmt.replace('%%', '%')

            # 解析扩展名
            exts = None
            if args.ext:
                exts = {e.strip().lower() if e.startswith('.') else f'.{e.strip().lower()}'
                        for e in args.ext.split(',')}

            # 输出目录验证
            output_dir = args.output_dir

            # 打印摘要
            print('=' * 60)
            print('  Photo & Video Renamer v2.8')
            print('=' * 60)
            print(f'  源文件夹:   {source_dir}')
            print(f'  模式:       {"🔍 预览（不修改文件）" if args.mode == "preview" else "⚡ 执行重命名"}')
            print(f'  子目录:     {"是" if args.recursive else "否"}')
            print(f'  日期格式:   {date_fmt}')
            print(f'  输出方式:   {"复制到: " + output_dir if output_dir else "原地重命名"}')
            print(f'  扩展名:     {", ".join(sorted(exts)) if exts else "全部支持格式"}')
            print(f'  I/O 超时:   {NETWORK_TIMEOUT}s（可通过环境变量 PHOTO_RENAMER_TIMEOUT 调整）')
            print(f'  视频元数据: {get_video_metadata_timeout()}s（patterns.json: video_metadata_timeout_seconds）')
            print('=' * 60)

            # 创建 renamer 实例
            renamer = PhotoRenamer(
                source_dir=str(source_dir),
                recursive=args.recursive,
                fmt=date_fmt,
                output_dir=output_dir,
                exts=exts,
            )

            # 扫描
            print(f'\n正在扫描文件...')
            files = renamer.scan_files()
            print(f'找到 {len(files)} 个文件')
            if not files:
                print('没有找到可处理的文件。')
                if is_interactive:
                    args.source = ''
                    continue
                sys.exit(0)

            # ── 智能发现模式 ─────────────────────────────────
            if args.discover:
                _run_discover_mode(source_dir, files, date_fmt, args.csv)
                if is_interactive:
                    args.source = ''
                    continue
                sys.exit(0)

            # ── 副本整理模式 ─────────────────────────────────
            if args.dedup:
                _run_dedup_mode(source_dir, files, date_fmt, args.mode, args.csv)
                if is_interactive:
                    args.source = ''
                    continue
                sys.exit(0)

            # ── 检测是否已重命名过 ─────────────────────────────
            if files:
                already_count = sum(1 for f in files
                                   if DateExtractor._is_already_renamed_stem(f.stem))
                already_pct = already_count * 100 // len(files)

                if already_count > 0:
                    # 统计已命名文件的类型分布
                    renamed_photos = sum(1 for f in files
                                        if f.suffix.lower() in IMAGE_EXTS
                                        and DateExtractor._is_already_renamed_stem(f.stem))
                    renamed_videos = already_count - renamed_photos

                    print(f'\n{"=" * 60}')
                    print(f'  ⚠ 检测到 {already_count}/{len(files)} ({already_pct}%) 的文件可能已被本工具处理过')
                    if renamed_photos > 0:
                        print(f'     其中照片: {renamed_photos} 个')
                    if renamed_videos > 0:
                        print(f'     其中视频: {renamed_videos} 个')
                    print(f'{"=" * 60}')
                    print(f'  重新处理风险：')
                    print(f'  • 照片：EXIF 信息不变，通常安全')
                    print(f'  • 视频：无 EXIF，依赖文件名解析，时分信息可能被错误修改')
                    print(f'{"=" * 60}')

                    if args.mode == 'execute' and not args.force:
                        print(f'\n  ⛔ 为保护数据，执行模式已自动中止。')
                        print(f'  如需继续，请检查预览结果确认无误后，使用 --force 重新执行：')
                        print(f'  python photo_renamer.py -s "{args.source}" -m execute --force')
                        if args.recursive:
                            print(f'    或: python photo_renamer.py -s "{args.source}" -m execute -r --force')
                        if is_interactive:
                            args.source = ''
                            continue
                        sys.exit(2)
                    elif args.mode == 'execute' and args.force:
                        print(f'  ⚡ --force 已启用，跳过保护检查，继续执行...')
                    else:
                        print(f'  🔍 预览模式下仅展示结果，不会修改文件。')
                        print(f'     请仔细检查视频文件的日期是否正确。')
                    print()

            # 预览模式
            if args.mode == 'preview':
                results = renamer.process(mode='preview')
                print(f'\n{"─" * 80}')
                print(f'  {"原文件名":<50} {"→ 新文件名":>30}')
                print(f'{"─" * 80}')

                ok_count = 0
                for r in results:
                    src_name = Path(r['original']).name
                    status_icon = '⏱' if 'timeout' in r.get('source', '') else ('✓' if r['status'] == 'ok' else '✗')
                    if r['status'] == 'ok':
                        ok_count += 1
                        print(f'  {status_icon} {src_name:<47} → {r["new_name"]}')
                        print(f'    [{r["date"]}] 来源: {r["source"]}')
                    else:
                        reason = '超时（网络延迟）' if 'timeout' in r.get('source', '') else '无法提取日期'
                        print(f'  {status_icon} {src_name:<47} → {reason}')

                print(f'{"─" * 80}')
                print(f'  成功: {ok_count}/{len(results)}')

                # 写入 CSV
                csv_path = args.csv
                if not csv_path:
                    csv_path = str(source_dir / 'preview_report.csv')
                renamer.write_csv(csv_path)
                print(f'\n预览报告已保存: {csv_path}')

            # 执行模式
            elif args.mode == 'execute':
                success = renamer.execute()
                total = len(files)

                # 打印结果
                print(f'\n{"─" * 80}')
                errors = [r for r in renamer.results if r['status'] != 'ok']
                for r in errors:
                    print(f'  ✗ {Path(r["original"]).name}: {r.get("error", "无法提取日期")}')

                print(f'\n{"─" * 80}')
                print(f'  完成: {success}/{total} 个文件已处理')
                if errors:
                    print(f'  失败: {len(errors)} 个')

                # 导出执行报告
                csv_path = str(source_dir / 'rename_log.csv')
                renamer.write_csv(csv_path)
                print(f'  日志已保存: {csv_path}')



        except SystemExit:
            if is_interactive:
                args.source = ''
                continue
            raise

        except KeyboardInterrupt:
            if is_interactive:
                print('\n\n  已取消当前操作。')
                args.source = ''
                continue
            raise

        except Exception as e:
            _show_error('处理出错', str(e))
            if is_interactive:
                args.source = ''
                input('\n按回车键返回菜单...')
                continue
            raise

        # ── 交互模式：智能发现新规则 + 返回菜单 ──
        if is_interactive:
            try:
                _offer_new_patterns(source_dir, files, date_fmt)
            except Exception:
                pass  # 智能发现失败不影响主流程
            input('\n按回车键返回菜单...')
            args.source = ''  # 重置，下次循环回到菜单
            continue

        break  # CLI 模式：处理完毕退出

if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        pass
    except Exception as e:
        _show_error('程序异常', str(e))
        input('\n程序异常终止，按回车键退出...')
