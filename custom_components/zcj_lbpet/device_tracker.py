"""设备追踪（宠物位置）。"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import PetData, ZcjCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """为每只绑定了定位器的宠物创建 tracker。"""
    coordinator: ZcjCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ZcjPetTracker(coordinator, pet.pet_no)
        for pet in coordinator.data.pets.values()
        if pet.has_tracker
    )


class ZcjPetTracker(CoordinatorEntity[ZcjCoordinator], TrackerEntity):
    """宠物 GPS 追踪。"""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator: ZcjCoordinator, pet_no: str) -> None:
        super().__init__(coordinator)
        self._pet_no = pet_no
        self._attr_unique_id = f"{pet_no}_tracker"

    @property
    def _pet(self) -> PetData | None:
        return self.coordinator.data.pet_by_id(self._pet_no)

    @property
    def _point(self) -> dict[str, Any]:
        pet = self._pet
        return (pet.latest_point if pet else None) or {}

    @property
    def device_info(self) -> DeviceInfo:
        pet = self._pet
        if pet is None:
            return DeviceInfo(identifiers={(DOMAIN, self._pet_no)})
        return DeviceInfo(
            identifiers={(DOMAIN, pet.pet_no)},
            name=pet.nickname,
            manufacturer=MANUFACTURER,
            model=pet.pet.get("petBreedName") or "宠物",
            serial_number=pet.card_no or pet.pet_no,
        )

    @property
    def available(self) -> bool:
        return super().available and bool(self._point)

    @property
    def latitude(self) -> float | None:
        try:
            return float(self._point["latitude"])
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def longitude(self) -> float | None:
        try:
            return float(self._point["longitude"])
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def location_accuracy(self) -> int:
        try:
            return int(float(self._point.get("radius") or 0))
        except (TypeError, ValueError):
            return 0

    @property
    def battery_level(self) -> int | None:
        value = self._point.get("electricQuantity")
        return int(value) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pet = self._pet
        point = self._point
        location = (pet.location if pet else None) or {}
        return {
            "pet_no": self._pet_no,
            "card_no": pet.card_no if pet else None,
            "coord_system": self.coordinator.coord_system,
            "raw_gcj02": point.get("raw_gcj02"),
            "address": point.get("address"),
            "poi": point.get("poi"),
            "locate_time": point.get("locateTime"),
            "gps_speed": point.get("gpsSpeed"),
            "satellite_num": point.get("satelliteNum"),
            "signal_power": point.get("signalPower"),
            "imei": point.get("imei"),
            "iccid": location.get("iccid"),
            "moving": location.get("moving"),
            "realtime_status": location.get("realtimeStatus"),
            "find_pet_flag": location.get("findPetFlag"),
            "charge_flag": location.get("chargeFlag"),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
