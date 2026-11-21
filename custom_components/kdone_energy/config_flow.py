"""Config flow: 아이디/비번 -> 단지 선택 -> (필요 시) 월패드 인증번호 입력."""
from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import KDOneClient, KDOneError, KDOneNeedCertification
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_APP_CERTIFY,
    CONF_CERT_NUMBER,
    CONF_COMPLEX,
    CONF_REFRESH_TOKEN,
    CONF_START_MONTH,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class KDOneConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._client: KDOneClient | None = None
        self._complexes: list[dict[str, Any]] = []
        self._reauth_entry = None
        self._start_month: str | None = None

    # 1) 계정 ------------------------------------------------------------
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            start = (user_input.get(CONF_START_MONTH) or "").strip()
            if start and not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", start):
                errors[CONF_START_MONTH] = "invalid_month"
            self._start_month = start or None
        if user_input is not None and not errors:
            self._client = KDOneClient(
                async_get_clientsession(self.hass),
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                # 이미 월패드 인증을 마친 번호(CLI 등)가 있으면 재사용 -> 인증번호 재요청 없이 로그인.
                # 실패하면 complex 단계에서 월패드 인증번호를 새로 요청한다.
                app_certify=(user_input.get(CONF_APP_CERTIFY) or "").strip() or None,
            )
            try:
                self._complexes = await self._client.get_complexes()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("단지 목록 조회 실패")
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_complex()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Optional(CONF_APP_CERTIFY): str,
                    vol.Optional(CONF_START_MONTH): str,
                }
            ),
            errors=errors,
        )

    # 2) 단지 ------------------------------------------------------------
    async def async_step_complex(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        assert self._client
        if user_input is not None:
            self._client.complex_id = user_input[CONF_COMPLEX]
            await self.async_set_unique_id(f"{self._client.complex_id}_{self._client.username}")
            self._abort_if_unique_id_configured()
            try:
                await self._client.login()
                return self._finish()
            except KDOneNeedCertification:
                pass
            except KDOneError as err:
                _LOGGER.warning("로그인 실패: %s", err)
            except (aiohttp.ClientError, TimeoutError):
                _LOGGER.exception("로그인 요청 실패")
                errors["base"] = "cannot_connect"
            if not errors:
                try:
                    await self._client.request_certification_code()
                    return await self.async_step_certify()
                except KDOneError as err:
                    _LOGGER.warning("인증번호 요청 실패: %s", err)
                    errors["base"] = "invalid_auth"
                except (aiohttp.ClientError, TimeoutError):
                    _LOGGER.exception("인증번호 요청 실패")
                    errors["base"] = "cannot_connect"
        options = [
            SelectOptionDict(
                value=str(c.get("Complex")),
                label=f"{c.get('Complex_Name', '')} ({c.get('Complex_Addr', '')})",
            )
            for c in self._complexes
        ]
        return self.async_show_form(
            step_id="complex",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_COMPLEX): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                    )
                }
            ),
            errors=errors,
        )

    # 3) 월패드 인증번호 ----------------------------------------------------
    async def async_step_certify(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        assert self._client
        if user_input is not None:
            try:
                await self._client.certify(user_input[CONF_CERT_NUMBER].strip())
                return self._finish()
            except (KDOneError, KDOneNeedCertification) as err:
                _LOGGER.warning("인증 실패: %s", err)
                errors["base"] = "invalid_code"
            except (aiohttp.ClientError, TimeoutError):
                _LOGGER.exception("인증 요청 실패")
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="certify",
            data_schema=vol.Schema({vol.Required(CONF_CERT_NUMBER): str}),
            errors=errors,
        )

    # 재인증(토큰/기기인증 만료 시) -------------------------------------------
    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self._get_reauth_entry()
        self._client = KDOneClient(
            async_get_clientsession(self.hass),
            entry_data[CONF_USERNAME],
            entry_data[CONF_PASSWORD],
            complex_id=entry_data[CONF_COMPLEX],
        )
        try:
            await self._client.request_certification_code()
        except KDOneError:
            return self.async_abort(reason="invalid_auth")
        except (aiohttp.ClientError, TimeoutError):
            return self.async_abort(reason="cannot_connect")
        return await self.async_step_certify()

    def _finish(self) -> ConfigFlowResult:
        c = self._client
        assert c
        data = {
            CONF_USERNAME: c.username,
            CONF_PASSWORD: c.password,
            CONF_COMPLEX: c.complex_id,
            CONF_APP_CERTIFY: c.app_certify,
            CONF_ACCESS_TOKEN: c.access_token,
            CONF_REFRESH_TOKEN: c.refresh_token,
        }
        if self._reauth_entry:
            data[CONF_START_MONTH] = self._reauth_entry.data.get(CONF_START_MONTH)
            return self.async_update_reload_and_abort(self._reauth_entry, data=data)
        data[CONF_START_MONTH] = self._start_month
        name = next(
            (x.get("Complex_Name") for x in self._complexes if str(x.get("Complex")) == c.complex_id),
            c.complex_id,
        )
        return self.async_create_entry(title=f"{name} ({c.username})", data=data)
