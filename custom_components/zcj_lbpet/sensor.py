"""传感器平台。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfLength,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MANUFACTURER
from .coordinator import PetData, ZcjCoordinator, account_device_info


@dataclass(frozen=True, kw_only=True)
class ZcjSensorDescription(SensorEntityDescription):
    """带取值函数的传感器描述。"""

    value_fn: Callable[[PetData], Any]
    requires_tracker: bool = False


def _point(pet: PetData) -> dict[str, Any]:
    return pet.latest_point or {}


def _motion(pet: PetData) -> dict[str, Any]:
    return pet.motion or {}


SENSORS: tuple[ZcjSensorDescription, ...] = (
    ZcjSensorDescription(
        key="steps",
        translation_key="steps",
        icon="mdi:paw",
        native_unit_of_measurement="步",
        state_class=SensorStateClass.MEASUREMENT,
        requires_tracker=True,
        value_fn=lambda pet: _motion(pet).get("motionCount"),
    ),
    ZcjSensorDescription(
        key="distance",
        translation_key="distance",
        icon="mdi:map-marker-distance",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        requires_tracker=True,
        value_fn=lambda pet: _motion(pet).get("mileage"),
    ),
    ZcjSensorDescription(
        key="calories",
        translation_key="calories",
        icon="mdi:fire",
        native_unit_of_measurement="kcal",
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        requires_tracker=True,
        value_fn=lambda pet: _motion(pet).get("kilocalorie"),
    ),
    ZcjSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        requires_tracker=True,
        value_fn=lambda pet: _point(pet).get("electricQuantity")
        if _point(pet).get("electricQuantity") is not None
        else _motion(pet).get("electricQuantity"),
    ),
    ZcjSensorDescription(
        key="signal",
        translation_key="signal",
        icon="mdi:signal",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        requires_tracker=True,
        value_fn=lambda pet: _point(pet).get("signalPower"),
    ),
    ZcjSensorDescription(
        key="grade",
        translation_key="grade",
        icon="mdi:podium",
        requires_tracker=True,
        value_fn=lambda pet: _motion(pet).get("grades"),
    ),
    ZcjSensorDescription(
        key="avg7_steps",
        translation_key="avg7_steps",
        icon="mdi:chart-line",
        native_unit_of_measurement="步",
        state_class=SensorStateClass.MEASUREMENT,
        requires_tracker=True,
        value_fn=lambda pet: _motion(pet).get("sevenDaysAvgMotionCount"),
    ),
    ZcjSensorDescription(
        key="avg7_distance",
        translation_key="avg7_distance",
        icon="mdi:chart-bell-curve",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        requires_tracker=True,
        value_fn=lambda pet: _motion(pet).get("sevenDaysAvgMileage"),
    ),
    ZcjSensorDescription(
        key="total_steps",
        translation_key="total_steps",
        icon="mdi:counter",
        native_unit_of_measurement="步",
        state_class=SensorStateClass.TOTAL,
        requires_tracker=True,
        value_fn=lambda pet: _motion(pet).get("totalMotionCount"),
    ),
    ZcjSensorDescription(
        key="accuracy",
        translation_key="accuracy",
        icon="mdi:map-marker-radius",
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        requires_tracker=True,
        value_fn=lambda pet: float(_point(pet)["radius"])
        if _point(pet).get("radius") is not None
        else None,
    ),
    ZcjSensorDescription(
        key="address",
        translation_key="address",
        icon="mdi:home-map-marker",
        requires_tracker=True,
        value_fn=lambda pet: _point(pet).get("address"),
    ),
    ZcjSensorDescription(
        key="last_locate",
        translation_key="last_locate",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        requires_tracker=True,
        value_fn=lambda pet: _parse_time(_point(pet).get("locateTime")),
    ),
    ZcjSensorDescription(
        key="imei",
        translation_key="imei",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
        requires_tracker=True,
        value_fn=lambda pet: _point(pet).get("imei"),
    ),
)


def _parse_time(raw: Any) -> datetime | None:
    """locateTime 形如 2026-10-25T15:32:00.382（本地时间）。"""
    if not raw or not isinstance(raw, str):
        return None
    try:
        from homeassistant.util import dt as dt_util

        parsed = datetime.fromisoformat(raw)
        return parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    except ValueError:
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """创建传感器实体。"""
    coordinator: ZcjCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for pet in coordinator.data.pets.values():
        for description in SENSORS:
            if description.requires_tracker and not pet.has_tracker:
                continue
            entities.append(ZcjSensor(coordinator, pet.pet_no, description))
    entities.append(ZcjTokenExpirySensor(coordinator, account_device_info(entry)))
    async_add_entities(entities)


class ZcjSensor(CoordinatorEntity[ZcjCoordinator], SensorEntity):
    """宠物传感器。"""

    _attr_has_entity_name = True
    entity_description: ZcjSensorDescription

    def __init__(
        self,
        coordinator: ZcjCoordinator,
        pet_no: str,
        description: ZcjSensorDescription,
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
    def native_value(self) -> Any:
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
        point = _point(pet)
        return {
            "pet_no": pet.pet_no,
            "card_no": pet.card_no,
            "poi": point.get("poi"),
            "location_type": point.get("locationType"),
            "moving": (pet.location or {}).get("moving"),
            "iccid": (pet.location or {}).get("iccid"),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class ZcjTokenExpirySensor(CoordinatorEntity[ZcjCoordinator], SensorEntity):
    """账号级：token 剩余有效期（秒）。

    和 `binary_sensor.token_invalid` 配套——那个告诉你“已经失效”，这个告诉你“还有多久到期”，
    方便在自动化里提前预警（例如剩余 < 2 天时发通知）。
    """

    _attr_has_entity_name = True
    _attr_translation_key = "token_expiry"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ZcjCoordinator,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry_id}_token_expiry"
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        # 同上：token 失效时也要能读到值（0 / 负数），不能跟着 coordinator 一起变 unavailable
        return True

    @property
    def native_value(self) -> int | None:
        left = self.coordinator.client.token_seconds_left
        return left if left is None else max(0, left)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.coordinator.client.token_status
        expires_at = status.get("expires_at")
        return {
            "expires_at": dt_util.utc_from_timestamp(float(expires_at)) if expires_at else None,
            "refresh_due": status.get("refresh_due"),
            "refresh_margin": status.get("refresh_margin"),
            "auth_failures": status.get("auth_failures"),
            "retry_in": status.get("retry_in"),
        }
