"""Rinnai E86 热水器本地集成。

在 HA 进程内运行假云端(TLS MQTT broker):热水器 WiFi 模块直连本机 8883,
自动应答对时、周期驱动上报、下发控制;原生注册实体;内置小爱语音桥
(把指定灯实体的开/关映射为循环开/关)。
依赖网关 DNAT:热水器出站 8883 → 本机 8883。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import ssl
import time
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import event as ha_event
from homeassistant.helpers.dispatcher import async_dispatcher_connect, dispatcher_send

from .const import (
    DOMAIN,
    DEVICE_ID,
    PRODUCT_ID,
    SIGNAL_STATE,
    TOPIC_BASE,
    VIRTUAL_EVENT_DEFAULT,
    UPSTREAM_HOST,
    UPSTREAM_PORT,
    VOICE_LIGHT_DEFAULT,
)

_LOGGER = logging.getLogger(__name__)
PACKET_NAMES = {1: "CONNECT", 3: "PUBLISH", 4: "PUBACK", 8: "SUBSCRIBE",
               9: "SUBACK", 12: "PING", 13: "PINGRSP", 14: "DISCONN"}
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SELECT]
LISTEN_PORT = 8883
POLL_SECONDS = 60

# ---------- Web 面板 ----------

PANEL_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>林内 E86 热水器</title><style>
:root{--bg:#0f1419;--card:#1a222c;--line:#2a3646;--fg:#dce4ec;--dim:#8496a8;--acc:#4da3ff;--ok:#3fb950;--warn:#e3b341}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--fg);font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;padding:24px;max-width:860px;margin:auto}
h1{font-size:20px;display:flex;align-items:center;gap:10px;margin-bottom:18px}
.dot{width:10px;height:10px;border-radius:50%;background:#666;display:inline-block}
.dot.on{background:var(--ok);box-shadow:0 0 8px var(--ok)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.card h3{font-size:13px;color:var(--dim);font-weight:500;margin-bottom:10px}
.big{font-size:44px;font-weight:600}
.unit{font-size:18px;color:var(--dim)}
.row{display:flex;justify-content:space-between;padding:5px 0;font-size:14px}
.row .k{color:var(--dim)}
.badges{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.badge{padding:4px 10px;border-radius:14px;font-size:13px;background:#233040;color:var(--dim)}
.badge.on{background:#123522;color:var(--ok);border:1px solid #1d4b2e}
.badge.warn{background:#3a2f12;color:var(--warn)}
.seg{display:flex;gap:8px;margin-top:6px}
.seg button{flex:1;padding:12px 0;border-radius:8px;border:1px solid var(--line);background:#233040;color:var(--fg);font-size:15px;cursor:pointer}
.seg button.active{background:var(--acc);color:#04121f;border-color:var(--acc);font-weight:600}
button.refresh{padding:6px 16px;border-radius:8px;border:1px solid var(--line);background:#233040;color:var(--fg);cursor:pointer}
.foot{color:var(--dim);font-size:12px;margin-top:16px;text-align:center}
</style></head><body>
<h1><span class="dot" id="dot"></span>🚿 林内 E86 热水器 <span style="flex:1"></span>
<button class="refresh" onclick="post('query')">刷新状态</button></h1>
<div class="grid">
<div class="card"><h3>热水目标温度(面板设定)</h3>
<div class="big"><span id="temp">--</span><span class="unit">°C</span></div>
<div class="badges" id="badges"></div></div>
<div class="card"><h3>零冷水循环</h3>
<div class="seg">
<button id="m0" onclick="post('cycle',0)">关闭</button>
<button id="m1" onclick="post('cycle',1)">单次循环</button>
</div></div>
<div class="card"><h3>运行状态</h3><div id="run"></div></div>
<div class="card"><h3>累计统计</h3><div id="stats"></div></div>
<div class="card"><h3>设备信息</h3><div id="info"></div></div>
</div>
<div class="foot" id="foot">rinnai_e86 · HA 内置面板</div>
<script>
async function post(cmd,value){try{
await fetch('/api/rinnai_e86/cmd',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cmd,value})});
setTimeout(load,4500);}catch(e){}}
function row(k,v){return `<div class="row"><span class="k">${k}</span><span>${v}</span></div>`}
function ago(t){if(!t)return '--';const s=Math.floor(Date.now()/1000-t);return s<120?s+' 秒前':Math.floor(s/60)+' 分钟前'}
async function load(){
try{
const s=await(await fetch('/api/rinnai_e86/status')).json();
document.getElementById('dot').className='dot'+(s.online?' on':'');
document.getElementById('temp').textContent=s.temp_setting_c??'--';
const b=[];
if(s.burning)b.push('<span class="badge on">🔥 燃烧中</span>');
if(s.faucet)b.push('<span class="badge on">🚰 水龙头开</span>');
if(s.child_lock)b.push('<span class="badge warn">🔒 童锁</span>');
if(s.error)b.push('<span class="badge warn">⚠ 故障 '+s.error+'</span>');
document.getElementById('badges').innerHTML=b.join('')||'<span class="badge">待机</span>';
document.getElementById('m0').className=s.cycle_mode==0?'active':'';
document.getElementById('m1').className=s.cycle_mode==1?'active':'';
document.getElementById('run').innerHTML=
row('燃烧',s.burning?'燃烧中':'停止')+row('水龙头',s.faucet?'开':'关')+
row('运行模式',s.operation_mode??'--')+row('最后上报',ago(s.last_seen));
document.getElementById('stats').innerHTML=
row('燃烧次数',s.burn_count??'--')+row('累计燃烧',s.burn_min?s.burn_min+' 分':'--')+
row('累计通电',s.power_min?s.power_min+' 分':'--');
const i=s.identity||{};
document.getElementById('info').innerHTML=
row('型号','RUS-E86 ('+(i.id||'-')+')')+row('模块固件',i.wifisoftversion||'V14')+
row('序列号', s.serial || '-');
document.getElementById('foot').textContent=(s.online?'在线':'离线')+' · '+new Date().toLocaleTimeString();
}catch(e){document.getElementById('dot').className='dot';}}
load();setInterval(load,3000);
</script></body></html>"""

# ---------- MQTT 基础编解码 ----------


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _lp(b: bytes) -> bytes:
    return len(b).to_bytes(2, "big") + b


def _body_of(pkt: bytes) -> bytes:
    i = 1
    while pkt[i] & 0x80:
        i += 1
    return pkt[i + 1 :]


def _read_lp(buf: bytes, pos: int) -> tuple[bytes, int]:
    n = buf[pos] << 8 | buf[pos + 1]
    return buf[pos + 2 : pos + 2 + n], pos + 2 + n


def _split_packets(buf: bytearray) -> list[bytes]:
    """切出所有完整包,并从缓冲区头部消费掉。"""
    out = []
    i = 0
    while i < len(buf):
        j = i + 1
        mult, rl = 1, 0
        while True:
            if j >= len(buf):
                del buf[:i]
                return out
            b = buf[j]
            rl += (b & 0x7F) * mult
            mult *= 128
            j += 1
            if not b & 0x80:
                break
        if j + rl > len(buf):
            del buf[:i]
            return out
        out.append(bytes(buf[i : j + rl]))
        i = j + rl
    buf.clear()
    return out


def _publish0(topic: str, payload: bytes, retain: bool = False) -> bytes:
    body = _lp(topic.encode()) + payload
    return bytes([0x30 | (0x01 if retain else 0)]) + _varint(len(body)) + body


def _publish1(topic: str, payload: bytes, msgid: int) -> bytes:
    body = _lp(topic.encode()) + msgid.to_bytes(2, "big") + payload
    return bytes([0x32]) + _varint(len(body)) + body


def _hx(v, default=None):
    try:
        return int(str(v).replace(" ", ""), 16)
    except (TypeError, ValueError):
        return default


# ---------- 证书 ----------


def ensure_cert(hass: HomeAssistant) -> tuple[str, str]:
    d = Path(hass.config.path(DOMAIN))
    d.mkdir(parents=True, exist_ok=True)
    crt, key = d / "server.crt", d / "server.key"
    if crt.exists() and key.exists():
        return str(crt), str(key)
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "mqtt.rinnai.com.cn")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(k.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("mqtt.rinnai.com.cn"),
                    x509.DNSName("wifiboiler_s1.rinnai.com.cn"),
                ]
            ),
            critical=False,
        )
        .sign(k, hashes.SHA256())
    )
    key.write_bytes(
        k.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(crt), str(key)


# ---------- 核心 Hub ----------


class RinnaiHub:
    """热水器假云端 + 状态源(可选桥接真云让林内App可用)。"""

    def __init__(self, hass: HomeAssistant, bridge_cloud: bool = True,
                 topic_base: str = "", device_id: str = "",
                 product_id: str = "") -> None:
        self.hass = hass
        self.bridge_cloud = bridge_cloud
        self.topic_base = topic_base.rstrip("/")
        self.device_id = device_id
        self.product_id = product_id
        self.writer: asyncio.StreamWriter | None = None
        self._sessions: dict = {}  # device_writer -> cloud_writer(成对清理)
        self.raw: dict = {}
        self.stats: dict = {}
        self.identity: dict = {}
        self.last_seen = 0.0
        self.msgid = 0x200
        self._cmd_lock = asyncio.Lock()
        self._query_event: asyncio.Event | None = None
        # 语音桥状态
        self.mirrored = False
        self.cycle_seen = False

    # ---- 对外 ----
    @property
    def online(self) -> bool:
        return bool(self.raw) and (time.time() - self.last_seen) < 120

    def friendly(self) -> dict:
        s, st = self.raw, self.stats
        return {
            "temp_setting_c": _hx(s.get("hotWaterTempSetting")),
            "burning": s.get("burningState") not in (None, "0"),
            "faucet": s.get("faucetNotCloseSign") not in (None, "0"),
            "serial": self.device_id,
            "cycle_mode": (
                _hx(s.get("cycleModeSetting"), 0)
                or (1 if s.get("temporaryCycleInsulationSetting") not in (None, "0") else 0)
            ),  # App走temporaryCycle通道,本地走cycleModeSetting,取或呈现
            "child_lock": s.get("childLock") not in (None, "0"),
            "priority": _hx(s.get("priority")),
            "error": _hx(s.get("errorCode"), 0),
            "operation_mode": s.get("operationMode"),
            "burn_count": _hx(st.get("hotWaterBurningTimes")),
            "gas_raw": _hx(st.get("gasConsumption")),
            "power_min": _hx(st.get("totalPowerSupplyTime")),
            "burn_min": _hx(st.get("totalHotWaterBurningTime")),
        }

    async def start(self) -> None:
        crt, key = await self.hass.async_add_executor_job(ensure_cert, self.hass)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")  # 模块只会 TLS1.2 CBC 套件(如 AES256-SHA256)
        await self.hass.async_add_executor_job(ctx.load_cert_chain, crt, key)
        self.server = await asyncio.start_server(
            self._handle, "0.0.0.0", LISTEN_PORT, ssl=ctx
        )
        self.hass.async_create_background_task(self._poll_loop(), "rinnai_e86 poll")
        _LOGGER.info("Rinnai E86 假云端监听 0.0.0.0:%d", LISTEN_PORT)

    async def stop(self) -> None:
        if getattr(self, "server", None):
            self.server.close()
            await self.server.wait_closed()

    def _next_id(self) -> int:
        self.msgid = (self.msgid + 1) & 0xFFFF or 1
        return self.msgid

    def _send(self, pkt: bytes) -> None:
        if self.writer is None:
            raise RuntimeError("设备未连接")
        self.writer.write(pkt)

    async def query(self) -> None:
        payload = json.dumps(
            {"deviceid": self.device_id, "id": self.product_id, "ptn": "JA0"}
        ).encode()
        self._send(_publish1(f"{self.topic_base}/get/", payload, self._next_id()))

    async def set_field(self, field: str, data_hex: str) -> None:
        await self._set_fields([(field, data_hex)])

    async def _set_fields(self, items: list[tuple[str, str]]) -> None:
        """带 get/ 前导舞步的 set 下发(实测裸发会触发模块重置)。"""
        async with self._cmd_lock:
            ev = asyncio.Event()
            self._query_event = ev
            try:
                await self.query()
                try:  # get 应答即发 set,不等固定延时
                    await asyncio.wait_for(ev.wait(), timeout=2.5)
                except asyncio.TimeoutError:
                    pass
            finally:
                self._query_event = None
            payload = json.dumps(
                {
                    "code": "03E9",
                    "enl": [{"data": dh, "id": f} for f, dh in items],
                    "id": self.product_id,
                    "ptn": "J00",
                    "sum": str(len(items)),
                }
            ).encode()
            self._send(_publish1(f"{self.topic_base}/set/", payload, self._next_id()))
            _LOGGER.info("下发 set %s", items)

    async def safe_set_cycle(self, value: int) -> None:
        """闭环下发:发送后校验设备回报,未生效则重试(防僵尸写入无声失败)。"""
        for attempt in range(1, 4):
            try:
                await self.set_cycle(value)
            except RuntimeError as err:
                _LOGGER.warning("set_cycle(%d) 第%d次发送失败: %s", value, attempt, err)
            for _ in range(6):  # 最多等9秒看状态回报
                await asyncio.sleep(1.5)
                if self.friendly().get("cycle_mode") == value:
                    _LOGGER.info("set_cycle(%d) 生效(第%d次)", value, attempt)
                    return
            _LOGGER.warning(
                "set_cycle(%d) 第%d次未见生效(当前=%s),重试",
                value, attempt, self.friendly().get("cycle_mode"))
        _LOGGER.error("set_cycle(%d) 三次均未生效", value)

    async def set_cycle(self, value: int) -> None:
        if value == 0:  # 关闭时顺序清两条通道(设备不认合包,只认单字段)
            await self._set_fields([("cycleModeSetting", "00")])
            await asyncio.sleep(1.0)
            await self._set_fields([("temporaryCycleInsulationSetting", "00")])
        elif value == 1:  # 单次循环走App同款通道(temporaryCycle),App/本地显示天然同步
            await self.set_field("temporaryCycleInsulationSetting", "01")
        else:  # 全天循环(极少用)仍走 cycleMode 通道
            await self.set_field("cycleModeSetting", f"{value:02X}")

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(POLL_SECONDS)
            stale = time.time() - self.last_seen if self.last_seen else 0
            if self.writer is not None and self.last_seen and stale > 180:
                _LOGGER.warning("状态已 %ds 未更新,强制重建设备会话", int(stale))
                try:
                    self.writer.close()
                except OSError:
                    pass
                continue
            try:
                await self.query()
            except RuntimeError:
                pass
            except Exception:  # noqa: BLE001
                _LOGGER.exception("查询失败")

    async def _open_bridge(self):
        """建立到真云端的桥,返回 (cloud_reader, cloud_writer) 或 None。"""
        try:
            def _cloud_ctx() -> ssl.SSLContext:
                cctx = ssl.create_default_context()
                cctx.check_hostname = False
                cctx.verify_mode = ssl.CERT_NONE
                return cctx

            cctx = await self.hass.async_add_executor_job(_cloud_ctx)
            creader, cwriter = await asyncio.wait_for(
                asyncio.open_connection(
                    UPSTREAM_HOST, UPSTREAM_PORT, ssl=cctx,
                    server_hostname=UPSTREAM_HOST),
                timeout=10)
            return creader, cwriter
        except (OSError, asyncio.TimeoutError) as err:
            _LOGGER.warning("桥接真云端失败: %s", err)
            return None

    # ---- 设备连接处理 ----
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
                      ) -> None:
        peer = writer.get_extra_info("peername")
        # 模块会并发开一条静默探测连接:等首字节,不发数据的直接丢弃
        try:
            first = await asyncio.wait_for(reader.readexactly(1), timeout=12)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError,
                ConnectionError, ssl.SSLError, OSError):
            writer.close()
            return
        _LOGGER.info("设备接入 %s", peer)
        # 接管:关闭所有旧会话(设备侧+其云端桥,防同clientId互踢/泄漏)
        for dw, cw in list(self._sessions.items()):
            try:
                dw.close()
            except OSError:
                pass
            if cw is not None:
                try:
                    cw.close()
                except OSError:
                    pass
        self._sessions[writer] = None
        self.writer = writer
        def _attach_bridge(creader, cwriter) -> None:
            self._sessions[writer] = cwriter
            self.hass.async_create_background_task(
                self._cloud_to_device(creader, writer, cwriter),
                "rinnai_e86 cloud relay")
            _LOGGER.info("已桥接真云端 %s:%d(林内App远程可用)",
                         UPSTREAM_HOST, UPSTREAM_PORT)

        if self.bridge_cloud:
            res = await self._open_bridge()
            if res:
                _attach_bridge(*res)
            else:  # 建桥失败:后台持续重试,直到本会话结束
                async def _bridge_retry() -> None:
                    while self.writer is writer and self.bridge_cloud:
                        await asyncio.sleep(25)
                        if self._sessions.get(writer) is not None:
                            return
                        res = await self._open_bridge()
                        if res:
                            _attach_bridge(*res)
                            return
                self.hass.async_create_background_task(
                    _bridge_retry(), "rinnai_e86 bridge retry")
        buf = bytearray(first)
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                buf.extend(data)
                for pkt in _split_packets(buf):
                    await self._on_packet(pkt, writer)
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            my_bridge = self._sessions.pop(writer, None)
            if self.writer is writer:
                self.writer = None
                self.raw = {}
                self._broadcast()
            if my_bridge is not None:  # 只关本会话自己的桥
                my_bridge.close()
            writer.close()
            _LOGGER.info("设备断开 %s", peer)

    async def _cloud_to_device(self, creader, writer, cloud_writer) -> None:
        """真云端→设备:原样转发;断开时关闭云端socket防泄漏。"""
        try:
            while True:
                data = await creader.read(4096)
                if not data:
                    break
                _LOGGER.debug("云→ %s", data[:96].hex())
                # 设备固件只在 get→set 节奏窗口内执行 set:
                # 云端裸发 set 时,代它补一条 get 舞步再转发
                if (
                    data[0] >> 4 == 3
                    and b"/set/" in data[:90]
                    and b'"code"' in data[:260]
                    and self.writer is writer
                ):
                    _LOGGER.info("云端 set 命令,代补 get 舞步")
                    try:
                        await self.query()
                        await asyncio.sleep(1.5)
                    except RuntimeError:
                        pass
                writer.write(data)
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            try:
                cloud_writer.close()
            except OSError:
                pass
            _LOGGER.info("真云端连接关闭")

    async def _on_packet(self, pkt: bytes, session_writer) -> None:
        cloud_writer = self._sessions.get(session_writer)
        if cloud_writer is not None:  # 桥接模式:转发给真云,本地只解析
            _LOGGER.debug("→云 [%s] %s", PACKET_NAMES.get(pkt[0] >> 4, "?"),
                          pkt[:48].hex())
            cloud_writer.write(pkt)
            await self._on_uplink_pkt(pkt)
            return
        await self._local_broker_pkt(pkt)

    async def _local_broker_pkt(self, pkt: bytes) -> None:
        t = pkt[0] >> 4
        if t == 1:  # CONNECT
            writer_pkt = b"\x20\x02\x00\x00"
            self.writer.write(writer_pkt)
        elif t == 8:  # SUBSCRIBE → 授权 QoS1(照抄真云行为)
            body = _body_of(pkt)
            mid = body[0] << 8 | body[1]
            filters = []
            p = 2
            while p < len(body):
                f, p = _read_lp(body, p)
                filters.append(f.decode())
                p += 1
            self.writer.write(
                bytes([0x90])
                + _varint(2 + len(filters))
                + mid.to_bytes(2, "big")
                + bytes([0x01] * len(filters))
            )
            _LOGGER.info("设备订阅 %s", filters)
        elif t == 3:  # PUBLISH(设备上行)
            body = _body_of(pkt)
            topic_b, p = _read_lp(body, 0)
            qos = (pkt[0] >> 1) & 3
            if qos:
                mid = body[p] << 8 | body[p + 1]
                payload = body[p + 2 :]
                self.writer.write(b"\x40\x02" + mid.to_bytes(2, "big"))
            else:
                payload = body[p:]
            await self._on_uplink(topic_b.decode(), payload)
        elif t == 12:  # PINGREQ
            self.writer.write(b"\xd0\x00")
        elif t == 14:  # DISCONNECT
            self.writer.close()

    async def _on_uplink_pkt(self, pkt: bytes) -> None:
        """桥接模式:只解析上行报文,不产生任何应答(真云负责)。"""
        if pkt[0] >> 4 != 3:
            return
        body = _body_of(pkt)
        topic_b, p = _read_lp(body, 0)
        qos = (pkt[0] >> 1) & 3
        payload = body[p + 2 :] if qos else body[p:]
        await self._on_uplink(topic_b.decode(), payload)

    async def _on_uplink(self, topic: str, payload: bytes) -> None:
        try:
            obj = json.loads(payload.decode("utf-8", "replace"))
        except ValueError:
            return
        ptn = obj.get("ptn")
        if (
            not self.bridge_cloud
            and topic.endswith("/stg/")
            and ptn == "JA5"
            and "epoch" not in obj
        ):
            answer = json.dumps(
                {"ptn": "JA5", "epoch": int(time.time()), "timezone": "+08:00"}
            ).encode()
            self._send(_publish0(f"{self.topic_base}/set/", answer))
            return
        if ptn == "J00" and "enl" in obj:
            self.raw = {e.get("id", "?"): e.get("data", "") for e in obj["enl"]}
            self.last_seen = time.time()
        elif ptn == "J05" and "egy" in obj:
            self.stats = {e.get("label", "?"): e.get("value", "") for e in obj["egy"]}
            self.last_seen = time.time()
        elif ptn == "JA0":
            self.identity = obj
            self.last_seen = time.time()
            if self._query_event is not None:  # 唤醒等待 get 应答的舞步
                self._query_event.set()
        else:
            self.last_seen = time.time()
        await self._voice_sync()
        self._broadcast()

    # ---- 语音桥(灯=镜像开关) ----
    async def _voice_sync(self) -> None:
        cm = _hx(self.raw.get("cycleModeSetting"))
        if cm == 1:
            self.cycle_seen = True
        if self.mirrored and self.cycle_seen and cm == 0:
            self.mirrored = False
            self.cycle_seen = False
            _LOGGER.info("循环结束 → 自动熄灯")
            await self.hass.services.async_call(
                "light", "turn_off", {"entity_id": self.hass.data[DOMAIN]["voice_light"]}
            )

    @callback
    def voice_light_changed(self, old, new) -> None:
        """灯实体状态事件回调。"""
        light = self.hass.data[DOMAIN].get("voice_light") or VOICE_LIGHT_DEFAULT
        if new is None or new.entity_id != light:
            return
        on = new.state == "on"
        was = bool(old and old.state == "on")
        if on and not was and not self.mirrored:
            self.mirrored = True
            self.cycle_seen = False
            self.hass.async_create_task(self.safe_set_cycle(1))
            _LOGGER.info("语音开 → 循环启动")
        elif not on and was and self.mirrored:
            self.mirrored = False
            self.hass.async_create_task(self.safe_set_cycle(0))
            _LOGGER.info("语音关 → 循环停止")

    def _broadcast(self) -> None:
        dispatcher_send(self.hass, SIGNAL_STATE)


# ---------- HA 集成入口 ----------


from aiohttp import web

from homeassistant.components.http import HomeAssistantView


class _PanelView(HomeAssistantView):
    url = "/api/rinnai_e86/panel"
    name = "api:rinnai_e86:panel"
    requires_auth = False  # 局域网页面板,与旧 Mac 面板同等开放度

    async def get(self, request):
        return web.Response(text=PANEL_HTML, content_type="text/html")


class _StatusView(HomeAssistantView):
    url = "/api/rinnai_e86/status"
    name = "api:rinnai_e86:status"
    requires_auth = False

    async def get(self, request):
        hub = request.app["hass"].data.get(DOMAIN, {}).get("hub")
        if hub is None:
            return web.json_response({"online": False})
        d = hub.friendly()
        d["identity"] = hub.identity
        d["last_seen"] = hub.last_seen
        d["online"] = hub.online
        return web.json_response(d)


class _CmdView(HomeAssistantView):
    url = "/api/rinnai_e86/cmd"
    name = "api:rinnai_e86:cmd"
    requires_auth = False

    async def post(self, request):
        hass = request.app["hass"]
        hub = hass.data.get(DOMAIN, {}).get("hub")
        if hub is None:
            return web.json_response({"ok": False, "error": "未就绪"}, status=503)
        try:
            body = await request.json()
        except ValueError:
            return web.json_response({"ok": False}, status=400)
        cmd = body.get("cmd")
        try:
            if cmd == "cycle" and body.get("value") in (0, 1, 2):
                hass.async_create_task(hub.set_cycle(int(body["value"])))
            elif cmd == "query":
                hass.async_create_task(hub.query())
            else:
                return web.json_response({"ok": False, "error": "未知命令"}, status=400)
        except RuntimeError as err:
            return web.json_response({"ok": False, "error": str(err)}, status=503)
        return web.json_response({"ok": True, "cmd": cmd})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {"voice_light": VOICE_LIGHT_DEFAULT}
    opts = {**entry.data, **entry.options}
    hub = RinnaiHub(
        hass,
        bridge_cloud=opts.get("bridge_cloud", True),
        topic_base=opts.get("topic_base", ""),
        device_id=opts.get("device_id", ""),
        product_id=opts.get("product_id", ""),
    )
    hass.data[DOMAIN]["voice_light"] = opts.get("voice_light", "")
    hass.data[DOMAIN]["virtual_event"] = opts.get("virtual_event", "")
    hass.data[DOMAIN]["hub"] = hub
    await hub.start()

    hass.http.register_view(_PanelView())
    hass.http.register_view(_StatusView())
    hass.http.register_view(_CmdView())

    async def _reload_on_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
        await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_reload_on_options))

    async def _light_event(ev) -> None:
        hub.voice_light_changed(ev.data.get("old_state"), ev.data.get("new_state"))

    entry.async_on_unload(
        ha_event.async_track_state_change_event(
            hass, [hass.data[DOMAIN]["voice_light"]], _light_event
        )
    )

    @callback
    def _virtual_event(ev) -> None:
        new = ev.data.get("new_state")
        old = ev.data.get("old_state")
        if new is None or new.entity_id != VIRTUAL_EVENT_DEFAULT:
            return
        if old is not None and old.state == new.state:
            return
        name = new.attributes.get("事件名称")
        if name == "cycle_on":
            _LOGGER.info("虚拟事件 cycle_on → 启动循环")
            hass.async_create_task(hub.safe_set_cycle(1))
        elif name == "cycle_off":
            _LOGGER.info("虚拟事件 cycle_off → 停止循环")
            hass.async_create_task(hub.safe_set_cycle(0))

    entry.async_on_unload(
        ha_event.async_track_state_change_event(
            hass, [VIRTUAL_EVENT_DEFAULT], _virtual_event
        )
    )

    async def _svc_cycle(call: ServiceCall) -> None:
        await hub.set_cycle(int(call.data["value"]))

    async def _svc_query(call: ServiceCall) -> None:  # noqa: ARG001
        await hub.query()

    hass.services.async_register(
        DOMAIN, "cycle", _svc_cycle, schema=vol.Schema({"value": vol.All(int, vol.In([0, 1, 2]))})
    )
    hass.services.async_register(DOMAIN, "query", _svc_query)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hub = hass.data[DOMAIN].pop("hub", None)
    if hub:
        await hub.stop()
    hass.services.async_remove(DOMAIN, "cycle")
    hass.services.async_remove(DOMAIN, "query")
    return unload
