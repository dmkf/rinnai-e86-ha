"""Config flow for rinnai_e86."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries

from .const import DOMAIN


def _schema(defaults: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required("topic_base", default=defaults.get("topic_base", "")): str,
        vol.Required("device_id", description="BLE/MAC序列号,如 289C...", default=defaults.get("device_id", "")): str,
        vol.Required("product_id", description="产品ID,如 0272xxxx", default=defaults.get("product_id", "")): str,
        vol.Required("voice_light", default=defaults.get("voice_light", "")): str,
        vol.Required("virtual_event", default=defaults.get("virtual_event", "")): str,
    })


class RinnaiE86FlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        if user_input is not None:
            return self.async_create_entry(title="林内E86热水器", data=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    def async_get_options_flow(config_entry):
        return RinnaiE86OptionsFlowHandler()


class RinnaiE86OptionsFlowHandler(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        defaults = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(defaults)
            .extend({vol.Required("bridge_cloud", default=defaults.get("bridge_cloud", True)): bool}),
        )
