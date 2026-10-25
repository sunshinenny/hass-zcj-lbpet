"""常量定义。"""

DOMAIN = "zcj_lbpet"

API_BASE = "https://zcb.zhichongjia.com"
APP_VERSION = "6.15.1"
APP_VERSION_CODE = "401"
USER_AGENT = "ZCJLiuBaPet/6.15.1 (iPhone; iOS 17.0; Scale/3.00)"

CONF_PHONE = "phone"
CONF_PASSWORD = "password"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_COORD_SYSTEM = "coord_system"

DEFAULT_SCAN_INTERVAL = 300
MIN_SCAN_INTERVAL = 60

# token 提前续期窗口：剩余有效期低于这个值就主动换新 token。
# 服务端 TTL 是 30 天，提前 1 天换新既不会增加登录频率（换完又是 30 天），
# 又能避开"刚好在两次轮询之间到期"的边界情况。
TOKEN_REFRESH_MARGIN = 86400

# 坐标输出坐标系
COORD_WGS84 = "wgs84"  # 转成 GPS 原始坐标，适配 HA 自带地图（默认）
COORD_GCJ02 = "gcj02"  # 原样输出高德坐标，适配高德类自定义卡片
DEFAULT_COORD_SYSTEM = COORD_WGS84

MANUFACTURER = "纳九物联 (68宠物)"
