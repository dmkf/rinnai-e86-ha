"""Constants for rinnai_e86."""
DOMAIN = "rinnai_e86"
SIGNAL_STATE = f"{DOMAIN}_state_updated"

UPSTREAM_HOST = "mqtt.rinnai.com.cn"  # Rinnai cloud MQTT (CN)
UPSTREAM_PORT = 8883

LISTEN_PORT = 8883  # local fake-cloud TLS port
POLL_SECONDS = 60
