#!/usr/bin/env python3
import json
import math
import multiprocessing
import os
import re
import subprocess
from datetime import datetime, timedelta

import cv2
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from track_parser import parse_track_file, find_track_file

# CHINESE_FONT_PATH = "/System/Library/Fonts/Hiragino Sans GB.ttc"
# CHINESE_FONT_PATH = "/System/Library/Fonts/STHeiti Light.ttc"
# CHINESE_FONT_PATH = "/System/Library/Fonts/STHeiti Medium.ttc"
# CHINESE_FONT_PATH = "/System/Library/Fonts/PingFang Bold.ttc"
CHINESE_FONT_PATH = "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"




# 实现一个输入elevations列表，画海拔高度曲线图的函数
def plot_elevation_profile(elevations, elevations2=None):
    plt.plot(elevations)
    if elevations2 is not None:
        plt.plot(elevations2)
        plt.legend(["Filtered", "Original"])
    plt.xlabel("Sample Index")
    plt.ylabel("Elevation (m)")
    plt.title("Elevation Profile")
    plt.show()

# 创建output目录
output_dir = 'output'
if not os.path.exists(output_dir):
    os.makedirs(output_dir)


# 计算两点之间的距离（使用Haversine公式）
def calculate_distance(lat1, lon1, lat2, lon2):
    # 地球半径（单位：公里）
    R = 6371.0
    
    # 转换为弧度
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    
    # 差值
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    
    # Haversine公式
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    # 距离（单位：公里）
    distance = R * c
    return distance


# 计算轨迹的边界，用于坐标映射
def calculate_bounds(points):
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    min_lon = min(lons)
    max_lon = max(lons)
    min_lat = min(lats)
    max_lat = max(lats)
    return min_lon, max_lon, min_lat, max_lat


# 将地理坐标映射到屏幕坐标
def map_coordinate(lon, lat, min_lon, max_lon, min_lat, max_lat, width, height, padding=20, y_scale=1.1):
    # 计算经纬度范围
    lon_range = max_lon - min_lon
    lat_range = max_lat - min_lat

    # 计算缩放比例，取较小值以确保整个轨迹都能显示
    scale = min((width - 2*padding)/lon_range, (height - 2*padding)/lat_range)

    # 计算偏移量
    offset_x = padding + (width - 2*padding - lon_range*scale)/2
    offset_y = padding + (height - 2*padding - lat_range*scale)/2

    # 计算屏幕坐标（注意y轴方向相反），y方向应用额外的缩放
    x = offset_x + (lon - min_lon) * scale
    y = offset_y + (max_lat - lat) * scale * y_scale

    return int(x), int(y)


# 从视频文件元数据中提取创建时间
def extract_creation_time_from_metadata(video_file):
    # 方法1：使用ffprobe获取详细元数据（尝试不同的参数）
    def try_ffprobe():
        try:
            # 尝试不同的ffprobe命令参数组合
            cmd_variations = [
                ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', video_file],
                ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', video_file],
                ['ffprobe', '-v', 'quiet', '-print_format', 'xml', '-show_format', '-show_streams', video_file]
            ]
            
            for cmd in cmd_variations:
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                    
                    # 检查是否成功执行
                    if result.returncode != 0:
                        print(f"ffprobe命令执行失败，返回码: {result.returncode}")
                        continue
                    
                    # 尝试解析JSON输出
                    try:
                        metadata = json.loads(result.stdout)
                        
                        # 尝试从format tags获取
                        if 'format' in metadata and 'tags' in metadata['format']:
                            tags = metadata['format']['tags']
                            # 尝试不同的创建时间字段名
                            creation_time_fields = [
                                'creation_time', 'date', 'datetime', 'creationdate',
                                'Creation Time', 'Date', 'DateTime', 'CreationDate',
                                'com.apple.quicktime.creationdate', 'com.apple.quicktime.modificationdate',
                                'encoding_time', 'Encoded date', 'Tagged date'
                            ]
                            
                            for field in creation_time_fields:
                                if field in tags:
                                    time_str = tags[field]
                                    # 尝试解析不同格式的时间字符串
                                    formats = [
                                        "%Y-%m-%dT%H:%M:%S.%fZ",
                                        "%Y-%m-%dT%H:%M:%SZ",
                                        "%Y-%m-%d %H:%M:%S",
                                        "%Y/%m/%d %H:%M:%S",
                                        "%Y-%m-%d %H:%M:%S.%f",
                                        "%Y/%m/%d %H:%M:%S.%f",
                                        "%a %b %d %H:%M:%S %Y",
                                        "UTC %Y-%m-%d %H:%M:%S"
                                    ]
                                    
                                    for fmt in formats:
                                        try:
                                            dt = datetime.strptime(time_str, fmt)
                                            return dt
                                        except Exception:
                                            continue
                        
                        # 尝试从streams tags获取
                        if 'streams' in metadata:
                            for stream in metadata['streams']:
                                if 'tags' in stream:
                                    tags = stream['tags']
                                    for field in creation_time_fields:
                                        if field in tags:
                                            time_str = tags[field]
                                            for fmt in formats:
                                                try:
                                                    dt = datetime.strptime(time_str, fmt)
                                                    return dt
                                                except Exception:
                                                    continue
                    except json.JSONDecodeError:
                        print("ffprobe返回的不是有效的JSON格式")
                        # 尝试解析XML格式
                        try:
                            import xml.etree.ElementTree as ET
                            root = ET.fromstring(result.stdout)
                            # 查找creation_time标签
                            for elem in root.iter():
                                if 'creation_time' in elem.tag or 'date' in elem.tag.lower():
                                    time_str = elem.text
                                    if time_str:
                                        formats = [
                                            "%Y-%m-%dT%H:%M:%S.%fZ",
                                            "%Y-%m-%dT%H:%M:%SZ",
                                            "%Y-%m-%d %H:%M:%S"
                                        ]
                                        for fmt in formats:
                                            try:
                                                dt = datetime.strptime(time_str, fmt)
                                                return dt
                                            except Exception:
                                                continue
                        except Exception as e:
                            print(f"解析XML失败: {e}")
                except Exception as e:
                    print(f"执行命令失败: {e}")
                    continue
        except Exception as e:
            print(f"ffprobe执行失败: {e}")
        return None
    

    # 方法2：使用文件系统的文件创建时间
    def try_file_system():
        try:
            # 获取文件的创建时间
            stat_info = os.stat(video_file)
            
            # 尝试不同的时间戳
            time_fields = [
                ('st_ctime', '创建时间'),
                ('st_mtime', '修改时间'),
                ('st_birthtime', ' birth时间')
            ]
            
            for field_name, field_desc in time_fields:
                try:
                    if hasattr(stat_info, field_name):
                        timestamp = getattr(stat_info, field_name)
                        dt = datetime.fromtimestamp(timestamp)
                        print(f"使用文件系统{field_desc}: {dt}")
                        # 转换为UTC时间（假设是北京时间，UTC+8）
                        # 处理小时可能为负数的情况
                        new_hour = dt.hour - 8
                        if new_hour < 0:
                            dt_utc = dt.replace(day=dt.day-1, hour=new_hour+24)
                        else:
                            dt_utc = dt.replace(hour=new_hour)
                        print(f"转换为UTC时间: {dt_utc}")
                        return dt_utc
                except Exception as e:
                    print(f"获取{field_desc}失败: {e}")
            
            print(f"无法从文件系统获取时间信息: {video_file}")
            return None
        except Exception as e:
            print(f"文件系统操作失败: {e}")
            return None
    
    # 方法3：尝试使用Python的moviepy库（如果可用）
    def try_moviepy():
        try:
            from moviepy.editor import VideoFileClip
            clip = VideoFileClip(video_file)
            # 尝试获取元数据
            if hasattr(clip, 'metadata') and clip.metadata:
                metadata = clip.metadata
                print(f"moviepy获取到元数据: {metadata}")
                # 尝试从元数据中提取时间
                for key, value in metadata.items():
                    if 'date' in key.lower() or 'time' in key.lower():
                        time_str = str(value)
                        formats = [
                            "%Y-%m-%dT%H:%M:%S.%fZ",
                            "%Y-%m-%dT%H:%M:%SZ",
                            "%Y-%m-%d %H:%M:%S",
                            "%Y/%m/%d %H:%M:%S"
                        ]
                        for fmt in formats:
                            try:
                                dt = datetime.strptime(time_str, fmt)
                                return dt
                            except Exception:
                                continue
            clip.close()
            print(f"moviepy未能获取创建时间: {video_file}")
            return None
        except Exception as e:
            print(f"moviepy执行失败: {e}")
            return None
    
    # 方法4：使用mutagen库读取MP4元数据
    def try_mutagen():
        try:
            from mutagen.mp4 import MP4
            video = MP4(video_file)
            # 常见存储录制时间的键：'\xa9day' 或 'creation_date'
            if '\xa9day' in video:
                # 格式可能是 "2010-02-04T07:22:28Z" 或 "2010-02-04 07:22:28"
                date_str = video['\xa9day'][0]
                print(f"mutagen获取到\xa9day: {date_str}")
            elif 'creation_date' in video:
                date_str = video['creation_date'][0]
                print(f"mutagen获取到creation_date: {date_str}")
            else:
                print(f"mutagen未找到时间字段: {video.keys()}")
                return None
            
            # 解析日期字符串（兼容多种格式）
            formats = [
                "%Y-%m-%dT%H:%M:%SZ", 
                "%Y-%m-%d %H:%M:%S", 
                "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ]
            for fmt in formats:
                try:
                    dt = datetime.strptime(date_str, fmt)
                    # 转换为UTC时间（如果需要）
                    return dt
                except ValueError:
                    continue
            print(f"mutagen无法解析时间格式: {date_str}")
            return None
        except Exception as e:
            print(f"mutagen执行失败: {e}")
            return None
    
    # 按顺序尝试不同的方法
    print(f"\n尝试从视频元数据中提取创建时间: {video_file}")
    
    # 方法1：ffprobe
    creation_time = try_ffprobe()
    if creation_time:
        print(f"✓ 使用ffprobe获取到创建时间: {creation_time}")
        return creation_time
    
    # 方法2：文件系统时间
    creation_time = try_file_system()
    if creation_time:
        print(f"✓ 使用文件系统时间: {creation_time}")
        return creation_time
    
    # 方法3：moviepy
    creation_time = try_moviepy()
    if creation_time:
        print(f"✓ 使用moviepy获取到创建时间: {creation_time}")
        return creation_time
    
    # 方法4：mutagen
    creation_time = try_mutagen()
    if creation_time:
        print(f"✓ 使用mutagen获取到创建时间: {creation_time}")
        return creation_time
    
    # 所有方法都失败
    print(f"✗ 无法从视频元数据中获取创建时间: {video_file}")
    return None



# 从视频文件元数据中提取创建位置
def extract_creation_location_from_metadata(video_file):
    """使用ffprobe获取视频拍摄地点"""
    # 构建ffprobe命令，以JSON格式输出格式信息
    cmd = [
        'ffprobe', '-v', 'quiet', '-print_format', 'json',
        '-show_format', video_file
    ]

    try:
        # 执行命令并捕获输出
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        # 从format.tags中提取location
        tags = data['format'].get('tags', {})
        # print("视频元数据标签:")
        # for key, value in tags.items():
        #     print(f"  {key}: {value}")
        
        location_str = tags.get('location')
        
        if location_str:
            print(f"找到location字段: {location_str}")
            # 处理location字段格式
            location_str = location_str.strip("/")
            location_str = location_str.strip("+")
            lat, lon = location_str.split("+")
            location = (float(lon), float(lat))  # 注意顺序：(lon, lat)
            print(f"✓ 使用ffprobe获取到创建位置: {location}")
            return location
        
        # 尝试从其他GPS相关字段获取
        if 'gps_latitude' in tags and 'gps_longitude' in tags:
            lat = tags['gps_latitude']
            lon = tags['gps_longitude']
            try:
                # 处理度分秒格式
                if isinstance(lat, str) and isinstance(lon, str):
                    # 解析度分秒格式
                    def parse_dms(dms_str):
                        parts = dms_str.split()
                        degrees = float(parts[0])
                        minutes = float(parts[1])
                        seconds = float(parts[2])
                        direction = parts[3]
                        decimal = degrees + minutes/60 + seconds/3600
                        if direction in ['S', 'W']:
                            decimal = -decimal
                        return decimal
                    
                    lat_decimal = parse_dms(lat)
                    lon_decimal = parse_dms(lon)
                    location = (lon_decimal, lat_decimal)
                    print(f"✓ 使用ffprobe获取到创建位置: {location}")
                    return location
                # 处理十进制格式
                else:
                    lat_decimal = float(lat)
                    lon_decimal = float(lon)
                    location = (lon_decimal, lat_decimal)
                    print(f"✓ 使用ffprobe获取到创建位置: {location}")
                    return location
            except Exception as e:
                print(f"解析GPS坐标失败: {e}")
        
        # 尝试解析ISO6709格式
        if 'com.apple.quicktime.location.ISO6709' in tags:
            iso6709 = tags['com.apple.quicktime.location.ISO6709']
            # 格式示例: +37.7749-122.4194/
            try:
                # 提取经纬度
                match = re.match(r'([+-]?\d+\.\d+)([+-]?\d+\.\d+)/', iso6709)
                if match:
                    lat = float(match.group(1))
                    lon = float(match.group(2))
                    location = (lon, lat)
                    print(f"✓ 使用ffprobe获取到创建位置: {location}")
                    return location
            except Exception as e:
                print(f"解析ISO6709格式失败: {e}")
        
        # 所有方法都失败
        print(f"✗ 无法从视频元数据中获取创建位置: {video_file}")
        return None

    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"处理文件 {video_file} 时出错: {e}")
        return None



# 找到最接近视频拍摄时间的轨迹点
def find_closest_track_point_by_time(video_time, track_points, track_times):
    if not track_times or not track_points:
        # 不再使用轨迹中心点，返回失败
        raise ValueError("无法获取轨迹时间信息")
    
    # 计算时间差，找到最接近的点
    min_diff = float('inf')
    closest_idx = 0
    
    for i, track_time in enumerate(track_times):
        diff = abs((video_time - track_time).total_seconds())
        if diff < min_diff:
            min_diff = diff
            closest_idx = i
    
    return closest_idx


# 为视频添加轨迹水印
def add_trajectory_watermark(input_video, output_video, track_points, track_times):
    # 打开视频
    cap = cv2.VideoCapture(input_video)
    if not cap.isOpened():
        print(f"无法打开视频: {input_video}")
        return False
    
    # 获取视频属性
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    
    # 定义编解码器并创建输出视频写入器
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_video, fourcc, fps, (width, height))
    
    # 计算轨迹边界
    if not track_points:
        print("轨迹点为空")
        return False
    
    # 轨迹形状水印宽度（固定）
    watermark_width = 450

    min_lon, max_lon, min_lat, max_lat = calculate_bounds(track_points)
    
    # 计算轨迹的长宽比（使用屏幕坐标）
    # 首先将所有轨迹点转换为屏幕坐标
    screen_points = []
    for point in [[min_lon, min_lat, 0], [max_lon, max_lat, 0], [min_lon, max_lat, 0], [max_lon, min_lat, 0]]:
        lon, lat, _ = point
        x, y = map_coordinate(lon, lat, min_lon, max_lon, min_lat, max_lat, watermark_width, watermark_width)  # 暂时使用宽度作为高度进行计算
        screen_points.append((x, y))
    
    # 计算屏幕坐标的范围
    if screen_points:
        screen_xs = [p[0] for p in screen_points]
        screen_ys = [p[1] for p in screen_points]
        screen_width = max(screen_xs) - min(screen_xs)
        screen_height = max(screen_ys) - min(screen_ys)
        aspect_ratio = screen_width / screen_height if screen_height > 0 else 1.0
    else:
        aspect_ratio = 1.0
    
    right_border_width = 60
    
    # 时间水印位置和大小（底部右下角）
    time_watermark_height = 80
    time_watermark_width = watermark_width  
    time_watermark_x = width - time_watermark_width - right_border_width 
    time_watermark_y = height - time_watermark_height - 20  # 底边界

    # 里程爬升等信息水印位置和大小（底部右下角）
    info_watermark_height = 40 
    info_watermark_width = watermark_width + 100 # 里程爬升等信息水印宽度 加大一点
    info_watermark_x = width - info_watermark_width - right_border_width 
    info_watermark_y = time_watermark_y - info_watermark_height  # 时间水印上面
    
    # 海拔高度曲线水印大小和位置（右下角）
    elevation_watermark_width = watermark_width
    elevation_watermark_height = 150
    elevation_watermark_x = width - elevation_watermark_width - right_border_width
    elevation_watermark_y = info_watermark_y - elevation_watermark_height - 5
    

    # 轨迹形状水印高度（根据长宽比自适应计算）
    # 根据轨迹的长宽比自适应计算水印高度
    watermark_height = int(watermark_width / aspect_ratio)
    if watermark_height > 400:
        print(f"水印高度超过400，原始高度: {watermark_height}")
    elif watermark_height < 200:
        print(f"水印高度低于200，原始高度: {watermark_height}")



    watermark_x = width - watermark_width - right_border_width  # 右边界，与时间水印对齐
    watermark_y = elevation_watermark_y - watermark_height - 5  # 轨迹水印在时间水印上方
    
    # 从视频元数据中提取拍摄时间
    video_time = extract_creation_time_from_metadata(input_video)
    
    # 找到最接近的轨迹点
    print(f"视频时间: {video_time}")
    print(f"轨迹时间点数量: {len(track_times)}")
    
    # 从视频元数据中提取拍摄位置

    video_position_idx_by_time = find_closest_track_point_by_time(video_time, track_points, track_times)
    print(f"时间匹配索引: {video_position_idx_by_time}")
    video_position_idx = video_position_idx_by_time
    
    video_lon, video_lat, elevation = track_points[video_position_idx]
    print(f"视频位置: ({video_lon}, {video_lat})")
    
    # 处理每一帧
    for frame_idx in range(total_frames):
        ret, frame = cap.read()
        if not ret:
            break
        
        # 创建水印图层
        watermark = np.zeros((watermark_height, watermark_width, 4), dtype=np.uint8)
        watermark[:,:,3] = 0  # 初始全透明
        
        # 创建海拔高度曲线水印图层
        elevation_watermark = np.zeros((elevation_watermark_height, elevation_watermark_width, 4), dtype=np.uint8)
        elevation_watermark[:,:,3] = 0  # 初始全透明
        
        # 绘制完整轨迹（固定不动）
        for i in range(len(track_points) - 1):
            # 映射坐标到水印空间，y_scale=1.5放大高度方向比例
            x1, y1 = map_coordinate(
                track_points[i][0], track_points[i][1],
                min_lon, max_lon, min_lat, max_lat,
                watermark_width, watermark_height
            )
            x2, y2 = map_coordinate(
                track_points[i+1][0], track_points[i+1][1],
                min_lon, max_lon, min_lat, max_lat,
                watermark_width, watermark_height
            )
            
            # 绘制轨迹线条（先画白色背景，再画绿色线条）
            # 绘制白色背景线条（更粗）
            cv2.line(watermark, (x1, y1), (x2, y2), (255, 255, 255, 200), 6, lineType=cv2.LINE_AA)
        
        for i in range(len(track_points) - 1):
            # 映射坐标到水印空间，y_scale放大高度方向比例
            x1, y1 = map_coordinate(
                track_points[i][0], track_points[i][1],
                min_lon, max_lon, min_lat, max_lat,
                watermark_width, watermark_height
            )
            x2, y2 = map_coordinate(
                track_points[i+1][0], track_points[i+1][1],
                min_lon, max_lon, min_lat, max_lat,
                watermark_width, watermark_height
            )
            
            # 绘制绿色线条（在白色背景上）
            cv2.line(watermark, (x1, y1), (x2, y2), (0, 255, 0, 200), 3, lineType=cv2.LINE_AA)

        # 绘制视频拍摄位置标记，y_scale放大高度方向比例
        cx, cy = map_coordinate(
            video_lon, video_lat,
            min_lon, max_lon, min_lat, max_lat,
            watermark_width, watermark_height
        )
        # 绘制当前位置标记（红色，进一步增大大小）
        cv2.circle(watermark, (cx, cy), 8, (0, 0, 255, 200), -1)
        cv2.circle(watermark, (cx, cy), 3, (255, 255, 255, 200), -1)
        
        # 绘制海拔高度变化曲线
        # 提取所有轨迹点的海拔高度
        elevations = [p[2] for p in track_points]
        
        # 计算海拔高度的范围
        min_elev = min(elevations)
        max_elev = max(elevations)
        elev_range = max_elev - min_elev if max_elev > min_elev else 1
        
        # 绘制海拔高度曲线
        padding = 20
        curve_width = elevation_watermark_width - 2 * padding
        curve_height = elevation_watermark_height - 2 * padding
        
        # 绘制曲线背景
        cv2.rectangle(elevation_watermark, (padding, padding), (elevation_watermark_width - padding, elevation_watermark_height - padding), (255, 255, 255, 0), -1)
        
        # 绘制曲线（白色背景，蓝色线条）
        for i in range(len(elevations) - 1):
            # 计算当前点和下一个点的坐标
            x1 = padding + int((i / (len(elevations) - 1)) * curve_width)
            y1 = padding + int((max_elev - elevations[i]) / elev_range * curve_height)
            x2 = padding + int(((i + 1) / (len(elevations) - 1)) * curve_width)
            y2 = padding + int((max_elev - elevations[i + 1]) / elev_range * curve_height)            
            cv2.line(elevation_watermark, (x1, y1), (x2, y2), (255, 255, 255, 200), 6, lineType=cv2.LINE_AA)
        
        for i in range(len(elevations) - 1):
            # 计算当前点和下一个点的坐标
            x1 = padding + int((i / (len(elevations) - 1)) * curve_width)
            y1 = padding + int((max_elev - elevations[i]) / elev_range * curve_height)
            x2 = padding + int(((i + 1) / (len(elevations) - 1)) * curve_width)
            y2 = padding + int((max_elev - elevations[i + 1]) / elev_range * curve_height)
            cv2.line(elevation_watermark, (x1, y1), (x2, y2), (0, 255, 0, 200), 3, lineType=cv2.LINE_AA)
        
        # 标记当前位置在海拔曲线上的点
        assert(0 <= video_position_idx < len(elevations))
        current_x = padding + int((video_position_idx / (len(elevations) - 1)) * curve_width)
        current_y = padding + int((max_elev - elevations[video_position_idx]) / elev_range * curve_height)
        cv2.circle(elevation_watermark, (current_x, current_y), 8, (0, 0, 255, 200), -1)
        cv2.circle(elevation_watermark, (current_x, current_y), 3, (255, 255, 255, 200), -1)
        
        # 计算从轨迹起点到当前视频拍摄点的距离和累计爬升
        distance_km = 0.0
        elevation_gain = 0.0
        if video_position_idx and len(track_points) > 1:
            # 计算从起点到当前点的累计距离和累计爬升
            total_distance = 0.0
            for i in range(1, video_position_idx + 1):
                # 检查轨迹点是否包含海拔信息
                if len(track_points[i-1]) >= 3 and len(track_points[i]) >= 3:
                    lon1, lat1, alt1 = track_points[i-1]
                    lon2, lat2, alt2 = track_points[i]
                    # 使用Haversine公式计算两点之间的距离
                    distance = calculate_distance(lat1, lon1, lat2, lon2)  # 注意参数顺序
                    total_distance += distance
                    # 计算爬升（只累加正值）
                    elevation_diff = alt2 - alt1
                    if elevation_diff > 0:
                        elevation_gain += elevation_diff
                else:
                    # 如果没有海拔信息，只计算距离
                    lon1, lat1 = track_points[i-1]
                    lon2, lat2 = track_points[i]
                    distance = calculate_distance(lat1, lon1, lat2, lon2)  # 注意参数顺序
                    total_distance += distance
                    print("没有海拔高度信息")
            distance_km = total_distance  # 转换为公里
        
        # 创建时间水印
        time_watermark = np.zeros((time_watermark_height, time_watermark_width, 4), dtype=np.uint8)
        time_watermark[:,:,3] = 0

        # 创建信息水印
        info_watermark = np.zeros((info_watermark_height, info_watermark_width, 4), dtype=np.uint8)
        info_watermark[:,:,3] = 0

        def get_chinese_text_size(text, font_path, font_size, thickness=0):
            """
            使用 PIL 计算中文字符串的实际尺寸（考虑线条粗细）
            
            Args:
                text: 文本内容
                font_path: 字体文件路径
                font_size: 字体大小（像素）
                thickness: 线条粗细（像素），与 cv2.putText 的 thickness 参数对应
            
            Returns:
                (width, height): 文本实际占用的宽度和高度（包含边框外扩）
                baseline: 基线偏移（从顶部到基线的距离）
            """
            # 创建临时图像用于测量
            temp_img = Image.new('RGB', (1, 1))
            draw = ImageDraw.Draw(temp_img)
            
            # 加载中文字体
            font = ImageFont.truetype(font_path, font_size)
            
            # 获取文本边界框（无边框时的尺寸）
            bbox = draw.textbbox((0, 0), text, font=font)
            width = bbox[2] - bbox[0]
            height = bbox[3] - bbox[1]
            
            # 获取基线偏移
            ascent, descent = font.getmetrics()
            baseline = ascent
            
            # 考虑线条粗细对尺寸的影响
            # 线条会向四周扩展，宽度增加 2*thickness，高度增加 2*thickness
            if thickness > 0:
                width += thickness * 2
                height += thickness * 2
                baseline += thickness  # 基线也会向下偏移
            
            return (width, height), baseline
    

        def put_text_with_border(img, text, org, font, font_scale, text_color, border_color, thickness):
            """
            支持中文的带边框文字绘制函数
            org: 左下角坐标（与 cv2.putText 保持一致）
            """
            pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA))
            draw = ImageDraw.Draw(pil_img)
            font_size = int(font_scale * 30)
            try:
                pil_font = ImageFont.truetype(CHINESE_FONT_PATH, font_size)
            except:
                pil_font = ImageFont.truetype("/System/Library/Fonts/STHeiti Light.ttc", font_size)
            
            # 🔧 关键修正：将左下角坐标转换为左上角坐标
            x_bottom, y_bottom = org  # 左下角坐标（基线位置）
            
            # 获取文本尺寸，用于计算左上角坐标
            # 使用 getbbox() 获取文本边界框（返回 left, top, right, bottom）
            bbox = draw.textbbox((0, 0), text, font=pil_font)
            text_height = bbox[3] - bbox[1]  # 文本总高度（从顶部到底部）
            
            # 转换为左上角坐标
            # PIL 中，(x, y) 是文本左上角的位置
            # 原左下角坐标 (x_bottom, y_bottom) 对应文本的基线位置
            # 需要减去文本高度才能得到左上角坐标
            x_top = x_bottom
            y_top = y_bottom - text_height
            
            # 转换颜色格式
            border_color_rgba = (int(border_color[0]), int(border_color[1]), int(border_color[2]), 255)
            text_color_rgba = (int(text_color[0]), int(text_color[1]), int(text_color[2]), 255)
            
            # 绘制边框（使用转换后的左上角坐标）
            for dx in [-1, 0, 1]:
                for dy in [-1, 0, 1]:
                    if dx == 0 and dy == 0:
                        continue
                    draw.text((x_top + dx * thickness, y_top + dy * thickness), 
                            text, font=pil_font, fill=border_color_rgba)
            
            # 绘制主体文字
            draw.text((x_top, y_top), text, font=pil_font, fill=text_color_rgba)
            
            result = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGBA2BGRA)
            img[:] = result
        
        # 添加拍摄时间和距离信息（右下角）
        if video_time:
            # 从extract_creation_time_from_metadata函数获取的时间已经是UTC+8
            time_str = (video_time + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
            # 距离、爬升、海拔高度 - 合并成一行显示
            info_str = f"里程{distance_km:.1f}km 爬升{elevation_gain:.0f}m 海拔{elevation:.0f}m"

            # 字体大小
            font_scale = 0.8
            font_thickness = 2
            font_size = int(font_scale * 30)  # 0.8 * 30 = 24px

            # 计算文本宽度高度，使文字右对齐
            (info_width, info_height), _ = get_chinese_text_size(info_str, CHINESE_FONT_PATH, font_size, font_thickness)
            info_x = info_watermark_width - info_width - 10
            info_y = (info_watermark_height + info_height) // 2
            (time_width, time_height), _ = get_chinese_text_size(time_str, CHINESE_FONT_PATH, font_size, font_thickness)
            time_x = time_watermark_width - time_width - 10
            time_y = (time_watermark_height + time_height) // 2

        # 绘制信息文字（带白色边框）
        put_text_with_border(
            info_watermark, info_str, (info_x, info_y),
            cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0, 200), (255, 255, 255, 255), font_thickness
        )
        
        # 绘制时间文字（带白色边框）
        put_text_with_border(
            time_watermark, time_str, (time_x, time_y),
            cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0, 255), (255, 255, 255, 255), font_thickness
        )
        
        # 将轨迹水印叠加到帧上
        for c in range(0, 3):
            frame[watermark_y:watermark_y+watermark_height, watermark_x:watermark_x+watermark_width, c] = \
                frame[watermark_y:watermark_y+watermark_height, watermark_x:watermark_x+watermark_width, c] * \
                (1 - watermark[:,:,3]/255.0) + \
                watermark[:,:,c] * (watermark[:,:,3]/255.0)
        
        # 将信息水印叠加到帧上
        for c in range(0, 3):
            frame[info_watermark_y:info_watermark_y+info_watermark_height, info_watermark_x:info_watermark_x+info_watermark_width, c] = \
                frame[info_watermark_y:info_watermark_y+info_watermark_height, info_watermark_x:info_watermark_x+info_watermark_width, c] * \
                (1 - info_watermark[:,:,3]/255.0) + \
                info_watermark[:,:,c] * (info_watermark[:,:,3]/255.0)

        # 将时间水印叠加到帧上
        for c in range(0, 3):
            frame[time_watermark_y:time_watermark_y+time_watermark_height, time_watermark_x:time_watermark_x+time_watermark_width, c] = \
                frame[time_watermark_y:time_watermark_y+time_watermark_height, time_watermark_x:time_watermark_x+time_watermark_width, c] * \
                (1 - time_watermark[:,:,3]/255.0) + \
                time_watermark[:,:,c] * (time_watermark[:,:,3]/255.0)
        
        # 将海拔高度曲线水印叠加到帧上
        for c in range(0, 3):
            frame[elevation_watermark_y:elevation_watermark_y+elevation_watermark_height, elevation_watermark_x:elevation_watermark_x+elevation_watermark_width, c] = \
                frame[elevation_watermark_y:elevation_watermark_y+elevation_watermark_height, elevation_watermark_x:elevation_watermark_x+elevation_watermark_width, c] * \
                (1 - elevation_watermark[:,:,3]/255.0) + \
                elevation_watermark[:,:,c] * (elevation_watermark[:,:,3]/255.0)
        
        # 写入输出视频
        out.write(frame)
    
    # 释放资源
    cap.release()
    out.release()
    
    # 保留原视频的音频
    print("正在合并音频...")
    try:
        # 创建临时文件
        temp_video = output_video + ".temp.mp4"
        os.rename(output_video, temp_video)
        
        # 使用ffmpeg合并原音频和处理后的视频
        cmd = [
            'ffmpeg', '-i', temp_video,
            '-i', input_video,
            '-c:v', 'copy', '-c:a', 'copy',
            '-map', '0:v:0', '-map', '1:a:0',
            '-shortest', output_video
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            print("音频合并成功")
            # 删除临时文件
            os.remove(temp_video)
        else:
            print(f"音频合并失败: {result.stderr}")
            # 恢复临时文件
            os.rename(temp_video, output_video)
    except Exception as e:
        print(f"处理音频时出错: {e}")
    
    print(f"处理完成: {output_video}")
    return True


def process_video(args):
    """处理单个视频的函数，用于多进程调用"""
    input_video, output_video, track_points, track_times = args
    video_file = os.path.basename(input_video)
    print(f"\n处理视频: {video_file}")
    
    try:
        add_trajectory_watermark(input_video, output_video, track_points, track_times)
        return f"成功处理: {video_file}"
    except Exception as e:
        print(f"处理视频 {video_file} 时出错: {e}")
        import traceback
        traceback.print_exc()
        return f"处理失败: {video_file}"


def main():
    # 输入输出目录
    input_dir = 'input'
    output_dir = 'output'
    
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 查找轨迹文件（支持 .kml / .gpx）
    track_file = find_track_file(input_dir)
    if not track_file:
        print("未找到轨迹文件（支持 .kml / .gpx）")
        return
    
    print(f"使用轨迹文件: {track_file}")
    
    # 解析轨迹文件获取轨迹点和时间（统一格式）
    track_data = parse_track_file(track_file)
    track_points = track_data["raw_points"]
    track_times = track_data["raw_times"]
    print(f"轨迹点数量: {len(track_points)}")
    print(f"轨迹时间点数量: {len(track_times)}")
    summary = track_data["summary"]
    print(f"总距离: {summary['total_distance_km']:.2f} km, "
          f"爬升: {summary['total_ascent_m']:.0f} m, "
          f"下降: {summary['total_descent_m']:.0f} m, "
          f"最高海拔: {summary['max_elevation_m']:.0f} m, "
          f"用时: {summary['total_duration_s']:.0f} s")
    if not track_points or not track_times:
        print("轨迹点或时间点为空")
        return
    
    # import pdb; pdb.set_trace()

    # 找到所有MP4视频文件
    video_files = [f for f in os.listdir(input_dir) if f.endswith('.mp4')]
    if not video_files:
        print("未找到MP4视频文件")
        return
    
    # 准备进程池参数
    pool_args = []
    for video_file in video_files:
        input_video = os.path.join(input_dir, video_file)
        # 获取视频创建时间并格式化为文件名前缀
        video_time = extract_creation_time_from_metadata(input_video)
        if video_time:
            time_prefix = video_time.strftime("%Y%m%d_%H%M%S")
            output_filename = f"{time_prefix}_{video_file}"
        else:
            output_filename = video_file
        output_video = os.path.join(output_dir, output_filename)
        pool_args.append((input_video, output_video, track_points, track_times))
    
    # 使用多进程处理视频
    print(f"开始并发处理 {len(video_files)} 个视频")
    with multiprocessing.Pool(processes=multiprocessing.cpu_count()) as pool:
        results = pool.map(process_video, pool_args)
    
    # 打印处理结果
    print("\n处理结果:")
    for result in results:
        print(result)

if __name__ == "__main__":
    main()
