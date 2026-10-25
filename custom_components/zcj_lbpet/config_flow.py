"""配置流程。"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ZcjApiClient, ZcjAuthError, ZcjError
from .const import (
    CONF_COORD_SYSTEM,
    CONF_PASSWORD,
    CONF_PHONE,
    CONF_SCAN_INTERVAL,
    COORD_GCJ02,
    COORD_WGS84,
    DEFAULT_COORD_SYSTEM,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PHONE): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class ZcjConfigFlow(ConfigFlow, domain=DOMAIN):
    """处理 68宠物 账号接入。"""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        errors: dict[str, str] = {}

        if user_input is not None:
            phone = user_input[CONF_PHONE].strip()
            await self.async_set_unique_id(phone)
            self._abort_if_unique_id_configured()

            client = ZcjApiClient(
                async_get_clientsession(self.hass),
                phone,
                user_input[CONF_PASSWORD],
            )
            try:
                await client.async_login()
                pets = await client.async_get_pets()
            except ZcjAuthError:
                errors["base"] = "invalid_auth"
            except ZcjError as err:
                _LOGGER.warning("68宠物 连接失败：%s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("68宠物 配置时发生未预期错误")
                errors["base"] = "unknown"
            else:
                if not pets:
                    errors["base"] = "no_pet"
                else:
                    return self.async_create_entry(
                        title=f"68宠物 ({phone})",
                        data={
                            CONF_PHONE: phone,
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ZcjOptionsFlow()


class ZcjOptionsFlow(OptionsFlow):
    """轮询间隔设置。

    HA 2024.11 起 OptionsFlow.config_entry 由框架注入且只读，
    因此不要再自定义 __init__ 赋值，否则会抛 AttributeError。
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        current_coord = self.config_entry.options.get(
            CONF_COORD_SYSTEM, DEFAULT_COORD_SYSTEM
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_SCAN_INTERVAL, default=current_interval): vol.All(
                        int, vol.Range(min=MIN_SCAN_INTERVAL, max=3600)
                    ),
                    vol.Optional(CONF_COORD_SYSTEM, default=current_coord): vol.In(
                        {
                            COORD_WGS84: "WGS-84 — HA 自带地图（推荐）",
                            COORD_GCJ02: "GCJ-02 高德 — 自定义高德卡片",
                        }
                    ),
                }
            ),
        )
