#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Photo & Video Renamer v2.4
按日期重命名照片和视频，优先级：EXIF → 文件名日期 → Unix时间戳 → 文件修改时间

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
import sys
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ─── 超时保护（网络盘/损坏文件抗卡顿） ─────────────────

# 全局超时配置（秒），可根据网络环境调整
NETWORK_TIMEOUT = float(os.environ.get('PHOTO_RENAMER_TIMEOUT', '15'))


def run_with_timeout(func: Callable, *args, timeout: float = None,
                     default: Any = None, **kwargs) -> Any:
    """
    在独立线程中执行 func(*args, **kwargs)，超时返回 default。
    使用 ThreadPoolExecutor 确保超时后线程被正确清理。
    """
    t = timeout if timeout is not None else NETWORK_TIMEOUT
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=t)
        except (concurrent.futures.TimeoutError, Exception):
            return default


# ─── 进度条（零依赖，自适应终端宽度） ──────────────────────

class ProgressBar:
    """终端内联进度条，支持 \r 原地刷新"""

    def __init__(self, total: int, desc: str = '处理中', width: int = 30,
                 disable: bool = False):
        self.total = max(total, 1)
        self.desc = desc
        self.width = width
        self.disable = disable or not sys.stdout.isatty()
        self.current = 0
        self._last_len = 0
        self._start_time = time.time()
        self.timeouts = 0  # 超时跳过计数

    def update(self, n: int = 1, info: str = ''):
        """增加进度并刷新显示"""
        self.current += n
        elapsed = time.time() - self._start_time
        elapsed_str = f'{int(elapsed // 60)}m{int(elapsed % 60)}s'
        if self.disable:
            # 非交互终端：每 20% 打印一行
            if self.current == 1 or self.current >= self.total or self.current % max(1, self.total // 5) == 0:
                pct = self.current * 100 // self.total
                sys.stdout.write(f'  {self.desc}: {self.current}/{self.total} ({pct}%) [{elapsed_str}]')
                if self.timeouts > 0:
                    sys.stdout.write(f' 超时跳过: {self.timeouts}')
                sys.stdout.write('\n')
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

        sys.stdout.write(line)
        sys.stdout.flush()

    def close(self):
        """完成时换行"""
        elapsed = time.time() - self._start_time
        elapsed_str = f'{int(elapsed // 60)}m{int(elapsed % 60)}s'
        if not self.disable:
            sys.stdout.write('\n')
            sys.stdout.flush()
        sys.stdout.write(f'  ✓ {self.desc}完成: {self.current}/{self.total} [{elapsed_str}]')
        if self.timeouts > 0:
            sys.stdout.write(f'  超时跳过: {self.timeouts}')
        sys.stdout.write('\n')
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
     "group_count": 6, "description": "YYYY-MM-DD HH-MM-SS（空格分隔，如 2015-12-04 15-39-11-于果）", "is_own_output": False},
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
]

# 全局模式配置路径
_PATTERN_CONFIG_PATH: Optional[str] = None


def set_pattern_config_path(path: str):
    """设置自定义 patterns.json 路径"""
    global _PATTERN_CONFIG_PATH
    _PATTERN_CONFIG_PATH = path


def _find_config_path() -> Path:
    """查找 patterns.json：优先自定义路径 → CWD → 脚本目录"""
    if _PATTERN_CONFIG_PATH:
        return Path(_PATTERN_CONFIG_PATH)
    cwd = Path.cwd() / 'patterns.json'
    if cwd.exists():
        return cwd
    return Path(__file__).parent / 'patterns.json'


def _from_default_patterns() -> list:
    """从默认配置编译模式列表"""
    return [(re.compile(e['regex']), dict(e)) for e in _DEFAULT_PATTERNS_CONFIG]


def _load_patterns_from_json(json_path: Path) -> list:
    """从 JSON 文件加载并编译模式，返回 [(compiled_regex, entry_dict), ...]"""
    with open(json_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    patterns = []
    for entry in config.get('patterns', []):
        compiled = re.compile(entry['regex'])
        patterns.append((compiled, dict(entry)))
    return patterns


def generate_default_config(json_path: Path = None):
    """生成默认 patterns.json（首次使用或 --generate-config 时调用）"""
    if json_path is None:
        json_path = _find_config_path()
    config = {
        "version": "2.0",
        "_instructions": (
            "每个 pattern 包含: regex(正则表达式), group_count(捕获组数:3/5/6), "
            "description(描述), is_own_output(是否自有输出格式)。"
            "捕获组必须按 年、月、日、时、分、秒 顺序。"
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
    """按优先级链提取日期：EXIF → 文件名 → Unix时间戳 → 修改时间"""

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
        来源描述: 'EXIF', 'Filename(模式名)', 'UnixTimestamp(ms)', 'FileModifyTime'
        若所有方法均失败且有超时发生，来源标注 '(timeout)'
        """
        had_timeout = False

        # 优先级1: EXIF
        dt, source = cls._from_exif(filepath)
        if 'timeout' in source:
            had_timeout = True
        if dt:
            return dt, source

        # 优先级2: 文件名日期
        dt, source = cls._from_filename(filepath)
        if dt:
            return dt, source

        # 优先级3: Unix 时间戳
        dt, source = cls._from_timestamp(filepath)
        if dt:
            return dt, source

        # 优先级4: 文件修改时间
        dt, source = cls._from_modify_time(filepath)
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
        for idx, (pattern, entry) in enumerate(cls._patterns):
            # 对每个模式遍历所有匹配（而非仅首个），跳过不合法日期直到找到有效的
            for match in pattern.finditer(stem):
                groups = match.groups()
                try:
                    if len(groups) == 6:  # YYYY MM DD HH MM SS
                        y, m, d, hh, mm, ss = map(int, groups)
                        dt = datetime(y, m, d, hh, mm, ss)
                    elif len(groups) == 5:  # YYYY MM DD HH MM
                        y, m, d, hh, mm = map(int, groups)
                        dt = datetime(y, m, d, hh, mm, 0)
                    elif len(groups) == 3:  # YYYY MM DD
                        y, m, d = map(int, groups)
                        dt = datetime(y, m, d, 0, 0, 0)
                    else:
                        continue

                    # 合理性检查
                    if 1970 <= y <= 2099 and 1 <= m <= 12 and 1 <= d <= 31:
                        desc = entry.get('description', f'模式{idx + 1}')
                        return dt, f'Filename({desc})'
                except (ValueError, TypeError):
                    continue

        return None, ''

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
    def _from_modify_time(cls, filepath: Path) -> Tuple[Optional[datetime], str]:
        try:
            mtime = run_with_timeout(lambda p: p.stat().st_mtime, filepath,
                                     timeout=NETWORK_TIMEOUT, default=None)
            if mtime is None:
                return None, 'FileModifyTime(timeout)'
            dt = datetime.fromtimestamp(mtime)
            return dt, 'FileModifyTime'
        except Exception:
            return None, ''


# ╔══════════════════════════════════════════════════════════╗
# ║         智能模式发现（PatternDiscoverer）                ║
# ╚══════════════════════════════════════════════════════════╝

class PatternDiscoverer:
    """
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
        # YYYY-MM-DD HH:MM:SS
        (re.compile(r'(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})'), 6, 'YYYY-MM-DD HH:MM:SS'),
        # YYYY-MM-DD HH-MM-SS
        (re.compile(r'(\d{4})-(\d{2})-(\d{2}) (\d{2})-(\d{2})-(\d{2})'), 6, 'YYYY-MM-DD HH-MM-SS'),
        # YYYY-MM-DD-HH-MM-SS
        (re.compile(r'(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})'), 6, 'YYYY-MM-DD-HH-MM-SS'),
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
            # 跳过已被现有模式匹配的文件
            if existing_extractor:
                dt, _ = existing_extractor._from_filename(fp)
                if dt:
                    continue

            stem = fp.stem
            found = None

            # 阶段1: 精确的扩展格式（含时间，优先级高）
            for pattern, group_count, signature in cls._EXTENDED_PATTERNS:
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
    def _validate_date_groups(cls, groups: tuple, group_count: int) -> bool:
        """验证 Y-M-D 顺序的捕获组是否构成合法日期"""
        y, mo, d = int(groups[0]), int(groups[1]), int(groups[2])
        if not (1970 <= y <= 2099 and 1 <= mo <= 12 and 1 <= d <= 31):
            return False
        if group_count >= 6:
            h, mi, s = int(groups[3]), int(groups[4]), int(groups[5])
            if not (0 <= h <= 23 and 0 <= mi <= 59 and 0 <= s <= 59):
                return False
        if group_count == 5:
            h, mi = int(groups[3]), int(groups[4])
            if not (0 <= h <= 23 and 0 <= mi <= 59):
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
    def _groups_to_datetime(cls, groups: tuple, group_count: int) -> datetime:
        """将 Y-M-D 顺序的捕获组转为 datetime"""
        y, mo, d = int(groups[0]), int(groups[1]), int(groups[2])
        h = mi = s = 0
        if group_count == 5:
            h, mi = int(groups[3]), int(groups[4])
        elif group_count == 6:
            h, mi, s = int(groups[3]), int(groups[4]), int(groups[5])
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

        return {
            "regex": ''.join(regex_parts),
            "group_count": group_count,
            "description": ''.join(description_parts) + '（手动添加）',
            "is_own_output": False,
        }


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

    def execute(self) -> int:
        """真正执行重命名/复制，返回成功数"""
        # 第一阶段：计算所有目标路径
        progress = ProgressBar(len(self.scan_files()), desc='分析文件')
        results = self.process(mode='execute', progress=progress)
        # process() 已经 close 了 progress，但我们还需要第二阶段进度

        # 第二阶段：执行重命名/复制
        exec_pb = ProgressBar(len(results), desc='执行重命名', disable=progress.disable)
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
        fieldnames = ['original', 'new_name', 'date', 'source', 'status']
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(self.results)


# ╔══════════════════════════════════════════════════════════╗
# ║              CLI 入口                                    ║
# ╚══════════════════════════════════════════════════════════╝

def resolve_format(fmt_arg: str) -> str:
    """解析格式参数：预设名 / 预设值 / 自定义 Python 日期格式"""
    # 先看是否是预设名
    if fmt_arg in FORMAT_PRESETS:
        return FORMAT_PRESETS[fmt_arg]
    # 再看是否是预设值（短路径匹配）
    for preset_name, preset_fmt in FORMAT_PRESETS.items():
        if fmt_arg == preset_fmt:
            return preset_fmt
    # 当作自定义格式
    return fmt_arg


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
            writer.writerows(csv_rows)
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
                writer.writerows(results)
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
                writer.writerows(results)
            print(f'  日志已保存: {csv_path}')


def main():
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

    parser.add_argument('-s', '--source', required=True, help='源文件夹路径')
    parser.add_argument('-m', '--mode', choices=['preview', 'execute'], default='preview',
                        help='模式: preview=模拟预览(不改文件), execute=真正重命名')
    parser.add_argument('-r', '--recursive', action='store_true', help='递归处理子目录')
    parser.add_argument('-f', '--format', default='默认',
                        help=f'输出格式。预设: {", ".join(FORMAT_PRESETS.keys())}；或自定义 Python 日期格式')
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

    args = parser.parse_args()

    # ── 生成默认配置（不需要源目录） ──────────────────
    if args.generate_config:
        config_path = Path(args.pattern_config) if args.pattern_config else None
        generate_default_config(config_path)
        print('请编辑该文件后重新运行程序即可。')
        sys.exit(0)

    # ── 设置自定义模式配置路径 ───────────────────────
    if args.pattern_config:
        set_pattern_config_path(args.pattern_config)

    # ── 提前初始化 DateExtractor（触发模式加载） ─────
    DateExtractor._ensure_initialized()

    # 验证源路径
    source_dir = Path(args.source)
    if not source_dir.is_dir():
        print(f'[ERROR] 源文件夹不存在: {args.source}')
        sys.exit(1)

    # 解析格式
    date_fmt = resolve_format(args.format)
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
    print('  Photo & Video Renamer v2.4')
    print('=' * 60)
    print(f'  源文件夹:   {source_dir}')
    print(f'  模式:       {"🔍 预览（不修改文件）" if args.mode == "preview" else "⚡ 执行重命名"}')
    print(f'  子目录:     {"是" if args.recursive else "否"}')
    print(f'  日期格式:   {date_fmt}')
    print(f'  输出方式:   {"复制到: " + output_dir if output_dir else "原地重命名"}')
    print(f'  扩展名:     {", ".join(sorted(exts)) if exts else "全部支持格式"}')
    print(f'  I/O 超时:   {NETWORK_TIMEOUT}s（可通过环境变量 PHOTO_RENAMER_TIMEOUT 调整）')
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
        sys.exit(0)

    # ── 智能发现模式 ─────────────────────────────────
    if args.discover:
        _run_discover_mode(source_dir, files, date_fmt, args.csv)
        sys.exit(0)

    # ── 副本整理模式 ─────────────────────────────────
    if args.dedup:
        _run_dedup_mode(source_dir, files, date_fmt, args.mode, args.csv)
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


if __name__ == '__main__':
    main()
