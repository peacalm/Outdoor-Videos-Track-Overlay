#!/usr/bin/env python3
"""
程序入口：整理输入（选择轨迹文件、解析轨迹、匹配视频），
并调度 video_watermarker 模块为视频批量添加轨迹水印。
"""

import argparse
import multiprocessing
import os

from track_parser import parse_track_file
from video_info_parser import extract_creation_time
from video_watermarker import process_video, process_image, IMAGE_EXTS, is_motion_photo


def select_track_file(input_dir, preferred_exts=('.kml', '.gpx', '.fit')):
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


def main(input_dir='input', output_dir='output', workers=None, filename_patterns=None, track_file=None):
    # 并发度默认使用 CPU 核心数
    if workers is None:
        workers = multiprocessing.cpu_count()
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 若未显式指定轨迹文件，则从输入目录自动查找（支持 .kml / .gpx / .fit）
    if track_file is None:
        track_file = select_track_file(input_dir)
    if not track_file:
        print(f"未找到轨迹文件（支持 .kml / .gpx / .fit）, input_dir: {input_dir}")
        return
    
    print(f"使用轨迹文件: {track_file}")
    
    # 解析轨迹文件获取轨迹点和时间（统一格式）
    track_data = parse_track_file(track_file)
    track_points = track_data["raw_points"]
    track_times = track_data["raw_times"]
    print(f"轨迹点数量: {len(track_points)}")
    print(f"轨迹时间点数量: {len(track_times)}")

    summary = track_data["summary"]
    
    total_sec = int(round(summary["total_duration_s"]))
    h, rem = divmod(total_sec, 3600)
    m, s = divmod(rem, 60)
    duration_str = f"{h}h{m:02d}m{s:02d}s" if h > 0 else f"{m}m{s:02d}s"

    print(f"总距离: {summary['total_distance_km']:.2f} km, "
          f"爬升: {summary['total_ascent_m']:.0f} m, "
          f"下降: {summary['total_descent_m']:.0f} m, "
          f"最高海拔: {summary['max_elevation_m']:.0f} m, "
          f"用时: {duration_str} \n")
    if not track_points or not track_times:
        print("轨迹点或时间点为空")
        return

    # 找到所有 MP4 视频文件
    video_files = [f for f in os.listdir(input_dir) if f.lower().endswith('.mp4')]
    # 找到所有图片文件（静态图 / 动图 GIF）
    image_files = [f for f in os.listdir(input_dir) if f.lower().endswith(IMAGE_EXTS)]

    if not video_files and not image_files:
        print(f"未找到视频或图片文件, input_dir: {input_dir}")
        return

    # 准备视频进程池参数
    pool_args = []
    failed_videos = []
    for video_file in video_files:
        input_video = os.path.join(input_dir, video_file)
        # 获取视频创建时间并格式化为文件名前缀
        video_time = extract_creation_time(input_video, filename_patterns)
        if video_time:
            time_prefix = video_time.strftime("%Y%m%d_%H%M%S")
            output_filename = f"{time_prefix}_{video_file}"
        else:
            failed_videos.append(video_file)
            continue
        output_video = os.path.join(output_dir, output_filename)
        pool_args.append((input_video, video_time, output_video, track_data))

    if failed_videos:
        print(f"未成功提取 {len(failed_videos)} 个视频的创建时间:")
        for video_file in failed_videos:
            print(f"  {video_file}")

    # 使用多进程处理视频
    if pool_args:
        print(f"开始并发处理 {len(pool_args)} 个视频（并发度: {workers}）")
        with multiprocessing.Pool(processes=workers) as pool:
            video_results = pool.map(process_video, pool_args)

        print("\n视频处理结果:")
        for result in video_results:
            print(result)

    # 准备图片处理参数（静态图直接加水印，动图 GIF 转成视频）
    failed_images = []
    image_args = []
    for image_file in image_files:
        input_image = os.path.join(input_dir, image_file)
        image_time = extract_creation_time(input_image, filename_patterns)
        if not image_time:
            failed_images.append(image_file)
            continue
        time_prefix = image_time.strftime("%Y%m%d_%H%M%S")
        ext = os.path.splitext(image_file)[1].lower()
        if ext == '.gif' or is_motion_photo(input_image):
            output_filename = f"{time_prefix}_{os.path.splitext(image_file)[0]}.mp4"
        else:
            output_filename = f"{time_prefix}_{image_file}"
        output_image = os.path.join(output_dir, output_filename)
        image_args.append((input_image, image_time, output_image, track_data))

    if failed_images:
        print(f"未成功提取 {len(failed_images)} 个图片的创建时间:")
        for image_file in failed_images:
            print(f"  {image_file}")

    # 处理图片（单帧，串行处理即可）
    if image_args:
        print(f"开始处理 {len(image_args)} 个图片")
        image_results = [process_image(args) for args in image_args]

        print("\n图片处理结果:")
        for result in image_results:
            print(result)

    if not pool_args and not image_args:
        print("所有视频/图片创建时间提取失败，无法继续处理")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="为户外视频/图片批量添加轨迹水印"
    )
    parser.add_argument(
        "-i", "--input-dir", default="input",
        help="输入文件夹名字（包含轨迹文件和 MP4 视频/图片），默认: input",
    )
    parser.add_argument(
        "-o", "--output-dir", default="output",
        help="输出文件夹名字，默认: output",
    )
    parser.add_argument(
        "-w", "--workers", type=int, default=multiprocessing.cpu_count(),
        help="并发处理视频的并发度，默认: CPU 核心数 (%d)" % multiprocessing.cpu_count(),
    )
    default_patterns = ['VID_%Y%m%d_%H%M%S', 'IMG_%Y%m%d_%H%M%S', 'picture_%Y_%m_%d_%H_%M_%S']
    parser.add_argument(
        "-p", "--filename-patterns", nargs='+', default=default_patterns,
        help="文件名模式（可传多个），不含扩展名，用于从文件名中提取时间，默认: None。"
             "例如'VID_%%Y%%m%%d_%%H%%M%%S'，多个模式用空格分隔",
    )
    parser.add_argument(
        "-t", "--track-file", default=None,
        help="指定轨迹文件路径（.kml/.gpx/.fit），不指定则自动在输入目录查找",
    )
    args = parser.parse_args()
    main(args.input_dir, args.output_dir, args.workers, args.filename_patterns, args.track_file)
