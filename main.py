#!/usr/bin/env python3
"""
程序入口：整理输入（选择轨迹文件、解析轨迹、匹配视频），
并调度 video_watermark 模块为视频批量添加轨迹水印。
"""

import argparse
import multiprocessing
import os

from track_parser import parse_track_file
from video_meta_parser import extract_creation_time_from_metadata
from video_watermark import process_video


def select_track_file(input_dir, preferred_exts=('.kml', '.gpx')):
    """在输入目录中查找轨迹文件。

    - 没有找到时返回 None
    - 只有一个时直接返回该文件
    - 有多个时列出并让用户输入数字选择
    """
    if not os.path.isdir(input_dir):
        return None

    # 按扩展名优先级收集所有轨迹文件
    track_files = []
    for f in sorted(os.listdir(input_dir)):
        if f.lower().endswith(preferred_exts):
            track_files.append(os.path.join(input_dir, f))

    if not track_files:
        return None

    if len(track_files) == 1:
        return track_files[0]

    # 多个轨迹文件，列出并让用户选择
    print("检测到多个轨迹文件，请选择要使用的轨迹文件：")
    for i, f in enumerate(track_files, start=1):
        print(f"  {i}. {os.path.basename(f)}")

    while True:
        choice = input(f"请输入序号 (1-{len(track_files)}): ").strip()
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(track_files):
                return track_files[idx - 1]
        print("输入无效，请重新输入。")


def main(input_dir='input', output_dir='output', workers=None):
    # 并发度默认使用 CPU 核心数
    if workers is None:
        workers = multiprocessing.cpu_count()
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 查找轨迹文件（支持 .kml / .gpx），多个时由用户选择
    track_file = select_track_file(input_dir)
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
        pool_args.append((input_video, output_video, track_data))
    
    # 使用多进程处理视频
    print(f"开始并发处理 {len(video_files)} 个视频（并发度: {workers}）")
    with multiprocessing.Pool(processes=workers) as pool:
        results = pool.map(process_video, pool_args)
    
    # 打印处理结果
    print("\n处理结果:")
    for result in results:
        print(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="为户外视频批量添加轨迹水印"
    )
    parser.add_argument(
        "-i", "--input-dir", default="input",
        help="输入文件夹名字（包含轨迹文件和 MP4 视频），默认: input",
    )
    parser.add_argument(
        "-o", "--output-dir", default="output",
        help="输出文件夹名字，默认: output",
    )
    parser.add_argument(
        "-w", "--workers", type=int, default=multiprocessing.cpu_count(),
        help="并发处理视频的并发度，默认: CPU 核心数 (%d)" % multiprocessing.cpu_count(),
    )
    args = parser.parse_args()
    main(args.input_dir, args.output_dir, args.workers)
