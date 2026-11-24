"""사용량 센서: 카테고리별 오늘 / 이번달 / 지난달."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import KDOneCoordinator
from .api import CATEGORIES
from .const import CATEGORY_NAMES, DOMAIN

ENERGY_UNITS = {"Wh", "kWh", "MWh", "GJ", "MJ", "Gcal", "Mcal", "kcal"}
VOLUME_UNITS = {"m³", "L"}


def normalize_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    u = unit.strip()
    if u.lower() in {"m3", "㎥", "m^3", "m³", "ton", "톤"}:
        return "m³"  # 수도/온수 '톤'은 1톤 = 1m³
    if u.lower() == "kwh":
        return "kWh"
    if u.lower() == "mwh":
        return "MWh"
    if u.lower() in {"gcal", "g cal"}:
        return "Gcal"
    if u.lower() == "mcal":
        return "Mcal"
    if u.lower() in {"l", "ℓ", "liter"}:
        return "L"
    return u


@dataclass(frozen=True)
class Period:
    key: str
    label: str
    state_class: SensorStateClass | None


PERIODS = [
    # 데이터 시작 월 이후 누적: 리셋 없음 -> 에너지 대시보드는 이 센서를 쓴다
    Period("total", "누적", SensorStateClass.TOTAL_INCREASING),
    # 이번달 누적: 매월 0으로 리셋 (표시용; 월말 마지막 조회 뒤 사용량은 빠질 수 있음)
    Period("this_month", "이번달", SensorStateClass.TOTAL_INCREASING),
    Period("today", "오늘", SensorStateClass.TOTAL_INCREASING),
    Period("last_month", "지난달", None),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: KDOneCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        KDOneUsageSensor(coordinator, entry, cat, p)
        for cat in CATEGORIES
        if coordinator.data.get(cat) is not None
        for p in PERIODS
    )


class KDOneUsageSensor(CoordinatorEntity[KDOneCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: KDOneCoordinator, entry: ConfigEntry, cat: str, period: Period) -> None:
        super().__init__(coordinator)
        self._cat = cat
        self._period = period
        self._attr_unique_id = f"{entry.unique_id}_{cat}_{period.key}"
        self._attr_name = f"{CATEGORY_NAMES[cat]} {period.label} 사용량"
        self._attr_state_class = period.state_class
        self._attr_icon = {
            "Electricity": "mdi:flash",
            "Gas": "mdi:fire",
            "Water": "mdi:water",
            "Hotwater": "mdi:water-boiler",
            "Heating": "mdi:radiator",
            "Cooling": "mdi:snowflake",
        }[cat]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="경동원(KD Navien)",
            model="홈네트워크 원격검침",
        )

    @property
    def _data(self) -> dict | None:
        return (self.coordinator.data or {}).get(self._cat)

    @property
    def native_unit_of_measurement(self) -> str | None:
        return normalize_unit((self._data or {}).get("unit"))

    @property
    def device_class(self) -> SensorDeviceClass | None:
        unit = self.native_unit_of_measurement
        if self._cat == "Gas":
            return SensorDeviceClass.GAS if unit in {"m³", "kWh", "MJ", "GJ"} else None
        if self._cat in ("Water", "Hotwater") and unit in VOLUME_UNITS:
            return SensorDeviceClass.WATER
        if unit in ENERGY_UNITS:
            return SensorDeviceClass.ENERGY
        return None

    @property
    def native_value(self) -> float | None:
        return (self._data or {}).get(self._period.key)

    @property
    def extra_state_attributes(self) -> dict | None:
        d = self._data
        if not d:
            return None
        if self._period.key == "this_month":
            return {"daily": d.get("days"), "raw_unit": d.get("unit")}
        if self._period.key == "last_month":
            return {"monthly": d.get("months"), "daily": d.get("prev_days"), "raw_unit": d.get("unit")}
        return {"raw_unit": d.get("unit")}
