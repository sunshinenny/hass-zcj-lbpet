"""数据协调器。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ZcjApiClient, ZcjAuthError, ZcjError
from .const import (
    CONF_PHONE,
    COORD_WGS84,
    DEFAULT_COORD_SYSTEM,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MANUFACTURER,
)
from .geo import gcj02_to_wgs84

_LOGGER = logging.getLogger(__name__)


def account_device_info(entry: ConfigEntry) -> DeviceInfo:
    """账号级设备：token 状态这类“账号维度”的实体的归属。"""
    phone = str(entry.data.get(CONF_PHONE) or "")
    masked = f"{phone[:3]}****{phone[-4:]}" if len(phone) >= 7 else phone
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"68宠物 账号 {masked}".strip(),
        manufacturer=MANUFACTURER,
        model="云端账号",
        entry_type=DeviceEntryType.SERVICE,
    )


@dataclass
class PetData:
    """单只宠物的聚合数据。"""

    pet: dict[str, Any]
    motion: dict[str, Any] | None = None
    location: dict[str, Any] | None = None
    online: bool = False

    @property
    def pet_no(self) -> str:
        return self.pet.get("petNo") or ""

    @property
    def card_no(self) -> str:
        return self.pet.get("cardNo") or ""

    @property
    def nickname(self) -> str:
        return self.pet.get("nickname") or self.pet_no or "宠物"

    @property
    def has_tracker(self) -> bool:
        return bool(self.card_no)

    @property
    def latest_point(self) -> dict[str, Any] | None:
        points = (self.location or {}).get("locationList") or []
        return points[0] if points else None


@dataclass
class ZcjData:
    """协调器整体数据。"""

    user: dict[str, Any] = field(default_factory=dict)
    pets: dict[str, PetData] = field(default_factory=dict)

    def pet_by_id(self, pet_no: str) -> PetData | None:
        return self.pets.get(pet_no)


class ZcjCoordinator(DataUpdateCoordinator[ZcjData]):
    """轮询 68宠物 云端。"""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ZcjApiClient,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        coord_system: str = DEFAULT_COORD_SYSTEM,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.entry_id = entry.entry_id
        self.coord_system = coord_system

    def _convert_points(self, points: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把高德坐标点转成目标坐标系。

        接口原始数据是 GCJ-02；HA 自带地图要 WGS-84。
        原始值保留在 `raw_gcj02` 里，方便排查。
        """
        if self.coord_system != COORD_WGS84:
            return points
        for point in points:
            try:
                lat = float(point["latitude"])
                lon = float(point["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            w_lat, w_lon = gcj02_to_wgs84(lat, lon)
            point["raw_gcj02"] = {
                "latitude": point["latitude"],
                "longitude": point["longitude"],
            }
            point["latitude"] = f"{w_lat:.7f}"
            point["longitude"] = f"{w_lon:.7f}"
        return points

    async def _async_update_data(self) -> ZcjData:
        try:
            pets = await self.client.async_get_pets()
        except ZcjAuthError as err:
            raise UpdateFailed(f"登录失效：{err}") from err
        except ZcjError as err:
            raise UpdateFailed(str(err)) from err

        data = ZcjData(pets={})
        try:
            data.user = await self.client.async_get_user_info() or {}
        except ZcjError:
            data.user = {}

        for pet in pets:
            pet_data = PetData(pet=pet)
            if pet_data.has_tracker:
                await self._fill_tracker_data(pet_data)
            data.pets[pet_data.pet_no] = pet_data
        return data

    async def _fill_tracker_data(self, pet_data: PetData) -> None:
        """位置与运动数据，任一失败不影响另一项。"""
        results = await asyncio.gather(
            self.client.async_get_motion(pet_data.pet_no, pet_data.card_no),
            self.client.async_get_location(pet_data.pet_no, pet_data.card_no),
            return_exceptions=True,
        )
        motion, location = results

        # 认证类错误要显式告警，否则 401 会被 gather 吞掉，表现为“位置莫名其妙不动了”
        if isinstance(motion, Exception):
            if isinstance(motion, ZcjAuthError):
                _LOGGER.warning("%s 步数获取认证失败：%s", pet_data.nickname, motion)
            else:
                _LOGGER.debug("%s 步数获取失败：%s", pet_data.nickname, motion)
        else:
            pet_data.motion = motion

        if isinstance(location, Exception):
            if isinstance(location, ZcjAuthError):
                _LOGGER.warning("%s 定位获取认证失败：%s", pet_data.nickname, location)
            else:
                _LOGGER.debug("%s 定位获取失败：%s", pet_data.nickname, location)
        else:
            if location:
                location["locationList"] = self._convert_points(
                    location.get("locationList") or []
                )
            pet_data.location = location
            pet_data.online = pet_data.latest_point is not None

    async def async_fetch_footprint(self, pet_no: str, yymmdd: str) -> list[dict[str, Any]]:
        pet_data = self.data.pet_by_id(pet_no) if self.data else None
        if pet_data is None or not pet_data.has_tracker:
            raise ZcjError(f"宠物 {pet_no} 没有绑定定位器")
        points = await self.client.async_get_footprint(
            pet_data.pet_no, pet_data.card_no, yymmdd
        )
        return self._convert_points(points)

    async def async_force_refresh(self, pet_no: str) -> None:
        pet_data = self.data.pet_by_id(pet_no) if self.data else None
        if pet_data is None or not pet_data.has_tracker:
            raise ZcjError(f"宠物 {pet_no} 没有绑定定位器")
        await self.client.async_refresh_location(pet_data.pet_no, pet_data.card_no)
