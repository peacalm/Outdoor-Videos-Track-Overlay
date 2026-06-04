#!/usr/bin/env python3
"""
将多个视频按文件名顺序合并成一个视频。

用法:
    python3 merge_videos.py [-i 输入目录] [-o 输出文件] [--copy]

参数:
    -i / --input-dir : 输入视频所在目录，默认 output
    -o / --output    : 输出视频文件，默认当前目录下的 merged.mp4
    --copy           : 使用无损流复制（仅当所有视频编码参数完全一致时可用，
                       速度快但易出现卡顿/花屏；默认关闭，采用重新编码）

依赖系统命令行工具 ffmpeg。
默认使用 concat filter 重新编码，能保证时间戳连续、画面正常，
可正确处理分辨率/帧率/编码参数不一致的视频。
"""

import argparse
import os
import subprocess
import tempfile

# 支持合并的视频扩展名
VIDEO_EXTS = ('.mp4', '.mov', '.m4v', '.avi', '.mkv')


def find_videos(input_dir):
    """返回输入目录中按文件名排序的视频文件绝对路径列表

    会自动跳过水印处理过程中产生的 .temp.mp4 中间文件
    （这类文件通常没有音频流，会导致合并失败）。
    """
    if not os.path.isdir(input_dir):
        return []
    videos = [
        os.path.join(input_dir, f)
        for f in sorted(os.listdir(input_dir))
        if f.lower().endswith(VIDEO_EXTS) and not f.lower().endswith(".temp.mp4")
    ]
    return videos


def _merge_by_concat_filter(videos, output_file):
    """使用 concat filter 重新编码合并，保证时间戳连续、画面正常。

    能正确处理各视频分辨率/帧率/编码参数不一致的情况。
    """
    # 构造输入参数
    cmd = ["ffmpeg", "-y"]
    for v in videos:
        cmd += ["-i", os.path.abspath(v)]

    # 构造 concat filter：[0:v][0:a][1:v][1:a]...concat=n=N:v=1:a=1[outv][outa]
    n = len(videos)
    filter_inputs = "".join(f"[{i}:v][{i}:a]" for i in range(n))
    filter_complex = f"{filter_inputs}concat=n={n}:v=1:a=1[outv][outa]"

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-c:a", "aac",
        output_file,
    ]
    print("正在合并（concat filter 重新编码模式）...")
    return subprocess.run(cmd, capture_output=True, text=True)


def _merge_by_concat_demuxer(videos, output_file):
    """使用 concat demuxer + 流复制（无损快速，要求编码参数完全一致）"""
    list_fd, list_path = tempfile.mkstemp(suffix=".txt", text=True)
    try:
        with os.fdopen(list_fd, "w") as f:
            for v in videos:
                safe_path = os.path.abspath(v).replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_path, "-c", "copy", output_file,
        ]
        print("正在合并（无损 copy 模式）...")
        return subprocess.run(cmd, capture_output=True, text=True)
    finally:
        if os.path.exists(list_path):
            os.remove(list_path)


def merge_videos(input_dir, output_file, use_copy=False):
    """按文件名顺序将 input_dir 下的视频合并为 output_file

    use_copy=True 时使用无损流复制（快但要求编码完全一致），
    否则使用 concat filter 重新编码（稳妥，默认）。
    """
    videos = find_videos(input_dir)
    if not videos:
        print(f"未在目录 {input_dir} 中找到视频文件")
        return False

    print(f"找到 {len(videos)} 个视频，按文件名顺序合并：")
    for i, v in enumerate(videos, start=1):
        print(f"  {i}. {os.path.basename(v)}")

    if use_copy:
        result = _merge_by_concat_demuxer(videos, output_file)
        if result.returncode != 0:
            print("无损合并失败，回退到重新编码模式...")
            result = _merge_by_concat_filter(videos, output_file)
    else:
        result = _merge_by_concat_filter(videos, output_file)

    if result.returncode == 0:
        print(f"合并成功: {output_file}")
        return True
    else:
        print(f"合并失败: {result.stderr}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="将多个视频按文件名顺序合并成一个视频"
    )
    parser.add_argument(
        "-i", "--input-dir", default="output",
        help="输入视频所在目录，默认: output",
    )
    parser.add_argument(
        "-o", "--output", default="merged.mp4",
        help="输出视频文件，默认: 当前目录下的 merged.mp4",
    )
    parser.add_argument(
        "--copy", action="store_true",
        help="使用无损流复制模式（快，但要求所有视频编码参数完全一致，否则可能卡顿/花屏）",
    )
    args = parser.parse_args()
    merge_videos(args.input_dir, args.output, use_copy=args.copy)


if __name__ == "__main__":
    main()

