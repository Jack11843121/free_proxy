# free_proxy

> 一个本地入口，聚合**所有**第三方 IP 代理供应商 —— 品赞、快代理、巨量…… 自动健康检查、智能轮换、自动补货、去重淘汰。你的爬虫 / RPA / 业务代码只需要把代理指向 `127.0.0.1:8899`，从此**永远不用再关心「该用哪家、IP 是不是过期了、被封了怎么办」**。

[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## 这是什么

`free_proxy` 是一个**本地自托管的 IP 代理聚合网关**（proxy pool / gateway）。它把多家上游代理供应商统一起来，对外只暴露一个稳定、自愈的本地 HTTP 代理端口：

```
你的业务代码 ──► 127.0.0.1:8899 (free_proxy) ──► 品赞 / 快代理 / 巨量 / 任意代理商
                       │
                       ├─ 定时健康检查：死 IP 自动淘汰
                       ├─ 智能轮换：round_robin / random / fastest / weighted
                       ├─ 自动补货：池低于阈值时向供应商拉新
                       └─ 控制面 API：状态 / 代理列表 / 手动刷新
```

**核心理念**：*「你只管用，IP 的事交给 free_proxy」*。供应商之间的差异（鉴权算法、接口格式、返回字段）被完全封装在「适配器」里，对使用者不可见。

详细理念说明见 [docs/PHILOSOPHY.md](docs/PHILOSOPHY.md)。

---

## 功能特性

- ✅ **供应商无关**：品赞（ipzan）、快代理（kuaidaili）、巨量（juliangip）已内置；任意其他 HTTP 提取类代理商可用「通用模板」**纯配置接入、零改代码**。
- ✅ **真正的代理网关**：支持标准 HTTP 前向代理 + HTTPS `CONNECT` 隧道，字节级透明中继，`requests` / `aiohttp` / `curl` / 浏览器 / RPA 全部零改动。
- ✅ **自愈代理池**：定时健康检查测延时，连续失败自动判死淘汰，过期 IP 自动清理，池不足自动补货。
- ✅ **多种调度策略**：轮询 / 随机 / 最快 / 加权（按失败次数与延迟）。
- ✅ **控制面 API**：查看状态、代理列表、取单条 IP 直连、手动触发刷新/检查。
- ✅ **多机共享（可选）**：`store.backend=redis` 时多台服务器共用同一份代理池，`RPOPLPUSH` 环形公平轮转。
- ✅ **零泄露安全**：真实密钥不硬编码，用 `${ENV}` 环境变量注入；`.gitignore` 已排除 `config.yaml`。

---

## 架构

```
                    ┌───────────────────────────────────────────────┐
                    │                   free_proxy                  │
                    │                                               │
  业务流量 ──HTTP/CONNECT──►  前向代理服务器(8899)  ──pick──►  代理池  │
                    │                      ▲                      │  │
                    │                      │                      │  │
                    │              调度器(健康检查/淘汰/补货)       │  │
                    │                      │                      │  │
                    │          ┌───────────┴───────────┐          │  │
                    │          │   Provider 适配层      │          │  │
                    │          │ 品赞│快代理│巨量│通用   │          │  │
                    │          └───────────────────────┘          │  │
                    └──────────────┬───────────────┬──────────────┘
                                   │               │
                            供应商提取 API      控制面 API(8891)
                               (拉 IP)         (状态/代理/刷新)
```

---

## 快速开始

### 1. 安装

```bash
git clone https://github.com/Jack11843121/free_proxy.git
cd free_proxy
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # 依赖：aiohttp、PyYAML（Redis 共享池才需要 redis）
```

Python ≥ 3.9 即可。

### 2. 配置

```bash
cp config.example.yaml config.yaml
```

编辑 `config.yaml`，填上你的供应商凭证（密钥建议用环境变量 `${VAR}` 注入）。最小可跑示例——只接一个静态/自有代理：

```yaml
server:
  listen: "0.0.0.0:8899"
providers:
  - name: my_own
    type: static
    proxies:
      - "1.2.3.4:8080"
```

接入品赞 / 快代理 / 巨量的完整写法见 [config.example.yaml](config.example.yaml) 与 [docs/PROVIDERS.md](docs/PROVIDERS.md)。

### 3. 启动

```bash
python -m free_proxy -c config.yaml
# 调试：拉取 + 健康检查各一轮后退出
python -m free_proxy -c config.yaml --check-once
```

看到 `前向代理已监听 0.0.0.0:8899`、`控制面已监听 127.0.0.1:8891` 即成功。

### 4. 业务代码接入

```bash
# curl 走代理
curl -x http://127.0.0.1:8899 https://httpbin.org/ip
```

```python
# requests
import requests
proxies = {"http": "http://127.0.0.1:8899", "https": "http://127.0.0.1:8899"}
print(requests.get("https://httpbin.org/ip", proxies=proxies, timeout=10).json())
```

```python
# aiohttp
import aiohttp
async with aiohttp.ClientSession() as s:
    async with s.get("https://httpbin.org/ip", proxy="http://127.0.0.1:8899") as r:
        print(await r.json())
```

也可以直接用控制面拿一条 IP 直连（适合不想走网关、只想轮询取 IP 的场景）：

```bash
curl http://127.0.0.1:8891/api/v1/proxy
# => {"ip":"1.2.3.4","port":8080,"provider":"pinzan","latency_ms":123.4, ...}
```

---

## 支持的代理商

| 供应商 | `type` | 鉴权方式 | 说明 |
|---|---|---|---|
| 品赞（ipzan.com） | `pinzan` | `no` + `secret` 直传 | 白名单模式，提取前把出口 IP 加白 |
| 快代理（kuaidaili.com） | `kuaidaili` | `secret_id` + `signature`（token / HMAC-SHA1） | 私密/独享/隧道均可 |
| 巨量代理（juliangip.com） | `julang` | `trade_no` + `sign`（MD5） | 动态/按量/企业版均可 |
| 任意供应商 | `generic` | 可配 `none/token/md5/hmacsha1` | **纯 YAML 接入，不改代码** |
| 静态/自有 | `static` | 无 / 账密 | 自有 IP 或「隧道代理」固定入口 |

> 想接入新的代理商？九成场景用 `generic` 三分钟搞定，详见 [docs/PROVIDERS.md](docs/PROVIDERS.md)。

---

## 控制面 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | 存活探针 |
| GET | `/api/v1/status` | 池状态、供应商、最近刷新/检查时间 |
| GET | `/api/v1/proxies?limit=100` | 代理列表（不含密码） |
| GET | `/api/v1/proxy` | 取一条可用代理（ip:port 直连） |
| GET | `/api/v1/providers` | 已启用的供应商 |
| POST | `/api/v1/refresh` | 手动触发拉取 |
| POST | `/api/v1/check` | 手动触发健康检查 |

完整说明见 [docs/API.md](docs/API.md)。

---

## 部署

支持 Docker、`systemd`、裸进程三种方式，含腾讯云部署步骤与上线前检查清单，见 [docs/DEPLOY.md](docs/DEPLOY.md)。

---

## 定制开发 / 联系

本项目 MIT 开源、永久免费使用。

如需**定制开发**（新增特殊代理商适配、隧道/独享/海外 IP、私有化内网部署、多机集群调度、与爬虫/RPA 框架集成等），或需要**部署 + 测试 + IP 代理使用指导**，欢迎联系：

📧 **11843121@qq.com**

> 可提供：开发、部署、测试、IP 代理指导等定制业务，费用很低。

---

## License

[MIT](LICENSE) © 2026 free_proxy contributors