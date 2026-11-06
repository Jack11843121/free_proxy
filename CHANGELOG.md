# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## v1.2.0（2026-11-06）

**新功能**

- SOCKS5 协议支持：本地 SOCKS5 监听（RFC1928 / RFC1929，支持 IPv4 / 域名 / IPv6 地址、可选用户名密码认证）。
- 上游 SOCKS5 代理链式转发：品赞/巨量等 `protocol=2` 返回的 SOCKS5 上游也能无缝链入。
- 稳定性 failover：上游建隧道/连接失败时自动换代理重试（`server.retry`）。

**测试**

- 新增 7 项 SOCKS5 测试（握手 / CONNECT IPv4+域名 / 认证 / 异常命令 / 上游链式 / failover），全量 **58 项通过**。

## v1.1.0（2026-11-06）

**新功能**

- Web 控制面板：内置可视化 dashboard（代理池状态 / 供应商 / 代理列表 / 一键刷新检查），零前端构建、随 `pip install` 分发。
- 一键安装脚本 `install.sh`：建 venv + 装依赖（清华镜像）+ 生成配置 + 安装 `free-proxy` 命令；`--systemd` 一键注册服务。
- 性能基准工具 `scripts/bench.py`：测 HTTP 转发吞吐/延迟（p50/p90/p99）与 CONNECT 隧道吞吐。
- 生产部署目录结构：`bin/`、`conf/`、`logs/`、`data/` + 管理脚本 `bin/free-proxy`（start/stop/restart/status/version）。
- Redis 对等集群：多实例共享同一代理池、环形公平轮转、按节点标识可观测。
- 新增供应商适配器：16云/亿牛云（16yun.cn）、花生代理（huashengdaili.com）；讯代理/太阳HTTP/芝麻代理待官方确认或已停运，见 PROVIDERS.md。

**优化**

- 转发链路：TCP_NODELAY 关 Nagle、大缓冲（256 KB）读、减少小包往返开销。
- 品赞适配器：支持 `mode=auth` 账号密码鉴权（逐 IP / 顶层 / 固定账密三级解析）。

**修复**

- 品赞 `no` 参数 YAML 布尔坑：YAML 1.1 把未加引号的 `no:` 解析成 `False`，适配器已兼容并在配置里加引号。

## v1.0.0（2026-11-06）

- 首个版本：品赞（ipzan）/ 快代理（kuaidaili）/ 巨量（juliangip）适配器 + `generic` 通用模板（纯 YAML 接入任意代理商）+ `static` 静态/隧道。
- 前向代理服务器：HTTP 前向 + HTTPS `CONNECT` 隧道，字节级透明中继。
- 代理池：内存 / Redis 共享，`round_robin`/`random`/`fastest`/`weighted` 调度。
- 调度器：自动拉取、健康检查、死 IP 淘汰、过期清理、自动补货。
- 控制面 JSON API：状态 / 代理列表 / 取单条 / 手动刷新检查。
- 文档：README + PHILOSOPHY / PROVIDERS / DEPLOY / API + Docker / systemd / nginx。