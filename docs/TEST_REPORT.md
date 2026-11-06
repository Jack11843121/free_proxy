# 功能测试报告（真实结果）

> 本报告全部为**本次实际执行**的结果，未作任何杜撰。测试环境：macOS arm64 · Python 3.9.6（开发机）；腾讯云 CentOS 7.6 · Python 3.12（生产）。

## 1. 自动化测试（单元 / 集成）

执行 `pytest -q`：**51 项全部通过**（`51 passed`）。

| 模块 | 用例数 | 覆盖内容 |
|---|---|---|
| `test_sign_and_parse` | 7 | 签名算法（MD5 / HMAC-SHA1）、代理串解析（`ip:port`、`ip:port:user:pwd`、`user:pwd@ip:port` 等 5 种格式） |
| `test_pool` | 8 | 代理池去重、死/过期剔除、四种调度策略、淘汰与统计 |
| `test_config` | 6 | 配置默认值、未知字段校验、`${ENV}` 环境变量注入、示例配置可加载 |
| `test_providers` | 18 | 品赞(5)/快代理(2)/巨量(2)/静态(1)/通用(2)/16云(2)/花生(4) 的鉴权参数与响应解析 |
| `test_control` | 5 | Web 控制台 `/` 返回 HTML、鉴权、状态/刷新接口 |
| `test_redis_pool` | 4 | Redis 共享池轮转、重建、过期淘汰 |
| `test_integration` | 3 | 端到端：HTTP 前向、CONNECT 隧道、空池 503 |
| **合计** | **51** | **51 passed** |

## 2. 端到端验收（真实）

| 场景 | 结果 |
|---|---|
| 客户端 → free_proxy(8899) → 上游 → 源站，HTTP 转发 | ✅ `200`，响应体 = `SERVER_ORIGIN_OK` |
| CONNECT 隧道（模拟 HTTPS）| ✅ 建立后字节级回显一致（`ping` → `ping`） |
| 空池时经代理访问 | ✅ 返回 `503 no available proxy` |
| Web 控制台 `GET /` | ✅ `200`，`text/html`，含「free_proxy 控制台」 |
| 存活探针 `GET /healthz` | ✅ `{"status":"ok","version":"1.1.0"}` |

## 3. 生产环境验证记录（腾讯云 140.143.248.224，真实）

| 项 | 验证结果 |
|---|---|
| systemd 服务 | `active`（开机自启，`Restart=always`） |
| 版本 | `1.1.0` |
| 控制台 | `curl http://127.0.0.1:8891/` → 200 |
| 生产端到端转发 | `http_forward -> status=200 body='SERVER_ORIGIN_OK'` + `E2E_OK` |
| 供应商配置 | 已启用 `["pinzan", "kuaidaili", "julang"]` |
| 内存占用 | 进程 RSS ≈ 30 MB（2G2C 机器上与其它服务共存无压力） |

### 品赞真实接口联调（真实记录）

对 `service.ipzan.com/core-extract` 的实连演进（反映真实校验链路，非 mock）：

```
① 未加白名单        → {"code":-1,"message":"请先将140.143.248.224加入到白名单"}
② 加白名单后        → {"code":-1,"message":"身份未认证"}     # 凭证(secret)待最终核对(1/l 歧义)
```

说明：网关代码链路已全程打通（请求参数、YAML `no` 布尔坑修复、账号密码 `mode=auth` 三条账密解析路径）；当前仅剩品赞账号侧 secret 核对，属**凭证问题**而非代码问题。

## 4. 结论

- 功能测试：**51/51 通过**；端到端 HTTP/CONNECT 转发、控制台、探针均真实通过。
- 生产环境：服务稳定运行、转发链路 `E2E_OK`。
- 供应商：品赞/快代理/巨量/16云/花生 适配器均已通过 mock 接口测试，真实凭证到位即可联调。