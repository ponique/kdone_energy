"""경동원 홈네트워크 에너지 사용량 (KDOne Energy)."""
from __future__ import annotations

import logging
from datetime import date

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import CATEGORIES, KDOneAuthError, KDOneClient, KDOneError, KDOneNeedCertification
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_APP_CERTIFY,
    CONF_COMPLEX,
    CONF_REFRESH_TOKEN,
    CONF_START_MONTH,
    DOMAIN,
    UPDATE_TIMES,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]


class KDOneCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: KDOneClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,  # 주기 조회 없음. UPDATE_TIMES 시각에만 조회(async_setup_entry)
        )
        self.entry = entry
        self.client = client
        self._active: list[str] | None = None
        self._months: dict[str, tuple] = {}
        self._last_total: dict[str, float] = {}
        start = entry.data.get(CONF_START_MONTH)
        self._start: date | None = date.fromisoformat(f"{start}-01") if start else None

    async def _async_update_data(self) -> dict:
        data: dict = {}
        # 서버 날짜는 KST. 컨테이너 시스템 시간대(UTC일 수 있음)가 아니라 HA 설정 시간대 기준 날짜를 쓴다.
        today = dt_util.now().date()
        # 첫 조회에서 오류난 항목(미설치 등)은 이후 호출하지 않는다(재로드 시 재판정).
        cats = self._active if self._active is not None else CATEGORIES
        try:
            for cat in cats:
                # Month(지난달·월별) 조회는 하루 1회만. 나머지 주기는 Day 1회 호출.
                cache = self._months.get(cat)
                cached = cache[1] if cache and cache[0] == today else None
                try:
                    usage = await self.client.get_usage(cat, today, cached, self._start)
                except KDOneError as err:
                    if isinstance(err, KDOneAuthError):
                        raise
                    _LOGGER.debug("%s 조회 실패: %s", cat, err)
                    data[cat] = None
                    continue
                if cached is None:
                    self._months[cat] = (today, usage["months"])
                # 누적 센서는 total_increasing. 서버가 과거 값을 조금 낮게 고치면 HA가 리셋으로
                # 보고 이중 집계하므로, 실행 중에는 직전 값 아래로 내려가지 않게 한다.
                prev = self._last_total.get(cat)
                if usage["total"] is not None:
                    if prev is not None and usage["total"] < prev:
                        _LOGGER.warning("%s 누적값 감소 무시: %s -> %s", cat, prev, usage["total"])
                        usage["total"] = prev
                    self._last_total[cat] = usage["total"]
                data[cat] = usage
        except (KDOneAuthError, KDOneNeedCertification) as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err
        if self._active is None:
            # 오류 항목과, 전 기간 0뿐인 항목(이 집의 냉방 등 미계량)은 센서·호출 대상에서 뺀다.
            for cat, u in data.items():
                if u is not None and not any(
                    v for v in (*u["months"].values(), *u["days"].values())
                ):
                    data[cat] = None
            self._active = [c for c in CATEGORIES if data.get(c) is not None]
        self._persist_tokens()
        return data

    def _persist_tokens(self) -> None:
        c = self.client
        if (
            self.entry.data.get(CONF_ACCESS_TOKEN) != c.access_token
            or self.entry.data.get(CONF_REFRESH_TOKEN) != c.refresh_token
        ):
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={
                    **self.entry.data,
                    CONF_ACCESS_TOKEN: c.access_token,
                    CONF_REFRESH_TOKEN: c.refresh_token,
                },
            )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    d = entry.data
    client = KDOneClient(
        async_get_clientsession(hass),
        d[CONF_USERNAME],
        d[CONF_PASSWORD],
        complex_id=d[CONF_COMPLEX],
        app_certify=d.get(CONF_APP_CERTIFY),
        access_token=d.get(CONF_ACCESS_TOKEN),
        refresh_token=d.get(CONF_REFRESH_TOKEN),
    )
    coordinator = KDOneCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    async def _scheduled_refresh(_now) -> None:
        await coordinator.async_refresh()

    for hour, minute in UPDATE_TIMES:
        # HA 설정 시간대(Asia/Seoul) 기준
        entry.async_on_unload(
            async_track_time_change(hass, _scheduled_refresh, hour=hour, minute=minute, second=0)
        )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok
