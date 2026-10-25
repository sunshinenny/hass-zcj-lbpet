"""68宠物 集成入口。"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ZcjApiClient, ZcjError
from .const import (
    CONF_COORD_SYSTEM,
    CONF_PASSWORD,
    CONF_PHONE,
    CONF_SCAN_INTERVAL,
    DEFAULT_COORD_SYSTEM,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import ZcjCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.BINARY_SENSOR,
]

SERVICE_REFRESH = "refresh_location"
SERVICE_FOOTPRINT = "get_footprint"

ATTR_PET_NO = "pet_no"
ATTR_DATE = "date"


def _pet_no_from_call(hass: HomeAssistant, call: ServiceCall) -> str:
    """从 service 调用里解析 pet_no（支持 target device）。"""
    if pet_no := call.data.get(ATTR_PET_NO):
        return str(pet_no)

    device_ids = call.data.get("device_id") or []
    if isinstance(device_ids, str):
        device_ids = [device_ids]
    dev_reg = dr.async_get(hass)
    for device_id in device_ids:
        if (device := dev_reg.async_get(device_id)) is None:
            continue
        for domain, ident in device.identifiers:
            if domain == DOMAIN:
                return ident
    raise HomeAssistantError("需要指定 pet_no 或目标设备")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """建立集成。"""
    session = async_get_clientsession(hass)
    client = ZcjApiClient(
        session,
        entry.data[CONF_PHONE],
        entry.data[CONF_PASSWORD],
    )
    coordinator = ZcjCoordinator(
        hass,
        entry,
        client,
        entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        entry.options.get(CONF_COORD_SYSTEM, DEFAULT_COORD_SYSTEM),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        _register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载集成。"""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """选项变更后重载。"""
    await hass.config_entries.async_reload(entry.entry_id)


def _register_services(hass: HomeAssistant) -> None:
    """注册服务。"""

    def _first_coordinator() -> ZcjCoordinator:
        entries = hass.data.get(DOMAIN) or {}
        if not entries:
            raise HomeAssistantError("68宠物 集成尚未加载")
        return next(iter(entries.values()))

    async def _handle_refresh(call: ServiceCall) -> None:
        coordinator = _first_coordinator()
        pet_no = _pet_no_from_call(hass, call)
        await coordinator.async_force_refresh(pet_no)
        await coordinator.async_request_refresh()

    async def _handle_footprint(call: ServiceCall) -> dict[str, Any]:
        coordinator = _first_coordinator()
        pet_no = _pet_no_from_call(hass, call)
        raw_date = call.data.get(ATTR_DATE)
        if raw_date is None:
            target = date.today()
        elif isinstance(raw_date, (date, datetime)):
            target = raw_date
        else:
            target = datetime.strptime(str(raw_date), "%Y-%m-%d").date()
        yymmdd = target.strftime("%y%m%d")
        points = await coordinator.async_fetch_footprint(pet_no, yymmdd)
        return {
            "pet_no": pet_no,
            "date": target.isoformat(),
            "count": len(points),
            "points": points,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH,
        _handle_refresh,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_PET_NO): cv.string,
                vol.Optional("device_id"): vol.Any(cv.string, [cv.string]),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_FOOTPRINT,
        _handle_footprint,
        schema=vol.Schema(
            {
                vol.Optional(ATTR_PET_NO): cv.string,
                vol.Optional("device_id"): vol.Any(cv.string, [cv.string]),
                vol.Optional(ATTR_DATE): vol.Any(cv.date, cv.string),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
