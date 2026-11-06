# Redis 对等集群

当多台服务器都要出口、又想**共享同一份代理池**（例如 4 台机器共用 36 个出口 IP）时，
用 Redis 组成**对等集群**：无主从、每台节点都是完整网关 + 调度器，读写同一个 Redis 池。

## 架构

```
            ┌──────── Redis（共享代理池）────────┐
            │   free_proxy:pool  (LIST, 环形轮转) │
            └───────▲──────────────▲─────────────┘
                    │ 拉取后 push   │ 拉取后 push
        ┌───────────┴───────┐  ┌───┴─────────────┐
        │  节点A (8899)      │  │  节点B (8899)     │
        │  前向代理+调度器    │  │  前向代理+调度器    │
        │  供应商:品赞+快代理 │  │  供应商:巨量+其它  │
        └───────────────────┘  └──────────────────┘
                业务流量 → 任选一台，都从同一池公平取 IP
```

- **对等**：没有 master，任意节点挂了都不影响其它节点；扩展就是「再加一台」。
- **公平轮转**：Redis `LIST` + `RPOPLPUSH` 原子轮转，多台节点取 IP 不会重复抢占、近似均分。
- **独立调度**：每台节点跑自己的拉取 + 健康检查，可各接不同的供应商，冗余互补。
- **可观测**：控制面 `store.backend=redis` 时，`/api/v1/status` 显示 `backend: redis` 与共享池键名。

## 配置

每台节点的 `conf/config.yaml` 都指向同一个 Redis：

```yaml
store:
  backend: redis
  redis_url: "redis://你的Redis地址:6379/0"   # 建议独立 DB，例如 /2
  redis_key: "free_proxy:pool"
```

各节点其它配置（供应商、端口、健康检查参数）按需不同即可。

## 部署

```bash
# 前提：已有 Redis 实例（自建 docker、云 Redis、或节点内任一机器起一个都行）
# 每台节点：
git clone https://github.com/Jack11843121/free_proxy.git
cd free_proxy
bash install.sh          # 装依赖 + 生成 conf/config.yaml
# 编辑 conf/config.yaml：填 store.backend=redis + 各节点自己的供应商
sudo bash install.sh --systemd
```

> Redis 用独立的 DB index（如 `/2`）避免和你业务 Redis 混用；也可给 Redis 设内存上限。

## 容器化集群

`deploy/docker-compose.yml` 自带一个可选 `redis` 服务，多台 free_proxy 容器
把 `store.redis_url` 指向同一个 Redis 即可组成集群。

## 规模建议

| 节点数 | 建议 | 说明 |
|---|---|---|
| 1 | 默认 memory 即可 | 单机无需 Redis |
| 2~4 | Redis 共享池 | 出口 IP 池共享、故障互相兜底 |
| >4 | Redis + 更强机器 | Redis 本身会成为协调点，必要时上云 Redis |

只要 Redis 可用，扩展节点本身不改变业务接入方式：业务侧永远只是「把代理指到某台节点的 8899」。