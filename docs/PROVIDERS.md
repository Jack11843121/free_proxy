# 供应商接入指南

本文说明如何在 `config.yaml` 里接入品赞、快代理、巨量，以及**任何其他** HTTP 提取类代理商。

---

## 通用前提：IP 白名单

绝大多数国内供应商要求你先做一件事：**把部署 free_proxy 的那台服务器的公网出口 IP，加入对应套餐的「白名单」**，否则提取接口会返回「IP 不在白名单」之类的错误。

- 品赞：白名单模式**必须**加白；
- 巨量：提取前**必须**加白；
- 快代理：可选（建议加白，或后台开启「API 调用授权」）。

加白的方法：登录供应商后台 → 对应订单/套餐 → 「白名单 / IP 设置」→ 添加本机公网 IP（`curl https://httpbin.org/ip` 可查本机出口 IP）。

---

## 1. 品赞代理（ipzan.com）

- 后台「生成提取链接」：https://www.ipzan.com/user/extractapi
- 参数来源以你后台生成的链接为准（不同套餐可能略有差异）。

```yaml
- name: pinzan
  type: pinzan
  enabled: true
  "no": "${PINZAN_NO}"            # 套餐/业务编号（YAML 里 no 必须加引号，否则被当布尔 False）
  secret: "${PINZAN_SECRET}"      # 套餐提取密钥
  num: 5                          # 每次提取数量
  mode: auth                      # auth=账号密码鉴权 | whitelist=白名单鉴权
  auth_user: "${PINZAN_USER}"     # mode=auth 时：套餐账号
  auth_password: "${PINZAN_PWD}"  # mode=auth 时：套餐密码
  # 可选：
  # pool: quality                 # 优质池
  # minute: 5                     # IP 有效时长(分钟)：1/3/5/10/15/30
  # repeat: 1                     # 是否允许重复提取
  # area: "440300"               # 地区筛选（行政区划码）
```

签名：品赞**无签名算法**，`no`+`secret` 直接作为 query 参数传输。

> ⚠️ **白名单**：无论 `mode` 是 auth 还是 whitelist，品赞都要求**调用提取接口的机器**（即运行 free_proxy 的服务器）公网 IP 先加入套餐白名单，否则返回「请先将 xx.xx.xx.xx 加入到白名单」。`mode=auth` 决定的是「提取出来的代理 IP 用账号密码连接」，`mode=whitelist` 则是「提取出来的代理 IP 用白名单直接连接」。

---

## 2. 快代理（kuaidaili.com）

- 官方文档：https://www.kuaidaili.com/doc/api/getdps/

### 令牌模式（`sign_type: token`，推荐，最简单）

```yaml
- name: kuaidaili
  type: kuaidaili
  enabled: true
  api_url: "https://dps.kdlapi.com/api/getdps"   # 私密代理；独享=getkps、隧道=gettps
  secret_id: "${KUAIDAILI_SECRET_ID}"
  sign_type: token
  secret_token: "${KUAIDAILI_SECRET_TOKEN}"
  num: 5
  dedup: 1                 # 过滤当天已提取过的 IP
```

### HMAC-SHA1 模式

```yaml
- name: kuaidaili
  type: kuaidaili
  enabled: true
  secret_id: "${KUAIDAILI_SECRET_ID}"
  sign_type: hmacsha1
  secret_key: "${KUAIDAILI_SECRET_KEY}"
  num: 5
```

签名算法（已内置，无需手工计算）：

```
原文 = METHOD + path + "?" + 按参数名 ASCII 序拼接的 k1=v1&k2=v2...
signature = base64( HMAC-SHA1(secret_key, 原文) )
```

---

## 3. 巨量代理（juliangip.com）

- 官方文档：https://www.juliangip.com/help/api/dynamic/

```yaml
- name: julang
  type: julang
  enabled: true
  api_url: "https://v2.api.juliangip.com/dynamic/getips"   # 按量=postpay/getips，企业版=company/...
  trade_no: "${JULIANG_TRADE_NO}"
  api_key: "${JULIANG_API_KEY}"    # 仅参与签名，不进入请求参数
  num: 5
  pt: 1                            # 1=HTTP 2=SOCKS
  ip_remain: 1                     # 返回剩余可用时长，用于精确计算过期
```

签名算法（已内置）：

```
sign = md5( 按参数名 ASCII 序拼接的 k1=v1&k2=v2... + "&key=" + api_key )
# 32 位小写；api_key 只参与签名，不出现在 query 里
```

---

## 4. 静态 / 自有代理（也用于「隧道代理」）

有些供应商的「隧道代理」是一个**固定的 host:port + 用户名密码**（不限量、自动换 IP），
例如快代理隧道 `tps*.kdlapi.com:15818`、巨量隧道 `XXX.juliangip.com:23456`。
把它当成一条静态代理即可：

```yaml
- name: tunnel
  type: static
  enabled: true
  proxies:
    - "tps121.kdlapi.com:15818"
  user: "${TUNNEL_USER}"       # 可选：账密鉴权
  password: "${TUNNEL_PWD}"
```

格式支持 `ip:port`、`ip:port:user:pwd`、`ip:port@user:pwd`、`user:pwd@ip:port`。

---

## 5. 任意代理商：通用模板（零代码接入）

90% 的提取类代理商本质都是「一个 URL + 一两个鉴权参数 + 返回 `ip:port` 列表」，
用 `generic` 即可：

```yaml
- name: some_vendor
  type: generic
  enabled: true
  url: "https://api.vendor.com/getip?orderid={secret}&num={num}"
  method: GET                     # GET | POST
  secret: "${SOME_VENDOR_KEY}"    # 被 {secret} 占位符引用
  params:
    num: 5
  # 可选签名：
  # sign:
  #   type: md5                   # none | token | md5 | hmacsha1
  #   key: "${SOME_VENDOR_KEY}"
  #   param: sign                 # 签名参数名
  #   suffix: "&key="             # md5 用：签名原文后缀
  response:
    format: json                  # json | text
    success_path: code            # 成功判定字段
    success_equals: 0             # 成功取值（快代理=0、巨量=200）
    list_path: "data.proxy_list"  # 列表字段路径（品赞为 data.list）
```

`response.format: text` 时，返回体按 `text_sep`（默认换行）切行，每行一条
`ip:port`（或 `ip:port:user:pwd` 等）。

---

## 6. 16云 / 亿牛云（16yun.cn）

- 文档：https://www.16yun.cn/help/plain/dev-manual/api-proxy-guide/
- 鉴权为链接内置静态密钥 + IP 白名单，**无签名**。

```yaml
- name: yiniuyun
  type: yiniuyun
  enabled: true
  order_id: "${YINIYUN_ORDER_ID}"   # 订单号（PL/ORDER_ID）
  sign: "${YINIYUN_SIGN}"           # 链接里的 s
  user: "${YINIYUN_USER}"           # 链接里的 u
  num: 5
```

返回 JSON `{"status":true,"proxy":[{"ip":..,"port":..}]}`；提取前把出口 IP 加白。

---

## 7. 花生代理（huashengdaili.com）

后台「提取IP → 获取API链接」生成完整 URL；字段名与别家不同：
主机字段 = `sever`（不是 `ip`）、密码字段 = `pw`、成功码 = `status=="0"`，适配器已内置映射与账密透传。

```yaml
- name: huasheng
  type: huasheng
  enabled: true
  api_url: "https://你的花生生成链接&count=10&type=1"
```

> 数量/返回类型由生成链接里的 `count`/`type` 决定；返回里的 `user`/`pw` 会透传到代理鉴权。

---

## 提示：暂未内置 / 需确认的供应商

- **讯代理（xundaili）**：需 MD5 签名，但官方签名公式未能核实，暂不内置以免误配；拿到官方文档后可按 `generic` 或新建适配器补上。
- **太阳HTTP（sunhttp）**：官网不可达、接口无法核实，暂缓。
- **芝麻代理（zhima）**：主站与接口域名均已挂牌出售、疑似停运，不建议接入。

---

## 8. 什么时候需要写新适配器

只有当某家供应商的签名算法或响应结构**无法用 `generic` 表达**时，才需要写代码：

1. 在 `free_proxy/providers/` 下新建一个文件，继承 `BaseProvider`，实现 `fetch()`；
2. 复用 `free_proxy/providers/base.py` 里的 `sign_hmacsha1` / `sign_md5` / `parse_entry` 等助手；
3. 在 `free_proxy/providers/__init__.py` 的 `_REGISTRY` 里注册它的 `type`。

参考 `julang.py`（约 40 行）即可照葫芦画瓢。

---

## 配置要点速查

| 供应商 | 鉴权参数 | 是否签名 | 成功码 | 列表字段 |
|---|---|---|---|---|
| 品赞 | `no` + `secret` | 否 | `code == 0` | `data.list[]` |
| 快代理 | `secret_id` + `signature` | token / HMAC-SHA1 | `code == 0` | `data.proxy_list[]` |
| 巨量 | `trade_no` + `sign` | MD5 | `code == 200` | `data.proxy_list[]` |
| 16云/亿牛云 | `order_id` + `s` + `u` | 否 | `status == true` | `proxy[]` |
| 花生 | 生成链接内的 key | 否 | `status == "0"` | `list[]`（主机=`sever`） |
| 通用 | 任意 | none/token/md5/hmacsha1 | 可配 | 可配 |

> **密钥安全**：所有凭证都用 `${ENV}` 环境变量注入，`config.yaml` 已被 `.gitignore` 排除，切勿把真实密钥提交进 git。