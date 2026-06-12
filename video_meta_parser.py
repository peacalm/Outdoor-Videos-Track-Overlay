#!/usr/bin/env python3
"""
视频元数据解析模块

对外接口:
  - extract_creation_time(video_file) -> datetime | None
      按顺序尝试四种方法提取视频拍摄/创建时间:
      1. ffprobe
      2. 文件系统时间戳（转换为 UTC，假定原时间为北京时间 UTC+8）
      3. moviepy
      4. mutagen

  - extract_creation_location(video_file) -> (lon, lat) | None
      使用 ffprobe 从视频元数据中提取 GPS 位置信息。

  - find_closest_track_point_index(video_time, track_points, track_times) -> int
      找到与给定视频时间最接近的轨迹点索引。

所有 print 日志均保留，方便排查问题；返回值与原 trail_marker.py
中的同名函数完全一致，便于平滑替换。
"""

import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
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

def _try_ffprobe_time(video_file):
    """方法 1：使用 ffprobe 读取元数据"""
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
    """方法 2：使用文件系统时间戳（假定为北京时间，转 UTC）"""
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
    """方法 3：使用 moviepy 读取元数据（可选依赖）"""
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
    """方法 4：使用 mutagen 读取 MP4 元数据（可选依赖）"""
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


def extract_creation_time(video_file) -> Optional[datetime]:
    """按顺序尝试四种方法，返回视频创建时间（datetime）或 None"""
    print(f"尝试从视频元数据中提取创建时间: {video_file}")

    creation_time = _try_ffprobe_time(video_file)
    if creation_time:
        print(f"✓ 使用ffprobe获取到创建时间: {creation_time}")
        return creation_time

    creation_time = _try_filesystem_time(video_file)
    if creation_time:
        print(f"✓ 使用文件系统时间: {creation_time}")
        return creation_time

    creation_time = _try_moviepy_time(video_file)
    if creation_time:
        print(f"✓ 使用moviepy获取到创建时间: {creation_time}")
        return creation_time

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


# 保留与原 trail_marker.py 同名函数，便于向后兼容
extract_creation_location_from_metadata = extract_creation_location


# ---------------------------------------------------------------------------
# 最接近轨迹点匹配
# ---------------------------------------------------------------------------

def find_closest_track_point_index(video_time, track_points, track_times):
    """找到与 video_time 最接近的轨迹点索引"""
    if not track_times or not track_points:
        raise ValueError("无法获取轨迹时间信息")

    min_diff = float('inf')
    closest_idx = 0

    for i, track_time in enumerate(track_times):
        diff = abs((video_time - track_time).total_seconds())
        if diff < min_diff:
            min_diff = diff
            closest_idx = i

    return closest_idx


# 保留与原 trail_marker.py 同名函数，便于向后兼容
def find_closest_track_point_by_time(video_time, track_points, track_times):
    return find_closest_track_point_index(
        video_time, track_points, track_times,
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python video_meta_parser.py <video_file>")
        sys.exit(1)
    path = sys.argv[1]
    t = extract_creation_time(path)
    loc = extract_creation_location(path)
    print(f"创建时间: {t}")
    print(f"位置:     {loc}")
