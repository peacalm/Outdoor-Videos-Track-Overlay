#!/usr/bin/env python3
"""
动态照片（Motion Photo）转视频功能库

把 JPEG 内嵌短视频（如小米/Google 的 MVIMG_xxx.jpg 动态照片）提取为 mp4 视频。

对外接口:
  - is_motion_photo(path) -> bool
      判断图片是否为动态照片。
  - extract_motion_photo_video(input_image, output_video) -> bool
      把动态照片内嵌的视频提取为 mp4 文件。
  - convert_directory(input_dir, output_dir) -> (success, failed)
      扫描目录中所有动态照片，将其内嵌视频提取为 mp4。

命令行用法:
  python3 motion_photo_to_video.py -i input_dir -o output_dir
      仅把目录中的动态照片转为 mp4，不做任何水印处理。
"""

import argparse
import os
import subprocess


def is_motion_photo(path):
    """判断图片是否为动态照片（JPEG 内嵌短视频，如小米/Google 的 MVIMG_xxx.jpg）。

    动态照片的 XMP 元数据中带 MicroVideo 标记，直接搜索文件字节即可识别，
    不依赖 Pillow 的 XMP 解析（避免 defusedxml 依赖）与 ffprobe（避免误判静态图）。
    """
    try:
        with open(path, 'rb') as f:
            data = f.read()
        return b'MicroVideo' in data
    except Exception:
        return False


def extract_motion_photo_video(input_image, output_video):
    """把动态照片（JPEG 内嵌短视频）的内嵌视频提取为 mp4。

    小米/Google 动态照片的短视频以完整 MP4 内嵌在文件尾部，从 ftyp box 的起始
    （size 字段，位于 'ftyp' 之前 4 字节）截取到文件末尾即可；失败则回退 ffmpeg。
    """
    try:
        with open(input_image, 'rb') as f:
            data = f.read()
        idx = data.find(b'ftyp')
        while idx >= 4:
            # box 起始为 size 字段，ftyp box 尺寸通常较小（16~64），用于排除误匹配
            size = int.from_bytes(data[idx - 4:idx], 'big')
            if 16 <= size <= 256:
                mp4_bytes = data[idx - 4:]
                head = mp4_bytes[:8192]
                if len(mp4_bytes) > 1000 and (b'moov' in head or b'mdat' in head):
                    with open(output_video, 'wb') as f:
                        f.write(mp4_bytes)
                    return True
            idx = data.find(b'ftyp', idx + 1)
    except Exception:
        pass

    # ffmpeg 直接提取回退
    cmd = [
        'ffmpeg', '-y', '-i', input_image,
        '-map', '0:v:0', '-map', '0:a:0?',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-shortest', output_video,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def convert_directory(input_dir, output_dir):
    """扫描目录中所有动态照片，将其内嵌视频提取为 mp4。

    返回 (success, failed) 计数元组。
    """
    os.makedirs(output_dir, exist_ok=True)
    if not os.path.isdir(input_dir):
        print(f"输入目录不存在: {input_dir}")
        return 0, 0

    success = 0
    failed = 0
    for f in sorted(os.listdir(input_dir)):
        input_path = os.path.join(input_dir, f)
        if not os.path.isfile(input_path):
            continue
        if not is_motion_photo(input_path):
            continue

        base = os.path.splitext(f)[0]
        output_path = os.path.join(output_dir, base + '.mp4')
        print(f"转换动态照片: {f} -> {output_path}")
        if extract_motion_photo_video(input_path, output_path):
            success += 1
        else:
            failed += 1
            print(f"  转换失败: {f}")

    print(f"完成: 成功 {success} 个, 失败 {failed} 个")
    return success, failed


def main():
    parser = argparse.ArgumentParser(
        description="把目录中的动态照片（Motion Photo，如小米 MVIMG_xxx.jpg）转为 mp4 视频"
    )
    parser.add_argument(
        "-i", "--input-dir", default="input",
        help="输入文件夹名字（包含动态照片），默认: input",
    )
    parser.add_argument(
        "-o", "--output-dir", default="output",
        help="输出文件夹名字，默认: output",
    )
    args = parser.parse_args()
    convert_directory(args.input_dir, args.output_dir)


if __name__ == "__main__":
    main()
