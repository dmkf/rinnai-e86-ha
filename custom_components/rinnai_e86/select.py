"""选择器平台:零冷水循环模式(关闭/单次循环/全天循环)。"""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_STATE

MODE_LABELS = ["关闭", "单次循环"]  # 全天循环(2)不常用,仅当设备处于该状态时临时显示


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    hub = hass.data[DOMAIN]["hub"]

    class CycleModeSelect(SelectEntity):
        _attr_has_entity_name = True
        _attr_name = "零冷水循环模式"
        _attr_unique_id = "rinnai_e86_cycle"
        _attr_options = MODE_LABELS
        _attr_current_option = MODE_LABELS[0]
        _attr_translation_key = "cycle_mode"

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

        async def async_select_option(self, option: str) -> None:
            mapping = {**{m: i for i, m in enumerate(MODE_LABELS)}, "全天循环": 2}
            if option not in mapping:
                raise ValueError(f"未知模式: {option}")
            await hub.set_cycle(mapping[option])

        async def async_added_to_hass(self):
            self.async_on_remove(
                async_dispatcher_connect(hass, SIGNAL_STATE, self._refresh)
            )

        @callback
        def _refresh(self):
            cm = int(hub.friendly().get("cycle_mode") or 0)
            label = MODE_LABELS[cm] if cm < len(MODE_LABELS) else "全天循环"
            # 设备处于未列出的模式(如经App设了全天)时,临时补进选项如实显示
            opts = MODE_LABELS + ([] if label in MODE_LABELS else [label])
            if self._attr_current_option != label or self._attr_options != opts:
                self._attr_current_option = label
                self._attr_options = opts
                self.async_write_ha_state()

    async_add_entities([CycleModeSelect()])
