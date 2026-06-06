#!/usr/bin/env python3
"""
视频轨迹水印模块

负责把轨迹形状、海拔曲线、里程/爬升/时间等信息绘制为水印并叠加到视频上。
所有轨迹相关数据均来自 track_parser.parse_track_file 的解析结果 track_data，
本模块不再做任何轨迹计算。

对外接口:
  - add_video_watermark(input_video, output_video, track_data)
      为单个视频添加轨迹水印，并保留原视频音频。
  - process_video(args)
      多进程封装，args 为 (input_video, output_video, track_data)。
"""

import os
import subprocess
from datetime import timedelta

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from video_meta_parser import (
    extract_creation_time_from_metadata,
    find_closest_track_point_by_time,
)

# CHINESE_FONT_PATH = "/System/Library/Fonts/Hiragino Sans GB.ttc"
# CHINESE_FONT_PATH = "/System/Library/Fonts/STHeiti Light.ttc"
CHINESE_FONT_PATH = "/System/Library/Fonts/STHeiti Medium.ttc"
# CHINESE_FONT_PATH = "/System/Library/Fonts/PingFang Bold.ttc"
# CHINESE_FONT_PATH = "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"


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


# 为视频添加轨迹水印
def add_video_watermark(input_video, output_video, track_data):
    # 从解析结果中取出所需数据（本模块不做任何轨迹计算）
    points = track_data["points"]          # 每个点的详细数据（含累计距离/爬升等）
    track_points = track_data["raw_points"]  # [(lon, lat, elevation), ...]
    track_times = track_data["raw_times"]    # [datetime, ...]

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
    time_watermark_height = 40
    time_watermark_width = watermark_width  
    time_watermark_x = width - time_watermark_width - right_border_width 
    time_watermark_y = height - time_watermark_height - 20  # 底边界

    # 里程爬升等信息水印位置和大小（底部右下角）
    info_watermark_height = 40 
    info_watermark_width = watermark_width + 100 # 里程爬升等信息水印宽度 加大一点
    info_watermark_x = width - info_watermark_width - right_border_width 
    info_watermark_y = time_watermark_y - info_watermark_height  # 时间水印上面

    # 详细运行信息水印（坡度/配速/累计用时），位于里程信息行上面
    info2_watermark_height = 40
    info2_watermark_width = info_watermark_width
    info2_watermark_x = info_watermark_x
    info2_watermark_y = info_watermark_y - info2_watermark_height

    # 海拔高度曲线水印大小和位置（右下角）
    elevation_watermark_width = watermark_width
    elevation_watermark_height = 150
    elevation_watermark_x = width - elevation_watermark_width - right_border_width
    elevation_watermark_y = info2_watermark_y - elevation_watermark_height - 5
    

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
        
        # 从解析结果中直接取出当前点的累计距离与累计爬升（无需重新计算）
        current_point = points[video_position_idx]
        distance_km = current_point["cumulative_distance_km"]
        elevation_gain = current_point["cumulative_ascent_m"]
        # 更详细的当前运行信息
        grade_degree = current_point["grade_degree"]              # 当前坡度（角度 °）
        pace_min_per_km = current_point["pace_min_per_km"]        # 当前配速 (min/km)
        cumulative_duration_s = current_point["cumulative_duration_s"]  # 累计用时（秒）

        # 创建时间水印
        time_watermark = np.zeros((time_watermark_height, time_watermark_width, 4), dtype=np.uint8)
        time_watermark[:,:,3] = 0

        # 创建信息水印
        info_watermark = np.zeros((info_watermark_height, info_watermark_width, 4), dtype=np.uint8)
        info_watermark[:,:,3] = 0

        # 创建详细运行信息水印（坡度/配速/累计用时）
        info2_watermark = np.zeros((info2_watermark_height, info2_watermark_width, 4), dtype=np.uint8)
        info2_watermark[:,:,3] = 0

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
            # 里程、爬升、海拔高度 - 显示在上面一行
            info2_str = f"里程{distance_km:.1f}km 爬升{elevation_gain:.0f}m 海拔{elevation:.0f}m"

            # 更详细的当前运行信息：坡度、配速、累计用时 - 显示在下面一行
            # 累计用时格式化为 H:MM:SS / MM:SS
            total_sec = int(round(cumulative_duration_s))
            h, rem = divmod(total_sec, 3600)
            m, s = divmod(rem, 60)
            if h > 0:
                duration_str = f"{h}:{m:02d}:{s:02d}"
            else:
                duration_str = f"{m:02d}:{s:02d}"
            # 配速格式化为 M'SS"（0 表示无有效数据）
            if pace_min_per_km > 0:
                pace_m = int(pace_min_per_km)
                pace_s = int(round((pace_min_per_km - pace_m) * 60))
                if pace_s == 60:
                    pace_m += 1
                    pace_s = 0
                pace_str = f"{pace_m}'{pace_s:02d}\""
            else:
                pace_str = "--'--\""
            info_str = f"坡度{grade_degree:.0f}° 配速{pace_str}/km"
            # 将用时信息加入时间行，并放在时间前面
            time_str = f"用时{duration_str} {time_str}"

            # 字体大小
            font_scale = 0.8
            font_thickness = 2
            font_size = int(font_scale * 30)  # 0.8 * 30 = 24px

            # 计算文本宽度高度，使文字右对齐
            (info_width, info_height), _ = get_chinese_text_size(info_str, CHINESE_FONT_PATH, font_size, font_thickness)
            info_x = info_watermark_width - info_width - 10
            info_y = (info_watermark_height + info_height) // 2
            (info2_width, info2_height), _ = get_chinese_text_size(info2_str, CHINESE_FONT_PATH, font_size, font_thickness)
            info2_x = info2_watermark_width - info2_width - 10
            info2_y = (info2_watermark_height + info2_height) // 2
            (time_width, time_height), _ = get_chinese_text_size(time_str, CHINESE_FONT_PATH, font_size, font_thickness)
            time_x = time_watermark_width - time_width - 10
            time_y = (time_watermark_height + time_height) // 2

        # 绘制详细运行信息文字（带白色边框）
        put_text_with_border(
            info2_watermark, info2_str, (info2_x, info2_y),
            cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0, 200), (255, 255, 255, 255), font_thickness
        )

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

        # 将详细运行信息水印叠加到帧上
        for c in range(0, 3):
            frame[info2_watermark_y:info2_watermark_y+info2_watermark_height, info2_watermark_x:info2_watermark_x+info2_watermark_width, c] = \
                frame[info2_watermark_y:info2_watermark_y+info2_watermark_height, info2_watermark_x:info2_watermark_x+info2_watermark_width, c] * \
                (1 - info2_watermark[:,:,3]/255.0) + \
                info2_watermark[:,:,c] * (info2_watermark[:,:,3]/255.0)

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
    input_video, output_video, track_data = args
    video_file = os.path.basename(input_video)
    print(f"\n处理视频: {video_file}")
    
    try:
        add_video_watermark(input_video, output_video, track_data)
        return f"成功处理: {video_file}"
    except Exception as e:
        print(f"处理视频 {video_file} 时出错: {e}")
        import traceback
        traceback.print_exc()
        return f"处理失败: {video_file}"
