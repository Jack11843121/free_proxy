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
  no: "${PINZAN_NO}"              # 套餐/业务编号
  secret: "${PINZAN_SECRET}"      # 套餐提取密钥
  num: 5                          # 每次提取数量
  mode: whitelist                 # 白名单模式
  # 可选：
  # pool: quality                 # 优质池
  # minute: 5                     # IP 有效时长(分钟)：1/3/5/10/15/30
  # area: "440300"                # 地区筛选（行政区划码）
```

签名：品赞**无签名算法**，`no`+`secret` 直接作为 query 参数传输。

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

## 6. 什么时候需要写新适配器

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
| 通用 | 任意 | none/token/md5/hmacsha1 | 可配 | 可配 |

> **密钥安全**：所有凭证都用 `${ENV}` 环境变量注入，`config.yaml` 已被 `.gitignore` 排除，切勿把真实密钥提交进 git。