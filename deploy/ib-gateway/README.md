# IB Gateway 一键部署指南（LifeHub + ib_async）

本目录提供与 LifeHub 后端配合的 `IB Gateway` 容器编排模板（基于 `ghcr.io/gnzsnz/ib-gateway:latest`）。

## 端口与安全设计

- **仅绑定 `127.0.0.1`**：宿主机 `4001`（实盘 Live）、`4002`（模拟盘 Paper）和 `5900`（VNC）均只监听本机回环地址，外部公网无法直连。
- **默认模拟盘模式**：`.env.example` 默认 `TRADING_MODE=paper`（对应 LifeHub 设置中的端口 `4002`）。

## 启动步骤

```bash
cd /opt/lifehub/deploy/ib-gateway   # 或你的 LifeHub 部署目录
cp .env.example .env
nano .env                           # 填入 TWS_USERID / TWS_PASSWORD，先用 paper 模式测试
docker compose up -d
docker compose logs -f
```

启动后打开手机 **IBKR Mobile (IB Key)** 确认二次验证推送，待日志显示 Gateway 登录就绪后，即可在 LifeHub 的 **「设置 -> IBKR 交易网关」** 或 **「IBKR 交易网关」** 页面点击测试连接与同步。

## 切换实盘（谨慎）

1. 先在模拟盘（`4002`）验证持仓同步、网格挂单与止盈逻辑完全符合预期；
2. 修改 `.env` 中 `TRADING_MODE=live`，执行 `docker compose up -d` 并再次通过 IB Key 确认；
3. 在 LifeHub 中将交易模式改为 `实盘 (4001)`，如需启用自动网格下单还需关闭「仅允许模拟盘自动下单」保护锁并为对应网格策略单独开启「允许实盘自动下单」。
