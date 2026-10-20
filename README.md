# Rinnai E86 本地控制 — Home Assistant 集成

为林内(Rinnai)E86 系列燃气热水器(内置 HF-LPB100 WiFi 模块)提供 **纯本地控制**:
在 HA 进程内运行一个 TLS 假云端,热水器直接连入家庭局域网,不再依赖林内云
(可选桥接模式保留林内 App 远程功能)。

> ⚠️ 免责声明:本项目为个人研究性质的反向工程,与林内官方无关。
> 燃气设备,请谨慎操作,所有控制命令请仅使用已验证安全的字段。
> 抓包/日志可能包含设备标识与使用数据,请勿公开分享。

## 工作原理

```
热水器模块 ──TLS──> HA(8883 端口,自签证书假云端)
                      ├─ 纯本地模式:应答对时/周期查询/下发控制
                      └─ 桥接模式(默认):透明转发林内云,App 照常可用
路由器 DNAT:模块出站 8883 → HA 主机(见下方配置)
```

### 抓到的协议要点(林内热水器线,区别于壁挂炉线)

- MQTT 3.1 over TLS(8883),模块不校验服务器证书
- 主题:`rinnai/SR/01/SR/<deviceid>/{set,get,stg,res,inf}`
- 载荷全 JSON,`data` 字段为十六进制字符串
- 固件要求 get→set 节奏:裸 set 会被"只应答不执行"(集成已内置代补舞步)
- SUBACK 需授权 QoS1;JA5 对时请求需应答(否则模块狂重连)

## 安装

1. 复制 `custom_components/rinnai_e86/` 到 HA 配置目录的 `custom_components/` 下
2. 重启 Home Assistant
3. 设置 → 设备与服务 → 添加集成 → **Rinnai E86**
4. 填写(从你的抓包中获得):
   - `topic_base`:如 `rinnai/SR/01/SR/289C6E144006`
   - `device_id` / `product_id`:设备序列号与产品 ID
   - `voice_light`:可选,作为语音开关的灯实体 ID
   - `virtual_event`:可选,虚拟事件实体 ID
5. 在路由器上把热水器出站 8883 DNAT 到 HA 主机(OpenWrt 示例):
   ```bash
   iptables -t nat -A PREROUTING -s <热水器IP> -p tcp --dport 8883 -j DNAT --to-destination <HA_IP>:8883
   iptables -t nat -A POSTROUTING -s <热水器IP> -d <HA_IP> -p tcp --dport 8883 -j SNAT --to-source <路由器IP>
   ```

## 实体与服务

- `select.零冷水循环模式`:关闭 / 单次循环(自动反映 App 的临时循环)
- `sensor.热水目标温度`、`binary_sensor.燃烧中/水龙头`、统计类诊断传感器
- 服务 `rinnai_e86.cycle`(value: 0/1/2)、`rinnai_e86.query`
- Web 面板:`http://<HA_IP>:8123/api/rinnai_e86/panel`

## 小爱同学(可选)

米家 App → 智能 → 情景 ×2:
- 「热水循环」→ 动作:中枢网关"产生虚拟事件",事件名 `cycle_on`
- 「停止热水循环」→ 同上,事件名 `cycle_off`

填入集成配置的 `virtual_event` 后即可语音控制。指示灯方案同样支持
(把 `voice_light` 填为网关指示灯实体)。

## 模式切换

集成 → 配置选项 → **桥接林内云**:
- 开(默认):App 在线、远程可用,本地控制并行
- 关:纯本地,设备零外联(App 离线)

## 已知事项

- 设备固件 60-80 秒重建一次 MQTT 会话,撞上下发窗口的命令由集成自动重试
- 关闭循环会顺序清理两条通道(设备不认多字段合包)
