# 控制面 API 参考

默认监听 `127.0.0.1:8891`。若配置了 `control.token`，除 `/healthz` 外都需带鉴权：
`Authorization: Bearer <token>` 或 `?token=<token>`。

所有响应均为 `application/json`。

---

## GET /healthz

存活探针。

```json
{ "status": "ok", "version": "1.0.0" }
```

## GET /api/v1/status

代理池整体状态。

```json
{
  "backend": "memory",
  "total": 120,
  "alive": 98,
  "expired": 22,
  "strategy": "weighted",
  "by_provider": { "pinzan": 40, "kuaidaili": 80 },
  "now": 1700000000.0,
  "version": "1.0.0",
  "providers": ["pinzan", "kuaidaili", "julang"],
  "last_refresh": 1700000000.0,
  "last_check": 1700000000.0
}
```

## GET /api/v1/proxies?limit=100

代理列表快照（**不返回密码**）。

```json
{
  "count": 2,
  "proxies": [
    { "ip": "1.2.3.4", "port": 8080, "provider": "pinzan", "alive": true, "latency_ms": 123.4, "success": 3, "fail": 0 },
    { "ip": "5.6.7.8", "port": 9090, "provider": "kuaidaili", "alive": false, "latency_ms": 0, "success": 1, "fail": 3 }
  ]
}
```

## GET /api/v1/proxy

按当前策略取一条可用代理，用于「直接拿 ip:port 自己用」的场景。

```json
{ "ip": "1.2.3.4", "port": 8080, "provider": "pinzan", "alive": true, "latency_ms": 123.4 }
```

- 无可用代理时返回 `503` + `{ "error": "no available proxy" }`。

## GET /api/v1/providers

已启用的供应商名称列表。

```json
{ "providers": ["pinzan", "kuaidaili", "julang"] }
```

## POST /api/v1/refresh

手动触发一轮「从所有供应商拉取新 IP」。（若已有刷新进行中则直接返回）

```json
{ "ok": true, "count": 120 }
```

## POST /api/v1/check

手动触发一轮健康检查。

```json
{ "ok": true, "total": 120, "alive": 98, "expired": 22, "backend": "memory" }
```

---

## 鉴权示例

```bash
# 无 token
curl http://127.0.0.1:8891/api/v1/status

# 有 token
curl -H "Authorization: Bearer mytoken" http://127.0.0.1:8891/api/v1/status
```