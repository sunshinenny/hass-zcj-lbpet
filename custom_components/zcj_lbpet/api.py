"""68宠物 (ZCJ LiuBa Pet) API 客户端.

逆向所得的接口约定：
  * 网关        https://zcb.zhichongjia.com，微服务拆分 /zcb-<service>/...
  * 登录        Basic base64("手机号:密码") -> JWT (有效期 30 天)
  * 业务请求    每个请求体必须带 nonceStr(8~32位) 与 sign(32位)
               服务端只校验 sign 非空，不校验内容
  * 会话        单会话，重新登录会使旧 token 失效
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from typing import Any

import aiohttp

from .const import (
    API_BASE,
    APP_VERSION,
    APP_VERSION_CODE,
    TOKEN_REFRESH_MARGIN,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

# 服务端约定的业务错误码
CODE_OK = 200
CODE_PARAM_ERROR = 1000
CODE_UNAUTHORIZED = 401
CODE_NOT_FOUND = 404
CODE_SERVER_BUSY = 500


class ZcjError(Exception):
    """基础异常。"""


class ZcjAuthError(ZcjError):
    """认证失败（账号/密码错误或 token 失效）。"""


class ZcjApiError(ZcjError):
    """接口返回非预期结果。"""


def _nonce() -> str:
    """生成 8~32 位的 nonceStr。"""
    return str(int(time.time() * 1000))


def _mock_sign(nonce: str) -> str:
    """服务端只做非空 + 长度校验，这里生成稳定的 32 位假签名。"""
    return hashlib.md5(nonce.encode()).hexdigest().upper()


class ZcjApiClient:
    """68宠物云端 API。"""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        phone: str,
        password: str,
    ) -> None:
        self._session = session
        self._phone = phone
        self._password = password
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._refresh_margin: float = float(TOKEN_REFRESH_MARGIN)
        self._lock = asyncio.Lock()
        self._auth_failures = 0
        self._auth_retry_at = 0.0

    # ------------------------------------------------------------------ 基础

    def _headers(self, authorized: bool = True) -> dict[str, str]:
        headers = {
            "Accept": "application/json;charset=UTF-8",
            "Content-Type": "application/json",
            "deviceType": "iOS",
            "version": APP_VERSION,
            "versionCode": APP_VERSION_CODE,
            "requestSource": "0",
            "User-Agent": USER_AGENT,
        }
        if authorized and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    @staticmethod
    def _envelope(payload: dict[str, Any] | None) -> dict[str, Any]:
        nonce = _nonce()
        body: dict[str, Any] = {"nonceStr": nonce, "sign": _mock_sign(nonce)}
        if payload:
            body.update(payload)
        return body

    async def _raw(
        self,
        service: str,
        path: str,
        payload: dict[str, Any] | None = None,
        method: str = "POST",
        authorized: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{API_BASE}/{service}{path}"
        headers = self._headers(authorized)
        if extra_headers:
            headers.update(extra_headers)
        # 登录接口同样要求 nonceStr + sign，只是不带 Bearer 头
        data = self._envelope(payload)
        try:
            async with self._session.request(
                method, url, json=data, headers=headers, timeout=aiohttp.ClientTimeout(total=25)
            ) as resp:
                text = await resp.text()
                try:
                    body: dict[str, Any] = json.loads(text)
                except ValueError as err:
                    # 网关层直接返回 401（非 JSON）时，转成业务 401 让上层触发重登
                    if resp.status == CODE_UNAUTHORIZED:
                        return {"code": CODE_UNAUTHORIZED, "msg": "HTTP 401"}
                    raise ZcjApiError(
                        f"{path} 返回非 JSON（HTTP {resp.status}）：{text[:160]}"
                    ) from err
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as err:
            # 网络层异常统一转成 ZcjApiError，上层只需处理 ZcjError 一种异常
            raise ZcjApiError(f"{path} 网络请求失败：{err!r}") from err
        return body

    # ------------------------------------------------------------------ 认证

    @property
    def token_seconds_left(self) -> int | None:
        """距服务端过期还有多少秒；没有 token 时返回 None。"""
        if not self._token or not self._expires_at:
            return None
        return int(self._expires_at - time.time())

    @property
    def token_expired(self) -> bool:
        """真正失效：没有 token，或者已过服务端有效期。"""
        if not self._token or not self._expires_at:
            return True
        return time.time() >= self._expires_at

    @property
    def token_in_refresh_window(self) -> bool:
        """已进入提前续期窗口（但还没真失效）。"""
        if self.token_expired:
            return False
        return time.time() >= self._expires_at - self._refresh_margin

    @property
    def token_valid(self) -> bool:
        """处于安全区，本次请求不需要续期。"""
        return not self.token_expired and not self.token_in_refresh_window

    @property
    def auth_cooldown_left(self) -> int:
        """登录冷却剩余秒数，0 表示不在冷却中。"""
        if self._auth_retry_at <= 0:
            return 0
        return max(0, int(self._auth_retry_at - time.time()))

    @property
    def token_status(self) -> dict[str, Any]:
        """给实体用的状态快照。"""
        return {
            "has_token": bool(self._token),
            "expires_at": self._expires_at or None,
            "seconds_left": self.token_seconds_left,
            "expired": self.token_expired,
            "refresh_due": self.token_in_refresh_window,
            "refresh_margin": int(self._refresh_margin),
            "auth_failures": self._auth_failures,
            "retry_in": self.auth_cooldown_left,
        }

    async def async_login(self) -> None:
        """用手机号 + 密码换取新的 JWT（对外接口，内部加锁）。"""
        async with self._lock:
            await self._async_login_locked()

    async def _async_login_locked(self) -> None:
        """实际登录。调用方必须已持有 self._lock。

        连续失败时做指数退避：60s → 120s → 240s → 480s → 600s 封顶。
        避免密码被改后每个轮询周期都去撞登录接口触发风控。
        """
        now = time.time()
        if now < self._auth_retry_at:
            raise ZcjAuthError(
                f"登录失败冷却中，{int(self._auth_retry_at - now)} 秒后再试"
            )

        try:
            basic = base64.b64encode(
                f"{self._phone}:{self._password}".encode()
            ).decode()
            body = await self._raw(
                "zcb-ucenter",
                "/app/user/login",
                {"cityId": 0},
                authorized=False,
                extra_headers={"Authorization": f"Basic {basic}"},
            )
            if body.get("code") != CODE_OK:
                raise ZcjAuthError(
                    f"登录失败：{body.get('msg') or body.get('code')}"
                )
            data = body.get("data") or {}
            token = data.get("token")
            if not token:
                raise ZcjAuthError("登录响应里没有 token")
        except (ZcjAuthError, ZcjApiError) as err:
            self._auth_failures += 1
            delay = min(600, 60 * 2 ** (self._auth_failures - 1))
            self._auth_retry_at = time.time() + delay
            _LOGGER.warning(
                "68宠物登录失败（连续 %s 次）：%s；%s 秒内不再尝试",
                self._auth_failures,
                err,
                delay,
            )
            raise

        self._token = token
        expires_ms = data.get("expirationTime") or 0
        if expires_ms:
            self._expires_at = expires_ms / 1000
        else:
            # 服务端没给到期时间，按 1 天兜底
            self._expires_at = time.time() + 86400
        # 提前量取「1 天」与「TTL 的 1/10」中的较小值：
        # 正常 30 天 TTL 下就是 1 天；万一服务端哪天把 TTL 压短，也不会永远停在“该续期”状态里反复登录
        ttl = max(0.0, self._expires_at - time.time())
        self._refresh_margin = min(float(TOKEN_REFRESH_MARGIN), ttl / 10)
        self._auth_failures = 0
        self._auth_retry_at = 0.0
        _LOGGER.debug(
            "68宠物登录成功，token 剩余 %s 秒，提前 %s 秒续期",
            int(ttl),
            int(self._refresh_margin),
        )

    async def _ensure_token(self) -> None:
        if self.token_valid:
            return

        async with self._lock:
            # 等锁期间可能已被其他协程刷新
            if self.token_valid:
                return

            if self.token_expired:
                # 没得选，只能换新的；失败就让上层抛 ZcjAuthError
                await self._async_login_locked()
                return

            # 提前续期窗口：换新成功最好，换不到也别把还能用的旧会话搞失效。
            # 否则密码一改、或者网络抖一下，明明还有一天有效期的 token 会立刻不可用。
            try:
                await self._async_login_locked()
            except ZcjError as err:
                _LOGGER.debug(
                    "提前续期失败，继续使用旧 token（剩余 %s 秒）：%s",
                    self.token_seconds_left,
                    err,
                )

    async def async_call(
        self,
        service: str,
        path: str,
        payload: dict[str, Any] | None = None,
        method: str = "POST",
    ) -> Any:
        """带自动续期的业务调用，返回 data 字段。"""
        await self._ensure_token()
        body = await self._raw(service, path, payload, method)

        if body.get("code") == CODE_UNAUTHORIZED:
            # 并发场景下两个请求会同时拿到 401。只有 token 仍是失效的那个时才重新登录，
            # 否则第二个请求会把第一个刚换来的 token 又顶掉。
            stale_token = self._token
            async with self._lock:
                if self._token == stale_token:
                    _LOGGER.debug("%s 返回 401，重新登录后重试", path)
                    self._token = None
                    await self._async_login_locked()
            body = await self._raw(service, path, payload, method)
            if body.get("code") == CODE_UNAUTHORIZED:
                raise ZcjAuthError(f"{path} 重新登录后仍返回 401")

        code = body.get("code")
        if code != CODE_OK:
            raise ZcjApiError(f"{path} 失败：[{code}] {body.get('msg')}")
        return body.get("data")

    # -------------------------------------------------------------- 业务接口

    async def async_get_user_info(self) -> dict[str, Any]:
        return await self.async_call("zcb-ucenter", "/app/user/info")

    async def async_get_pets(self) -> list[dict[str, Any]]:
        data = await self.async_call("zcb-business", "/app/pet/list")
        return data or []

    async def async_get_motion(self, pet_no: str, card_no: str) -> dict[str, Any] | None:
        """当日步数 / 卡路里 / 里程 / 电量 / 周榜。"""
        return await self.async_call(
            "zcb-business",
            "/app/walk_pet/index_motion_count",
            {"petNo": pet_no, "cardNo": card_no},
        )

    async def async_get_location(self, pet_no: str, card_no: str) -> dict[str, Any] | None:
        """实时定位。mode=0 只取最新一条，最省流量。"""
        return await self.async_call(
            "zcb-business",
            "/app/pet_locate/realtime",
            {"mode": 0, "petNo": pet_no, "cardNo": card_no},
        )

    async def async_get_footprint(
        self, pet_no: str, card_no: str, yymmdd: str
    ) -> list[dict[str, Any]]:
        """按日期取历史轨迹点。yymmdd 形如 261025。"""
        data = await self.async_call(
            "zcb-business",
            "/app/pet_locate/footprint",
            {"petNo": pet_no, "cardNo": card_no, "date": yymmdd},
        )
        return data or []

    async def async_refresh_location(self, pet_no: str, card_no: str) -> None:
        """要求定位器立即上报一次位置。"""
        await self.async_call(
            "zcb-business",
            "/app/pet_locate/refresh",
            {"petNo": pet_no, "cardNo": card_no},
        )

    async def async_get_unread_count(self) -> int:
        data = await self.async_call("zcb-business", "/app/message/not_read")
        return int(data or 0)
