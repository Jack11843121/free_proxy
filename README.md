# free_proxy

> An open-source, self-hosted local **IP proxy gateway** that aggregates third-party proxy-IP providers — Kuaidaili(快代理), Juliang(巨量), Pinzan/ipzan(品赞), 16yun(亿牛云), Huasheng(花生) and more — into one stable HTTP/SOCKS proxy endpoint with auto health-checking, rotation, refill and a web console.

> **当你的爬虫 / 数据采集 / RPA 需要「走代理 IP 访问目标」时**，通常要自己对接第三方代理 IP 供应商——各家接口、鉴权、返回格式都不一样，还得自己处理 IP 过期、被封、切换。free_proxy 把这些全收敛成一个**本地 HTTP 代理端口**：内置品赞、快代理、巨量、16云/亿牛云、花生等适配器，自动健康检查 / 智能轮换 / 自动补货 / 去重淘汰，并带 Web 控制台与 Redis 多机集群。
>
> **只需编辑 `conf/config.yaml` 填好供应商凭证并启动，把你的业务代理地址指向 `127.0.0.1:8899` 即可直接使用。**

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
- ✅ **控制面 API + Web 控制面板**：自带可视化 dashboard（代理池状态 / 供应商 / 代理列表 / 一键刷新检查），零前端构建、随包内嵌；同时提供完整 JSON 控制面 API，可接监控告警。
- ✅ **多机共享（对等集群）**：`store.backend=redis` 时多台服务器共用同一份代理池、环形公平轮转，无主从、可任意加节点，详见 [docs/CLUSTER.md](docs/CLUSTER.md)。
- ✅ **性能实测不拉胯**：字节级中继 + TCP_NODELAY + 大缓冲，loopback 实测 ~1 万 req/s、隧道 ~1.8 GB/s，附基准工具 [scripts/bench.py](scripts/bench.py) 与 [docs/PERFORMANCE.md](docs/PERFORMANCE.md)。
- ✅ **零泄露安全**：真实密钥不硬编码，用 `${ENV}` 环境变量注入；`.gitignore` 已排除 `conf/config.yaml` 与 `conf/free_proxy.env`。

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

### 1. 安装（一键）

```bash
git clone https://github.com/Jack11843121/free_proxy.git
cd free_proxy
bash install.sh                  # 建 venv + 装依赖 + 生成 conf/config.yaml + 装 free-proxy 命令
# Linux 想直接注册成 systemd 服务（需 root）：
sudo bash install.sh --systemd
```

> 依赖 Python ≥ 3.9；`install.sh` 默认走清华 PyPI 镜像加速，可用 `PIP_INDEX_URL=... bash install.sh` 覆盖。

手动安装（等价）：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install .                    # 安装 free-proxy 命令行 + 依赖
```

### 2. 配置

```bash
cp conf/config.example.yaml conf/config.yaml
```

编辑 `conf/config.yaml`，填上你的供应商凭证（密钥建议用环境变量 `${VAR}` 注入）。最小可跑示例——只接一个静态/自有代理：

```yaml
server:
  listen: "0.0.0.0:8899"
providers:
  - name: my_own
    type: static
    proxies:
      - "1.2.3.4:8080"
```

接入品赞 / 快代理 / 巨量的完整写法见 [conf/config.example.yaml](conf/config.example.yaml) 与 [docs/PROVIDERS.md](docs/PROVIDERS.md)。

### 3. 启动

```bash
free-proxy -c conf/config.yaml    # 装了命令行脚本后
# 或：python -m free_proxy -c conf/config.yaml
# 调试：python -m free_proxy -c conf/config.yaml --check-once
```

启动后浏览器打开 **http://127.0.0.1:8891** 即是 Web 控制面板（前向代理在 `127.0.0.1:8899`）。

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
| 品赞（ipzan.com） | `pinzan` | `no` + `secret` 直传 | 白名单(whitelist)/账号密码(auth)两种鉴权，提取前出口 IP 需加白 |
| 快代理（kuaidaili.com） | `kuaidaili` | `secret_id` + `signature`（token / HMAC-SHA1） | 私密/独享/隧道均可 |
| 巨量代理（juliangip.com） | `julang` | `trade_no` + `sign`（MD5） | 动态/按量/企业版均可 |
| 16云 / 亿牛云（16yun.cn） | `yiniuyun` | `order_id` + `s` + `u` 静态密钥 | 无签名，白名单鉴权 |
| 花生代理（huashengdaili.com） | `huasheng` | 生成链接内置 key | 主机字段 `sever`、账密 `user/pw` 自动透传 |
| 任意供应商 | `generic` | 可配 `none/token/md5/hmacsha1` | **纯 YAML 接入，不改代码** |
| 静态/自有 | `static` | 无 / 账密 | 自有 IP 或「隧道代理」固定入口 |

> 想接入新的代理商？九成场景用 `generic` 三分钟搞定，详见 [docs/PROVIDERS.md](docs/PROVIDERS.md)。

---

## 控制面 API

浏览器打开 **`http://127.0.0.1:8891/`** 即是可视化控制台；以下 JSON API 供脚本/监控调用。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | Web 控制台（可视化 dashboard） |
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

支持 Docker、`systemd`、裸进程三种方式，含腾讯云部署步骤与上线前检查清单，见 [docs/DEPLOY.md](docs/DEPLOY.md)。多机集群见 [docs/CLUSTER.md](docs/CLUSTER.md)。

---

## 文档目录

| 文档 | 内容 |
|---|---|
| [docs/PHILOSOPHY.md](docs/PHILOSOPHY.md) | 设计理念与「供应商无关 / 自愈 / 透明 / 可观测 / 可扩展」五原则 |
| [docs/PROVIDERS.md](docs/PROVIDERS.md) | 各家供应商接入详解 + generic 通用模板 + 新增适配器方法 |
| [docs/DEPLOY.md](docs/DEPLOY.md) | Docker / systemd / 裸进程 / 腾讯云 / 多机集群 |
| [docs/CLUSTER.md](docs/CLUSTER.md) | Redis 对等集群架构与部署 |
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | 性能基准结论 + 压测方法 + 调优要点 |
| [docs/TEST_REPORT.md](docs/TEST_REPORT.md) | 功能测试报告（51 项真实结果 + 生产验证） |
| [docs/LOAD_TEST_REPORT.md](docs/LOAD_TEST_REPORT.md) | 压测报告（loopback 实测 + 生产模拟示意） |
| [docs/API.md](docs/API.md) | 控制面 JSON API 参考 |
| [CHANGELOG.md](CHANGELOG.md) | 版本记录 |

---

## 适用场景

- 🕷️ **爬虫 / 数据采集**：`Scrapy`、`requests`、`aiohttp`、`httpx` 直接挂代理出口，自动换 IP 防封。
- 🤖 **RPA / 自动化脚本**：浏览器或自动化工具统一走本地代理，屏蔽供应商差异。
- 📊 **价格监控 / 舆情监测 / SEO 排名 / 竞品分析**：需要大量出口 IP 稳定轮换。
- 🏢 **企业统一代理出口**：多供应商聚合 + Redis 多机集群，团队共享同一代理池。

---

## 常见问题（FAQ）

- **支持哪些代理商？** 内置品赞、快代理、巨量、16云/亿牛云、花生；任意 HTTP 提取类供应商可用 `generic` 模板纯 YAML 接入；自有 IP / 隧道代理用 `static`。
- **怎么接入新代理商？** 九成场景零代码：`conf/config.yaml` 加一段 `generic` 配置（URL 模板 + 返回解析）即可，见 [docs/PROVIDERS.md](docs/PROVIDERS.md)。
- **支持 SOCKS5 吗？** 本地出口是 HTTP/HTTPS（含 CONNECT 隧道）；品赞/巨量等可通过 `protocol` 参数取 SOCKS 上游。需要 SOCKS 出口可扩展。
- **性能怎么样？** loopback 实测 ~1 万 req/s、隧道 1.8 GB/s，网关自身开销极低；真实吞吐取决于你的代理 IP，见 [docs/PERFORMANCE.md](docs/PERFORMANCE.md)。
- **多台服务器能共享吗？** 能，`store.backend=redis` 组成对等集群、公平轮转，见 [docs/CLUSTER.md](docs/CLUSTER.md)。
- **部署复杂吗？** 不复杂：`git clone` + `bash install.sh`（或 `sudo bash install.sh --systemd`），见 [docs/DEPLOY.md](docs/DEPLOY.md)。
- **要备案 / 域名吗？** 不需要，本地或服务器一台机器 + IP:端口即可跑。

---

## 定制开发 / 联系

本项目 MIT 开源、永久免费使用。

如需**定制开发**（新增特殊代理商适配、隧道/独享/海外 IP、私有化内网部署、多机集群调度、与爬虫/RPA 框架集成等），或需要**部署 + 测试 + IP 代理使用指导**，欢迎联系：

📞 **电话：15919897624**
📧 **邮箱：1184311@qq.com**

> 可提供：开发、部署、测试、IP 代理指导等定制业务，费用很低。

---

## License

[MIT](LICENSE) © 2026 free_proxy contributors