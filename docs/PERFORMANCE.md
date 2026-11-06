# 性能基准

## 结论（实测数据）

用 `scripts/bench.py` 在本机 loopback 上起「本地 mock 上游 + 本地源站」,
测 free_proxy 转发链路自身的开销。实测（macOS, Python 3.9, 单进程）：

| 并发 | 请求数 | 吞吐 | p50 | p90 | p99 |
|---|---|---|---|---|---|
| 10 | 2000 | **9,689 req/s** | 1.00 ms | 1.10 ms | 1.19 ms |
| 50 | 2000 | **10,633 req/s** | 4.40 ms | 4.59 ms | 15.27 ms |
| 200 | 2000 | **9,500 req/s** | 9.03 ms | 33.02 ms | 120.95 ms |

- **CONNECT 隧道吞吐：~1.8 GB/s**（64 MB 单连接大块转发）。
- 1 KB 响应、纯字节级中继，网关自身几乎没有可见瓶颈；真实场景吞吐由**上游代理 IP 的速度**决定，不是 free_proxy。

> 说明：这是「网关自身转发能力」的微基准，不含任何真实代理 IP 的网络开销。
> free_proxy 的转发热点在 `asyncio` 字节中继，做了 TCP_NODELAY + 256 KB 大缓冲 +
> 减少多余 `drain()` 优化。

## 怎么跑

```bash
# 需已装依赖（pip install . 或 install.sh）
python scripts/bench.py
# 自定义并发梯度 / 请求数 / 隧道传输量
python scripts/bench.py --concurrency 10,50,100,200 --requests 5000 --megabytes 128
```

输出示例：

```
==============================================================
free_proxy 性能基准（loopback · mock 上游 · 本地源站）
==============================================================
HTTP 前向转发（1 KB 响应）：
    并发       请求     吞吐(req/s)   p50(ms)   p90(ms)   p99(ms)
    10     2000          9689      1.00      1.10      1.19
    ...
CONNECT 隧道吞吐：1842.63 MB/s（64 MB 单连接大块转发）
```

## 上线压测建议

要对**你的生产部署**做压测时，把 `bench.py` 里的「mock 上游」换成真实代理池：

1. 先让 free_proxy 跑起来、池里有真实代理；
2. 用你熟悉的压测工具（`ab` / `wrk` / `vegeta` / `hey`）把流量打到
   `http://127.0.0.1:8899`，目标站点选一个国内可达、稳定的地址；
3. 观察控制台 `/api/v1/status` 的存活数、健康检查延迟（`/api/v1/proxies`）。

## 性能调优要点

| 项 | 默认 | 说明 |
|---|---|---|
| `server.backlog` | 256 | 大并发可调到 2048 |
| `server.connect_timeout` | 10 | 上游连接超时 |
| `pool.check_interval` | 30 | 健康检查频率，太密会占上游配额 |
| `pool.strategy` | weighted | 最快场景用 `fastest`，均匀出口用 `round_robin` |
| 上游代理池数量 | — | 池越大越抗「单个短效 IP 抽风」 |

关键点：**代理 IP 本身是瓶颈**（短效、限速、被封），free_proxy 只做「无损转发 + 自动换 IP」，
不引入可感知的额外延迟。