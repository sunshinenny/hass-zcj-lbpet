"""坐标系转换。

68宠物 全链路使用高德地图 SDK，接口返回的经纬度是 GCJ-02（火星坐标）；
Home Assistant 自带的 Mapbox / OpenStreetMap 使用 WGS-84。
直接把 GCJ-02 当 WGS-84 用，在华东地区会稳定偏移 500 米左右（东北→西南方向）。

需要高德类自定义地图卡片时，可把 coord_system 选项设为 gcj02 关闭转换。
"""

from __future__ import annotations

import math

__all__ = ["gcj02_to_wgs84", "wgs84_to_gcj02", "out_of_china"]

_A = 6378245.0  # 克拉索夫斯基椭球长半轴
_EE = 0.00669342162296594323  # 偏心率平方


def out_of_china(lat: float, lon: float) -> bool:
    """境外坐标不做偏移校正。"""
    return not (73.66 < lon < 135.05 and 3.86 < lat < 53.55)


def _delta_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
    ret += 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _delta_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y
    ret += 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lat: float, lon: float) -> tuple[float, float]:
    """WGS-84 -> GCJ-02。"""
    if out_of_china(lat, lon):
        return lat, lon
    d_lat = _delta_lat(lon - 105.0, lat - 35.0)
    d_lon = _delta_lon(lon - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = 1 - _EE * math.sin(rad_lat) ** 2
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrt_magic) * math.pi)
    d_lon = (d_lon * 180.0) / (_A / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lat + d_lat, lon + d_lon


def gcj02_to_wgs84(lat: float, lon: float) -> tuple[float, float]:
    """GCJ-02 -> WGS-84（迭代逼近，3 轮即可收敛到厘米级）。"""
    if out_of_china(lat, lon):
        return lat, lon
    w_lat, w_lon = lat, lon
    for _ in range(3):
        g_lat, g_lon = wgs84_to_gcj02(w_lat, w_lon)
        w_lat += lat - g_lat
        w_lon += lon - g_lon
    return w_lat, w_lon
