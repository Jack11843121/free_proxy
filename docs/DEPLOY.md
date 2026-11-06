# 部署指南（含腾讯云）

free_proxy 支持三种部署方式：**Docker**（推荐）、**systemd 托管**、**裸进程**。

---

## 方式一：Docker（最快）

```bash
# 1. 准备配置
cp config.example.yaml config.yaml
# 编辑 config.yaml，填好供应商凭证

# 2. 构建镜像
docker build -t free_proxy .

# 3. 运行
docker run -d --name free_proxy \
  --restart=always \
  -p 8899:8899 \
  -p 127.0.0.1:8891:8891 \
  -v $PWD/config.yaml:/app/config.yaml:ro \
  free_proxy

# 或使用 compose（可选 Redis 共享池）
cd deploy
docker compose up -d --build
```

---

## 方式二：systemd（裸机，长期运行推荐）

```bash
# 1. 上传代码到服务器
sudo mkdir -p /opt/free_proxy
sudo chown -R $USER /opt/free_proxy
# 把本仓库（含 free_proxy/、requirements.txt）传上来，或用 git clone

# 2. 装依赖
cd /opt/free_proxy
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. 配置
cp config.example.yaml config.yaml
cp deploy/systemd/free_proxy.env.example /opt/free_proxy/free_proxy.env
# 编辑 config.yaml 与 free_proxy.env（密钥）

# 4. 注册 systemd 服务
sudo cp deploy/systemd/free-proxy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now free-proxy
sudo systemctl status free-proxy   # 查看日志：journalctl -u free-proxy -f
```

> 若想以独立低权限用户运行，把 service 里的 `User=` 改成你创建好的用户。

---

## 方式三：裸进程（临时 / 调试）

```bash
cd free_proxy
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m free_proxy -c config.yaml
# 后台：nohup python -m free_proxy -c config.yaml > free_proxy.log 2>&1 &
```

---

## 腾讯云部署步骤（Lighthouse 轻量 / CVM）

1. **开一台机器**：轻量应用服务器或 CVM，选 Linux（Ubuntu/Debian/CentOS 均可），按需 2C2G 起步即可（代理网关本身资源占用很低）。
2. **放行端口**：在控制台「防火墙 / 安全组」放行：
   - `8899`（前向代理，供你的业务/服务器访问；**按需限制来源 IP**，不要对公网全开）
   - `8891`（控制面，**强烈建议只绑定内网 / 仅放行你自己的 IP**）
3. **登录服务器**，按「方式二」用 systemd 部署。
4. **加白名单**：查本机公网出口 IP（`curl https://httpbin.org/ip`），到各供应商后台把该 IP 加入白名单。
5. **验证**：
   ```bash
   curl http://127.0.0.1:8891/healthz
   curl -x http://127.0.0.1:8899 https://httpbin.org/ip
   tail -f /opt/free_proxy/free_proxy.log   # 或 journalctl -u free-proxy -f
   ```

### 控制面要不要公网暴露？

- 前向代理 `8899`：本身就是给「你自己的业务」用的出口，建议用安全组只允许你的业务服务器 IP 访问。
- 控制面 `8891`：建议**只监听内网**（config 里 `control.listen: 127.0.0.1:8891`），或套一层 Nginx + HTTPS + Basic Auth 再暴露（见 `deploy/nginx.conf`）。

---

## 多机共享代理池（可选）

4 台机器想共用同一份 IP 池、公平轮换？用 Redis：

1. 装一个 Redis（或云 Redis）；
2. 每台机器 `config.yaml` 都改成：
   ```yaml
   store:
     backend: redis
     redis_url: "redis://你的Redis地址:6379/0"
     redis_key: "free_proxy:pool"
   ```
3. 各机器照常启动；每台仍是独立的前向代理 + 调度器，但读写同一个 Redis 列表，
   `RPOPLPUSH` 保证多台机器**环形公平轮转**同一批 IP。

---

## 上线前检查清单

- [ ] 供应商白名单已加本机公网出口 IP
- [ ] `config.yaml` 里真实密钥已用 `${ENV}` 注入，未硬编码
- [ ] 控制面未对公网裸奔（仅内网监听 / 已加鉴权 / Nginx 反代）
- [ ] 前向代理 `8899` 的访问来源已限制
- [ ] `systemd` 已 `enable`，重启自启
- [ ] 用 `curl -x` 跑通了真实站点的请求
- [ ] 观察日志：健康检查在跑、池里有存活 IP、无连续报错