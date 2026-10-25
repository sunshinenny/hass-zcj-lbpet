# 68宠物 for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/sunshinenny/hass-zcj-lbpet)](https://github.com/sunshinenny/hass-zcj-lbpet/releases)

把 68宠物（纳九物联 / 智宠家，包名 `com.zcj.lbpet`）的宠物定位器接入 Home Assistant：
实时位置、今日步数、消耗热量、运动里程、定位器电量与信号、历史轨迹。

> 非官方集成。接口来自对 App 客户端流量的逆向分析，服务商随时可能变更。

## 安装

### HACS（推荐，可自动更新）

1. HACS → 右上角 `⋮` → **自定义存储库 / Custom repositories**
2. 仓库地址填 `https://github.com/sunshinenny/hass-zcj-lbpet`，类别选 **集成 / Integration**
3. 在 HACS 的集成列表里搜索 **68宠物** → 下载
4. 重启 Home Assistant
5. 设置 → 设备与服务 → 添加集成 → 搜索 **68宠物** → 填 68宠物 App 的**手机号**和**密码**

之后有新版本时 HACS 会给出更新提示，点一下就能升级，不用再手动拷贝目录。

### 手动

1. 把 `custom_components/zcj_lbpet` 整个目录复制到 HA 配置目录的 `custom_components/` 下。
2. 重启 Home Assistant。
3. 设置 → 设备与服务 → 添加集成 → 搜索「68宠物」。
4. 填 68宠物 App 的**手机号**和**密码**。

删掉 `custom_components/zcj_lbpet/__pycache__` 再重启可以避免残留字节码。

## 功能

| 平台 | 实体 |
|---|---|
| `device_tracker` | 每只绑定定位器的宠物一个 GPS 追踪实体，属性含地址、POI、定位时间、IMEI、ICCID、信号、充电状态 |
| `sensor` | 今日步数、今日里程、今日消耗、定位器电量、信号强度、运动称号、7 日平均步数、7 日平均里程、累计步数、定位精度、所在位置、最后定位时间、设备 IMEI；**账号级**：Token 剩余有效期 |
| `binary_sensor` | 已绑定定位器、定位数据在线、充电中、寻宠模式、实时上报模式；**账号级**：Token 已失效 |
| 服务 | `zcj_lbpet.refresh_location` 强制定位器立即上报；`zcj_lbpet.get_footprint` 拉取指定日期历史轨迹（返回响应数据） |

Token 相关两个实体挂在单独的「68宠物 账号」设备下（设备类型为服务），不和宠物混在一起。`binary_sensor` 的 device_class 是 `problem`，token 失效时状态为 `on`，可以直接拿去配自动化告警。

## 服务示例

```yaml
# 让定位器立刻上报一次
service: zcj_lbpet.refresh_location
target:
  device_id: 0a1b2c...

# 取昨天轨迹点（响应数据）
service: zcj_lbpet.get_footprint
target:
  device_id: 0a1b2c...
data:
  date: "2026-10-24"
response_variable: track
```

`get_footprint` 返回：

```json
{
  "pet_no": "P0000002020...",
  "date": "2026-10-24",
  "count": 55,
  "points": [
    {"latitude": "28.5631928", "longitude": "121.3318495",
     "address": "浙江省 台州市 路桥区 银安西街 靠近立成香樟源",
     "poi": "立成香樟源", "locateTime": "2026-10-25T15:32:00.382"}
  ]
}
```

## 定位偏移与坐标系（重要）

68宠物 全链路使用高德地图 SDK，接口返回的经纬度是 **GCJ-02（火星坐标）**；Home Assistant 自带地图用 **WGS-84**。直接把 GCJ-02 当 WGS-84 用，位置会稳定偏移 500 米左右，方向是**东南**（也就是宠物看起来跑到了真实位置的右下方）。

实测偏差：台州 544 m、北京 555 m、杭州 521 m、上海 482 m。

集成默认把坐标转成 WGS-84，所以地图上的位置是对的。

如果你用高德类自定义地图卡片（需要 GCJ-02），在 集成选项 → 坐标系 里改成 `GCJ-02 高德`，关闭转换。改完要重载集成。

原始高德坐标保留在 `device_tracker` 实体的 `raw_gcj02` 属性里，方便你核对。

**注意精度问题**：转换只解决系统性偏移，不解决设备本身的定位误差。实体的 `location_accuracy` 就是服务端给的定位半径 `radius`：

| radius | 定位方式 | 典型场景 |
|---|---|---|
| 10~50 m | GPS | 户外空旷 |
| 100~300 m | 混合定位 | 楼宇间 |
| 500~1000 m | 基站（LBS） | 室内、地下车库 |

基站定位下偏几百米是设备本身的限制，软件改不了。想强制设备重新定位，调用 `zcj_lbpet.refresh_location` 服务。

## 注意事项

**1. 单会话，会顶掉手机 App。**
68宠物 的 token 是单会话的——集成登录后，手机 App 上的登录态会失效；反过来 App 重新登录，集成的 token 也会失效。集成检测到 401 会自动重新登录，但每次自动重登都会再把 App 顶下去。

如果手机上频繁被要求重新登录，把轮询间隔调大（选项里设，默认 300 秒，最小 60 秒）。本质上这是个二选一的问题，没有完美的绕过办法。

**1.1 Token 自动续期机制。**
三层，不需要你手动干预：

| 场景 | 行为 |
|---|---|
| 剩余有效期不足 **1 天**（窗口内） | 请求前主动换新 token。换新成功 → 无感；换新失败（密码改/网络断）→ **继续用旧 token**，不因为"想提前续"把还能用的会话搞死 |
| token 真的过期 | 必须换新的，换不到 → 该轮失败、实体变不可用，下一轮再试 |
| 业务请求返回 401（例如手机 App 登录把你顶掉） | 锁内重登一次后重试该请求；仍 401 则该轮标记失败，写 `WARNING` 日志 |
| 登录本身连续失败 | 指数退避 60 → 120 → 240 → 480 → 600 秒封顶，不会每轮撞登录接口触发风控 |

提前量取「1 天」和「服务端 TTL 的 1/10」中的较小值。正常 TTL 是 30 天，所以就是 1 天——不会增加登录频率（换完又是 30 天），但能避开"刚好在两次轮询之间到期"的边界情况。

步数与定位是并发请求的。两个请求同时收到 401 时，只有第一个会真正重登，第二个复用它的结果——避免"第二次登录把第一次刚拿到的 token 顶掉"导致的静默失效。

配套两个实体，方便你做告警：

```yaml
automation:
  - alias: 68宠物 token 失效告警
    trigger:
      - platform: state
        entity_id: binary_sensor.68chongwu_zhang_hao_token_shi_xiao
        to: "on"
      - platform: numeric_state
        entity_id: sensor.68chongwu_zhang_hao_token_sheng_yu_you_xiao_qi
        below: 172800   # 剩不到 2 天
    action:
      - service: notify.notify
        data:
          message: "68宠物 token 快没了，检查账号密码"
```

实体 id 以实际生成为准（HA 会按中文名音译），自动化里选实体就行。

`binary_sensor` 的属性里有 `seconds_left`、`expires_at`、`refresh_due`、`auth_failures`、`retry_in`，排查时直接看。

**2. 密码以明文存在 HA 配置里。**
`config/.storage/core.config_entries` 中可读到。这是为了 token 过期后能自动续期。介意的话只能接受每 30 天手动改一次配置。

**3. 位置精度看 `radius`。**
定位器是 4G + GPS + LBS 混合定位，市区内 `radius` 常在 30~550 米之间浮动。`radius` 已映射到 `location_accuracy`，地图上会显示精度圆。

**4. 没有绑定定位器的宠物只有档案。**
只绑了蓝牙电子犬牌的宠物没有 GPS 数据，集成会创建 `已绑定定位器 = off` 的二进制传感器，不创建位置和步数实体。

## 排错

| 现象 | 原因 / 处理 |
|---|---|
| 添加集成报「手机号或密码错误」 | 先在 App 里确认能登录；密码含特殊字符时确认没多打空格 |
| 实体全部 `unavailable` | 看日志有没有 `401`；手机 App 登录会顶掉集成会话，集成会自己重登 |
| 地图上偏 500 米 | 坐标系选项设错，或用了高德卡片但仍选 `wgs84` |
| 位置长时间不动 | 定位器没有上报。看 `定位数据在线` 和 `定位精度`；室内多半是基站定位 |
| HACS 里看不到更新 | 检查仓库有没有发新的 Release，HACS 只认 Release 版本 |

## 相关

同作者的另一个宠物定位器集成：[陪彼宠物 hass-peibi-pet](https://github.com/sunshinenny/hass-peibi-pet)

## 声明

非官方集成，与纳九物联 / 智宠家无任何关联。仅供个人在自己的账号上使用。不要高频轮询，不要用来批量抓取他人数据。
