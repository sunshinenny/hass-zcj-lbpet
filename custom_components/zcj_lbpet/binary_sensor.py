"""二进制传感器。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER
from .coordinator import PetData, ZcjCoordinator, account_device_info


@dataclass(frozen=True, kw_only=True)
class ZcjBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[PetData], bool]
    requires_tracker: bool = False


BINARY_SENSORS: tuple[ZcjBinaryDescription, ...] = (
    ZcjBinaryDescription(
        key="tracker_bound",
        translation_key="tracker_bound",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda pet: pet.has_tracker,
    ),
    ZcjBinaryDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        requires_tracker=True,
        value_fn=lambda pet: pet.online,
    ),
    ZcjBinaryDescription(
        key="charging",
        translation_key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        requires_tracker=True,
        value_fn=lambda pet: str((pet.location or {}).get("chargeFlag")) == "1",
    ),
    ZcjBinaryDescription(
        key="find_pet_mode",
        translation_key="find_pet_mode",
        icon="mdi:bullhorn",
        requires_tracker=True,
        value_fn=lambda pet: bool((pet.location or {}).get("findPetFlag")),
    ),
    ZcjBinaryDescription(
        key="realtime_mode",
        translation_key="realtime_mode",
        icon="mdi:crosshairs-gps",
        requires_tracker=True,
        value_fn=lambda pet: bool((pet.location or {}).get("realtimeStatus")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ZcjCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []
    for pet in coordinator.data.pets.values():
        for description in BINARY_SENSORS:
            if description.requires_tracker and not pet.has_tracker:
                continue
            entities.append(ZcjBinarySensor(coordinator, pet.pet_no, description))
    entities.append(ZcjTokenBinarySensor(coordinator, account_device_info(entry)))
    async_add_entities(entities)


class ZcjBinarySensor(CoordinatorEntity[ZcjCoordinator], BinarySensorEntity):
    """宠物状态二进制传感器。"""

    _attr_has_entity_name = True
    entity_description: ZcjBinaryDescription

    def __init__(
        self,
        coordinator: ZcjCoordinator,
        pet_no: str,
        description: ZcjBinaryDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._pet_no = pet_no
        self._attr_unique_id = f"{pet_no}_{description.key}"

    @property
    def _pet(self) -> PetData | None:
        return self.coordinator.data.pet_by_id(self._pet_no)

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
        return super().available and self._pet is not None

    @property
    def is_on(self) -> bool | None:
        pet = self._pet
        if pet is None:
            return None
        try:
            return self.entity_description.value_fn(pet)
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        pet = self._pet
        if pet is None:
            return None
        return {
            "pet_status": pet.pet.get("petStatus"),
            "pet_card_status": pet.pet.get("petCardStatus"),
            "pet_lost_status": pet.pet.get("petLostStatus"),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class ZcjTokenBinarySensor(CoordinatorEntity[ZcjCoordinator], BinarySensorEntity):
    """账号级：token 是否失效。

    `on` = token 已经不可用（没有 token 或已过服务端有效期）。
    这个实体故意不跟 coordinator 的可用性走——token 失效时 coordinator 会整体
    `UpdateFailed`，用默认 `available` 会让它变成 unavailable，反而把要报警的信息藏起来。
    """

    _attr_has_entity_name = True
    _attr_translation_key = "token_invalid"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ZcjCoordinator,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry_id}_token_invalid"
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.client.token_expired

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = dict(self.coordinator.client.token_status)
        expires_at = status.get("expires_at")
        if expires_at:
            status["expires_at"] = dt_util.utc_from_timestamp(float(expires_at))
        return status
