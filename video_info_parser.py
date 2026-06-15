#!/usr/bin/env python3
"""
视频信息解析模块

对外接口:
  - extract_creation_time(video_file, filename_pattern=None) -> datetime | None
      提取视频拍摄/创建时间（UTC），按以下优先级依次尝试：
      1. 从文件名中提取（需提供 filename_pattern，将文件名视为北京时间并转 UTC）
      2. ffprobe 读取视频元数据
      3. 文件系统时间戳（将文件名视为北京时间并转 UTC）
      4. moviepy 读取视频元数据
      5. mutagen 读取 MP4 元数据

  - extract_creation_location(video_file) -> (lon, lat) | None
      使用 ffprobe 从视频元数据中提取 GPS 位置信息。

"""

import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional


# ---------------------------------------------------------------------------
# 内部辅助：时间字符串解析（兼容多种常见格式）
# ---------------------------------------------------------------------------

_TIME_FORMATS = [
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y/%m/%d %H:%M:%S.%f",
    "%a %b %d %H:%M:%S %Y",
    "UTC %Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
]

_TIME_FIELD_NAMES = [
    'creation_time', 'date', 'datetime', 'creationdate',
    'Creation Time', 'Date', 'DateTime', 'CreationDate',
    'com.apple.quicktime.creationdate', 'com.apple.quicktime.modificationdate',
    'encoding_time', 'Encoded date', 'Tagged date',
]


def _parse_datetime(time_str):
    """尝试多种格式解析时间字符串，失败返回 None"""
    if not time_str:
        return None
    time_str = str(time_str).strip()
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(time_str, fmt)
        except (ValueError, TypeError):
            continue
    return None


# ---------------------------------------------------------------------------
# 提取创建时间
# ---------------------------------------------------------------------------

def _try_filename_time(video_file, filename_pattern):
    """从文件名中按正则匹配提取时间，pattern 格式不包含扩展名，例如 'VID_%Y%m%d_%H%M%S'。
    会在文件名（不含扩展名）中搜索匹配部分，提取后视为北京时间，由调用方转换为 UTC。
    返回 naive datetime 对象或 None。"""
    # 将 strptime 格式的模式转换为正则表达式
    directive_to_regex = {
        '%Y': r'\d{4}', '%m': r'\d{2}', '%d': r'\d{2}',
        '%H': r'\d{2}', '%M': r'\d{2}', '%S': r'\d{2}',
        '%f': r'\d+', '%y': r'\d{2}', '%j': r'\d{3}',
        '%I': r'\d{2}', '%p': r'[APap][Mm]', '%Z': r'[A-Za-z]+',
    }
    regex_pattern = re.escape(filename_pattern)
    for directive, regex_repl in directive_to_regex.items():
        regex_pattern = regex_pattern.replace(re.escape(directive), regex_repl)

    video_file_basename_noext = os.path.splitext(os.path.basename(video_file))[0]
    match = re.search(regex_pattern, video_file_basename_noext)
    if not match:
        print(f"文件名中未匹配到时间模式: {video_file_basename_noext}")
        return None

    matched_str = match.group(0)
    try:
        return datetime.strptime(matched_str, filename_pattern)
    except ValueError as e:
        print(f"无法从文件名解析时间: {matched_str}, 错误信息: {e}")
        return None

def _try_ffprobe_time(video_file):
    """使用 ffprobe 读取视频元数据中的时间字段"""
    try:
        cmd_variations = [
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_format', '-show_streams', video_file],
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_format', video_file],
            ['ffprobe', '-v', 'quiet', '-print_format', 'xml',
             '-show_format', '-show_streams', video_file],
        ]

        for cmd in cmd_variations:
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=10,
                )
                if result.returncode != 0:
                    continue

                # JSON 路径
                try:
                    metadata = json.loads(result.stdout)

                    # format.tags
                    if 'format' in metadata and 'tags' in metadata['format']:
                        tags = metadata['format']['tags']
                        for field in _TIME_FIELD_NAMES:
                            if field in tags:
                                dt = _parse_datetime(tags[field])
                                if dt is not None:
                                    return dt

                    # streams.tags
                    if 'streams' in metadata:
                        for stream in metadata['streams']:
                            if 'tags' in stream:
                                tags = stream['tags']
                                for field in _TIME_FIELD_NAMES:
                                    if field in tags:
                                        dt = _parse_datetime(tags[field])
                                        if dt is not None:
                                            return dt
                except json.JSONDecodeError:
                    # XML 回退
                    try:
                        root = ET.fromstring(result.stdout)
                        for elem in root.iter():
                            tag_low = elem.tag.lower()
                            if 'creation_time' in tag_low or 'date' in tag_low:
                                dt = _parse_datetime(elem.text)
                                if dt is not None:
                                    return dt
                    except ET.ParseError:
                        continue
            except Exception:
                continue
    except Exception as e:
        print(f"ffprobe执行失败: {e}")
    return None


def _try_filesystem_time(video_file):
    """使用文件系统时间戳（假定为北京时间，转 UTC）"""
    try:
        stat_info = os.stat(video_file)

        for field_name, field_desc in [
            ('st_ctime', '创建时间'),
            ('st_mtime', '修改时间'),
            ('st_birthtime', ' birth时间'),
        ]:
            try:
                if hasattr(stat_info, field_name):
                    timestamp = getattr(stat_info, field_name)
                    dt = datetime.fromtimestamp(timestamp)
                    print(f"使用文件系统{field_desc}: {dt}")
                    # 北京时间 -> UTC
                    new_hour = dt.hour - 8
                    if new_hour < 0:
                        dt_utc = dt.replace(day=dt.day - 1, hour=new_hour + 24)
                    else:
                        dt_utc = dt.replace(hour=new_hour)
                    print(f"转换为UTC时间: {dt_utc}")
                    return dt_utc
            except Exception as e:
                print(f"获取{field_desc}失败: {e}")

        print(f"无法从文件系统获取时间信息: {video_file}")
    except Exception as e:
        print(f"文件系统操作失败: {e}")
    return None


def _try_moviepy_time(video_file):
    """使用 moviepy 读取视频元数据（可选依赖）"""
    try:
        from moviepy.editor import VideoFileClip
        clip = VideoFileClip(video_file)
        try:
            if hasattr(clip, 'metadata') and clip.metadata:
                metadata = clip.metadata
                print(f"moviepy获取到元数据: {metadata}")
                for key, value in metadata.items():
                    k = str(key).lower()
                    if 'date' in k or 'time' in k:
                        dt = _parse_datetime(str(value))
                        if dt is not None:
                            return dt
            print(f"moviepy未能获取创建时间: {video_file}")
        finally:
            clip.close()
    except Exception as e:
        print(f"moviepy执行失败: {e}")
    return None


def _try_mutagen_time(video_file):
    """使用 mutagen 读取 MP4 元数据（可选依赖）"""
    try:
        from mutagen.mp4 import MP4
        video = MP4(video_file)
        # 常见键：'\xa9day' 或 'creation_date'
        if '\xa9day' in video:
            date_str = video['\xa9day'][0]
            print(f"mutagen获取到\xa9day: {date_str}")
        elif 'creation_date' in video:
            date_str = video['creation_date'][0]
            print(f"mutagen获取到creation_date: {date_str}")
        else:
            print(f"mutagen未找到时间字段: {video.keys()}")
            return None

        dt = _parse_datetime(date_str)
        if dt is not None:
            return dt
        print(f"mutagen无法解析时间格式: {date_str}")
    except Exception as e:
        print(f"mutagen执行失败: {e}")
    return None


def extract_creation_time(video_file, filename_pattern=None) -> Optional[datetime]:
    """提取视频创建时间，返回 UTC 时间（datetime）或 None。

    提取策略按优先级依次为：
      1. 从文件名提取（需提供 filename_pattern，视为北京时间转 UTC）
      2. ffprobe 读取视频元数据
      3. 文件系统时间戳（视为北京时间转 UTC）
      4. moviepy 读取视频元数据
      5. mutagen 读取 MP4 元数据
    """

    print(f"尝试提取创建时间: {video_file}")

    # 方法 1：从文件名提取
    if filename_pattern:
        creation_time = _try_filename_time(video_file, filename_pattern)
        if creation_time:
            utc_creation_time = creation_time - timedelta(hours=8)
            print(f"✓ 从文件名提取到时间: {creation_time}, UTC时间: {utc_creation_time}")
            return utc_creation_time

    # 方法 2：ffprobe
    creation_time = _try_ffprobe_time(video_file)
    if creation_time:
        print(f"✓ 使用ffprobe获取到创建时间: {creation_time}")
        return creation_time

    # 方法 3：文件系统时间戳
    creation_time = _try_filesystem_time(video_file)
    if creation_time:
        print(f"✓ 使用文件系统时间: {creation_time}")
        return creation_time

    # 方法 4：moviepy
    creation_time = _try_moviepy_time(video_file)
    if creation_time:
        print(f"✓ 使用moviepy获取到创建时间: {creation_time}")
        return creation_time

    # 方法 5：mutagen
    creation_time = _try_mutagen_time(video_file)
    if creation_time:
        print(f"✓ 使用mutagen获取到创建时间: {creation_time}")
        return creation_time

    print(f"✗ 无法从视频元数据中获取创建时间: {video_file}")
    return None


# ---------------------------------------------------------------------------
# 提取拍摄位置（GPS）
# ---------------------------------------------------------------------------

def _parse_dms(dms_str):
    """解析度分秒字符串为十进制角度"""
    parts = dms_str.split()
    degrees = float(parts[0])
    minutes = float(parts[1])
    seconds = float(parts[2])
    direction = parts[3]
    decimal = degrees + minutes / 60 + seconds / 3600
    if direction in ('S', 'W'):
        decimal = -decimal
    return decimal


def extract_creation_location(video_file):
    """使用 ffprobe 从视频元数据中提取拍摄位置，返回 (lon, lat) 或 None"""
    cmd = [
        'ffprobe', '-v', 'quiet', '-print_format', 'json',
        '-show_format', video_file,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        tags = data['format'].get('tags', {})

        # 路径 1：location 字段
        location_str = tags.get('location')
        if location_str:
            print(f"找到location字段: {location_str}")
            location_str = location_str.strip("/").strip("+")
            lat, lon = location_str.split("+")
            location = (float(lon), float(lat))  # (lon, lat)
            print(f"✓ 使用ffprobe获取到创建位置: {location}")
            return location

        # 路径 2：gps_latitude / gps_longitude
        if 'gps_latitude' in tags and 'gps_longitude' in tags:
            lat = tags['gps_latitude']
            lon = tags['gps_longitude']
            try:
                if isinstance(lat, str) and isinstance(lon, str):
                    lat_decimal = _parse_dms(lat)
                    lon_decimal = _parse_dms(lon)
                else:
                    lat_decimal = float(lat)
                    lon_decimal = float(lon)
                location = (lon_decimal, lat_decimal)
                print(f"✓ 使用ffprobe获取到创建位置: {location}")
                return location
            except Exception as e:
                print(f"解析GPS坐标失败: {e}")

        # 路径 3：ISO 6709 格式（苹果设备常见）
        iso_key = 'com.apple.quicktime.location.ISO6709'
        if iso_key in tags:
            iso6709 = tags[iso_key]
            try:
                match = re.match(r'([+-]?\d+\.\d+)([+-]?\d+\.\d+)/', iso6709)
                if match:
                    lat = float(match.group(1))
                    lon = float(match.group(2))
                    location = (lon, lat)
                    print(f"✓ 使用ffprobe获取到创建位置: {location}")
                    return location
            except Exception as e:
                print(f"解析ISO6709格式失败: {e}")

        print(f"✗ 无法从视频元数据中获取创建位置: {video_file}")
        return None

    except (subprocess.CalledProcessError, json.JSONDecodeError,
            KeyError, ValueError) as e:
        print(f"处理文件 {video_file} 时出错: {e}")
        return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="视频信息解析工具")
    parser.add_argument("video_file", help="视频文件路径")
    parser.add_argument("-p", "--filename-pattern", default=None,
                        help="文件名时间模式（不含扩展名），例如'VID_%%Y%%m%%d_%%H%%M%%S'")
    args = parser.parse_args()

    t = extract_creation_time(args.video_file, args.filename_pattern)
    loc = extract_creation_location(args.video_file)
    print(f"创建时间: {t}")
    print(f"位置:     {loc}")
