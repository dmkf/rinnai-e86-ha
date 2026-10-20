"""二进制传感器平台。"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_STATE

SENSORS = [
    BinarySensorEntityDescription(
        key="burning", name="燃烧中", device_class=BinarySensorDeviceClass.RUNNING
    ),
    BinarySensorEntityDescription(
        key="faucet", name="水龙头", device_class=BinarySensorDeviceClass.OPENING
    ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub = hass.data[DOMAIN]["hub"]

    class RinnaiBinarySensor(BinarySensorEntity):
        _attr_has_entity_name = True

        def __init__(self, desc):
            self.entity_description = desc
            self._attr_unique_id = f"rinnai_e86_{desc.key}"
            self._attr_is_on = None

        @property
        def device_info(self):
            return DeviceInfo(
                identifiers={(DOMAIN, "rinnai_e86")},
                name="林内E86热水器",
                manufacturer="Rinnai",
                model="RUS-E86 (0E86)",
                sw_version="WiFi模块V14",
            )

        @property
        def available(self):
            return hub.online

        async def async_added_to_hass(self):
            self.async_on_remove(
                async_dispatcher_connect(hass, SIGNAL_STATE, self._refresh)
            )

        @callback
        def _refresh(self):
            val = hub.friendly().get(self.entity_description.key)
            if self._attr_is_on != val:
                self._attr_is_on = bool(val)
                self.async_write_ha_state()

    async_add_entities([RinnaiBinarySensor(d) for d in SENSORS])
