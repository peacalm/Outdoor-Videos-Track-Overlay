#!/usr/bin/env python3
"""
轨迹文件解析模块

支持格式: .kml, .gpx

输出统一格式:
{
    "summary": {
        "total_distance_km": float,        # 总距离（公里）
        "total_ascent_m": float,           # 累计爬升高度（米）
        "total_descent_m": float,          # 累计下降高度（米）
        "max_elevation_m": float,          # 最高海拔（米）
        "min_elevation_m": float,          # 最低海拔（米）
        "total_duration_s": float,         # 累计用时（秒）
        "avg_pace_min_per_km": float,      # 平均配速（分钟/公里）
        "avg_speed_km_per_h": float        # 平均速度（公里/小时）
    },
    "points": [
        {
            "lon": float,                  # 经度
            "lat": float,                  # 纬度
            "elevation": float,            # 海拔高度（米）
            "time": datetime,              # 时间（datetime 对象）
            "cumulative_distance_km": float,  # 累计距离（公里）
            "cumulative_ascent_m": float,    # 累计爬升高度（米）
            "cumulative_descent_m": float,   # 累计下降高度（米）
            "cumulative_duration_s": float,  # 累计用时（秒，从轨迹起点到当前点）
            "grade_percent": float,        # 当前坡度 (%)
            "pace_min_per_km": float,      # 当前配速 (min/km)
            "speed_km_per_h": float        # 当前速度 (km/h)
        },
        ...
    ],
    "raw_points": [(lon, lat, elevation), ...],  # 原始轨迹点（兼容旧接口）
    "raw_times": [datetime, ...]                 # 原始时间列表（兼容旧接口）
}
"""

import math
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

try:
    from scipy.signal import medfilt
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


# ---------------------------------------------------------------------------
# 基础计算工具函数
# ---------------------------------------------------------------------------

def calculate_distance(lat1, lon1, lat2, lon2):
    """使用 Haversine 公式计算两点间距离（公里）"""
    R = 6371.0
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _filter_elevations(elevations, filter_window=13):
    """对海拔数组做中值滤波；若缺少 scipy 则返回原数据"""
    if HAS_SCIPY and len(elevations) >= filter_window:
        return list(medfilt(elevations, filter_window))
    return list(elevations)


def _parse_datetime(s):
    """兼容多种常见时间格式"""
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    formats = [
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# KML 解析
# ---------------------------------------------------------------------------

def _parse_kml(kml_file):
    """从 KML 文件中读取轨迹点，返回 (points, times)

    points: [(lon, lat, elevation), ...]
    times : [datetime, ...]
    """
    tree = ET.parse(kml_file)
    root = tree.getroot()
    namespaces = {
        'kml': 'http://www.opengis.net/kml/2.2',
        'gx': 'http://www.google.com/kml/ext/2.2',
    }

    track_points = []
    track_times = []
    elevations = []

    # 1) 优先从 gx:Track 获取（支持不同的命名空间写法）
    track = root.find(
        './/kml:Folder[@id="TbuluTrackFolder"]/kml:Placemark/gx:Track',
        namespaces,
    )
    if track is None:
        track = root.find('.//gx:Track', namespaces)
    if track is None:
        for elem in root.iter():
            if 'Track' in elem.tag and 'gx' in elem.tag:
                track = elem
                break

    if track is not None:
        times = (track.findall('when')
                 or track.findall('{http://www.opengis.net/kml/2.2}when')
                 or track.findall('kml:when', namespaces))
        coords = (track.findall('gx:coord', namespaces)
                  or track.findall('coord'))

        if not coords:
            raise ValueError("KML 轨迹点为空")

        for coord in coords:
            parts = coord.text.strip().split()
            if len(parts) < 2:
                raise ValueError("KML 轨迹点坐标格式错误")
            lon = float(parts[0])
            lat = float(parts[1])
            elevation = float(parts[2]) if len(parts) >= 3 else 0.0
            elevations.append(elevation)
            track_points.append((lon, lat, elevation))

        if not times:
            raise ValueError("KML 中未找到时间信息")

        for t in times:
            dt = _parse_datetime(t.text)
            if dt is None:
                raise ValueError("KML 轨迹点时间格式错误")
            track_times.append(dt)

    # 2) 若没有 gx:Track，再尝试从 LineString / coordinates 获取
    if not track_points:
        coords_elem = root.find('.//kml:coordinates', namespaces)
        if coords_elem is None:
            coords_elem = root.find('.//coordinates')
        if coords_elem is not None and coords_elem.text:
            for line in coords_elem.text.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split(',')
                if len(parts) < 2:
                    continue
                lon = float(parts[0])
                lat = float(parts[1])
                elevation = float(parts[2]) if len(parts) >= 3 else 0.0
                elevations.append(elevation)
                track_points.append((lon, lat, elevation))
            # 简单 coordinates 没有时间点，按等间隔 1 秒 模拟
            track_times = [datetime.utcfromtimestamp(i)
                           for i in range(len(track_points))]

    if not track_points:
        raise ValueError("无法从 KML 中解析到轨迹点")

    # 海拔中值滤波
    elevations_filtered = _filter_elevations(elevations)
    track_points = [(p[0], p[1], elevations_filtered[i])
                    for i, p in enumerate(track_points)]

    return track_points, track_times


# ---------------------------------------------------------------------------
# GPX 解析
# ---------------------------------------------------------------------------

def _parse_gpx(gpx_file):
    """从 GPX 文件中读取轨迹点，返回 (points, times)

    points: [(lon, lat, elevation), ...]
    times : [datetime, ...]
    """
    tree = ET.parse(gpx_file)
    root = tree.getroot()

    # 命名空间处理
    ns = ''
    match_ns = None
    if root.tag.startswith('{'):
        match_ns = root.tag.split('}', 1)[0][1:]
        ns = match_ns

    def _findall(parent, tag):
        if ns:
            return parent.findall('{%s}%s' % (ns, tag))
        return parent.findall(tag)

    def _find(parent, tag):
        if ns:
            return parent.find('{%s}%s' % (ns, tag))
        return parent.find(tag)

    track_points = []
    track_times = []
    elevations = []

    # 1) 先尝试 trk -> trkseg -> trkpt
    for trk in _findall(root, 'trk'):
        for trkseg in _findall(trk, 'trkseg'):
            for pt in _findall(trkseg, 'trkpt'):
                lat = float(pt.attrib.get('lat'))
                lon = float(pt.attrib.get('lon'))
                ele_elem = _find(pt, 'ele')
                elevation = (float(ele_elem.text)
                             if ele_elem is not None and ele_elem.text else 0.0)
                time_elem = _find(pt, 'time')
                dt = (_parse_datetime(time_elem.text)
                      if time_elem is not None else None)
                elevations.append(elevation)
                track_points.append((lon, lat, elevation))
                track_times.append(dt)

    # 2) 若没有 trk，再尝试 rte -> rtept
    if not track_points:
        for rte in _findall(root, 'rte'):
            for pt in _findall(rte, 'rtept'):
                lat = float(pt.attrib.get('lat'))
                lon = float(pt.attrib.get('lon'))
                ele_elem = _find(pt, 'ele')
                elevation = (float(ele_elem.text)
                             if ele_elem is not None and ele_elem.text else 0.0)
                time_elem = _find(pt, 'time')
                dt = (_parse_datetime(time_elem.text)
                      if time_elem is not None else None)
                elevations.append(elevation)
                track_points.append((lon, lat, elevation))
                track_times.append(dt)

    # 3) 若没有 wpt，也尝试 wpt
    if not track_points:
        for pt in _findall(root, 'wpt'):
            lat = float(pt.attrib.get('lat'))
            lon = float(pt.attrib.get('lon'))
            ele_elem = _find(pt, 'ele')
            elevation = (float(ele_elem.text)
                         if ele_elem is not None and ele_elem.text else 0.0)
            time_elem = _find(pt, 'time')
            dt = (_parse_datetime(time_elem.text)
                  if time_elem is not None else None)
            elevations.append(elevation)
            track_points.append((lon, lat, elevation))
            track_times.append(dt)

    if not track_points:
        raise ValueError("无法从 GPX 中解析到轨迹点")

    # 如果 GPX 没有时间戳，则用递增时间代替（避免后续计算出错）
    if all(t is None for t in track_times):
        track_times = [datetime.utcfromtimestamp(i)
                       for i in range(len(track_points))]

    # 海拔中值滤波
    elevations_filtered = _filter_elevations(elevations)
    track_points = [(p[0], p[1], elevations_filtered[i])
                    for i, p in enumerate(track_points)]

    return track_points, track_times


# ---------------------------------------------------------------------------
# 计算派生数据（summary & 各点的坡度/配速/速度等）
# ---------------------------------------------------------------------------

def _build_result(points, times):
    """根据原始 points/times 构建统一格式的解析结果"""
    if not points or not times:
        raise ValueError("轨迹点或时间为空")

    n = len(points)
    result_points = []
    total_distance = 0.0
    total_ascent = 0.0
    total_descent = 0.0
    max_elevation = float('-inf')
    min_elevation = float('inf')
    cum_dist = 0.0
    cum_duration = 0.0

    for i in range(n):
        lon, lat, ele = points[i]
        if ele > max_elevation:
            max_elevation = ele
        if ele < min_elevation:
            min_elevation = ele

        # 当前点距上一点的距离 / 时间差
        if i == 0:
            seg_dist_km = 0.0
            seg_elev_diff = 0.0
            seg_time_s = 0.0
            cum_dist = 0.0
        else:
            prev_lon, prev_lat, prev_ele = points[i - 1]
            seg_dist_km = calculate_distance(prev_lat, prev_lon, lat, lon)
            seg_elev_diff = ele - prev_ele
            if seg_elev_diff > 0:
                total_ascent += seg_elev_diff
            else:
                total_descent += (-seg_elev_diff)
            cum_dist += seg_dist_km
            if times[i] is not None and times[i - 1] is not None:
                seg_time_s = (times[i] - times[i - 1]).total_seconds()
            else:
                seg_time_s = 0.0

        total_distance = cum_dist
        cum_duration += seg_time_s

        # 坡度（grade）= 垂直变化 / 水平距离 * 100%
        # 水平距离用球面距离米近似
        seg_dist_m = seg_dist_km * 1000.0
        grade = 0.0
        if seg_dist_m > 0:
            grade = (seg_elev_diff / seg_dist_m) * 100.0

        # 当前配速 & 速度（本点使用前一段的速度平滑）
        if seg_time_s > 0 and seg_dist_km > 0:
            speed_kmh = seg_dist_km / (seg_time_s / 3600.0)
            pace_min_per_km = seg_time_s / 60.0 / seg_dist_km
        else:
            # 用总平均代替
            speed_kmh = 0.0
            pace_min_per_km = 0.0

        result_points.append({
            "lon": lon,
            "lat": lat,
            "elevation": ele,
            "time": times[i],
            "cumulative_distance_km": cum_dist,
            "cumulative_ascent_m": total_ascent,
            "cumulative_descent_m": total_descent,
            "cumulative_duration_s": cum_duration,
            "grade_percent": grade,
            "pace_min_per_km": pace_min_per_km,
            "speed_km_per_h": speed_kmh,
        })

    # 总用时
    if times[0] is not None and times[-1] is not None:
        total_duration_s = (times[-1] - times[0]).total_seconds()
    else:
        total_duration_s = 0.0

    # 平均配速 / 平均速度
    if total_distance > 0 and total_duration_s > 0:
        avg_speed_kmh = total_distance / (total_duration_s / 3600.0)
        avg_pace_min_per_km = total_duration_s / 60.0 / total_distance
    else:
        avg_speed_kmh = 0.0
        avg_pace_min_per_km = 0.0

    if max_elevation == float('-inf'):
        max_elevation = 0.0
    if min_elevation == float('inf'):
        min_elevation = 0.0

    summary = {
        "total_distance_km": total_distance,
        "total_ascent_m": total_ascent,
        "total_descent_m": total_descent,
        "max_elevation_m": max_elevation,
        "min_elevation_m": min_elevation,
        "total_duration_s": total_duration_s,
        "avg_pace_min_per_km": avg_pace_min_per_km,
        "avg_speed_km_per_h": avg_speed_kmh,
    }

    # 兼容旧接口的简化字段
    raw_points = [(p["lon"], p["lat"], p["elevation"]) for p in result_points]
    raw_times = [p["time"] for p in result_points]

    return {
        "summary": summary,
        "points": result_points,
        "raw_points": raw_points,
        "raw_times": raw_times,
    }


# ---------------------------------------------------------------------------
# 对外主接口
# ---------------------------------------------------------------------------

def parse_track_file(track_file):
    """解析轨迹文件（支持 .kml / .gpx），返回统一格式结果"""
    if not os.path.isfile(track_file):
        raise FileNotFoundError("轨迹文件不存在: %s" % track_file)

    ext = os.path.splitext(track_file)[1].lower()
    if ext == '.kml':
        points, times = _parse_kml(track_file)
    elif ext == '.gpx':
        points, times = _parse_gpx(track_file)
    else:
        raise ValueError("不支持的轨迹文件格式: %s（仅支持 .kml / .gpx）" % ext)

    return _build_result(points, times)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python track_parser.py <轨迹文件>")
        sys.exit(1)
    result = parse_track_file(sys.argv[1])
    print("=== Summary ===")
    for k, v in result["summary"].items():
        print(f"  {k}: {v}")
    print(f"Total points: {len(result['points'])}")
    if result["points"]:
        p = result["points"][0]
        print(f"First point keys: {list(p.keys())}")
