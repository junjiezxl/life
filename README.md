# LifeHub · 个人提醒、思源笔记与财务结账面板

基于 **Python (FastAPI) + SQLite + React (Vite + TypeScript + Tailwind CSS)** 构建的本地个人中心网站。

## 核心功能

1. **展示面板 (`Dashboard`)**
   - 顶部 **AI 快捷指令栏**：输入自然语言同时新增提醒和记录个人消费/收入。
   - 实时指标卡：待处理/今日到期/逾期提醒、个人净资产、本期未结账现金流、近期固定账单。
2. **提醒事项 & AI 对话 (`Reminders & AI Chat`)**
   - 支持按状态、来源（手动 / AI 对话 / 思源笔记 / 固定账单）与关键词筛选。
   - 带金额的提醒支持「完成并自动入账」。
   - 右侧持久 AI 聊天框支持多任务拆解，未配置外部 API Key 时自动启用本地中文智能规则解析。
3. **本地思源笔记 MCP 提取 (`SiYuan MCP Extractor`)**
   - 支持 **MCP Stdio**（如 `npx -y siyuan-mcp@latest`）、**MCP HTTP** 与 **直连思源内核 API (`http://127.0.0.1:6806`)**。
   - 自动扫描思源待办块（`- [ ]`、`TODO`、`提醒` 等），AI 提炼为可编辑的候选提醒卡片并一键批量导入。
4. **个人财务与结账中心 (`Finance & Settlement`)**
   - **资产与负债账户**：银行卡、现金/余额宝、理财基金、信用卡、贷款，实时计算净资产。
   - **收支流水 & 周期固定账单**：支持房租/水电/订阅到期前自动生成提醒，扣款后自动推进下期到期日。
   - **一键结账**：汇总本期未结账收入、支出、分类占比与时点净资产快照，归档历史结账账单并生成 AI 财务总结。
5. **系统设置 (`Settings`)**
   - 支持配置 **Google Gemini API**、**OpenAI 兼容接口（OpenAI / DeepSeek / 本地 Ollama）** 或 **本地规则解析**。
   - 支持测试 AI 连接、测试思源 MCP 连接并查看发现的 MCP 工具列表，以及清空示例数据/恢复演示数据。

## 快速启动

```bash
chmod +x start.sh
./start.sh
```

启动后访问：`http://127.0.0.1:8000`（局域网/NAS 可通过 `http://<NAS_IP>:8000` 访问，支持通过 `LIFEHUB_HOST` / `LIFEHUB_PORT` 环境变量覆盖监听地址与端口）。

### 原生部署（不使用 Docker，适合 Linux NAS / 飞牛 / 群晖）

1. **环境准备**：NAS 上安装 `python3`（3.9+）与 `nodejs`（18+，如果 NAS 不想装 Node，可先在本地电脑执行 `cd frontend && npm install && npm run build` 后把 `frontend/dist` 拷贝到 NAS，NAS 上只需 Python 即可运行）。
2. **拉取代码并启动**：
   ```bash
   git clone https://github.com/junjiezxl/life.git /opt/lifehub
   cd /opt/lifehub
   chmod +x start.sh
   ./start.sh
   ```
   `start.sh` 会自动创建 `backend/.venv`、安装 `backend/requirements.txt`、构建前端并监听 `0.0.0.0:8000`。
3. **后台常驻与开机自启（systemd）**：
   新建 `/etc/systemd/system/lifehub.service`：
   ```ini
   [Unit]
   Description=LifeHub Service
   After=network.target

   [Service]
   Type=simple
   WorkingDirectory=/opt/lifehub
   Environment="TZ=Asia/Shanghai"
   ExecStart=/opt/lifehub/start.sh
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```
   启用服务：
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now lifehub
   ```

### 开发模式（前后端分离热更新）

```bash
# 终端 1：启动后端
cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

# 终端 2：启动前端
cd frontend && npm run dev
```

## NAS / Docker Compose 部署

适用于群晖、飞牛 fnOS、威联通、绿联等支持 Docker Compose 的 NAS：

### 方式 A：使用 GitHub Actions 预构建镜像（推荐，适合飞牛 fnOS）

仓库已配置 `.github/workflows/docker-publish.yml`，每次推送到 `main` 分支后会自动测试并构建推送 `ghcr.io/junjiezxl/life:latest`。

在飞牛 **「Docker -> Compose -> 新增项目」** 中直接填入 [`docker-compose.ghcr.yml`](docker-compose.ghcr.yml) 内容即可启动（无需下载源码到本地编译）：

- 默认已启用 `watchtower`（仅每 5 分钟检查更新 `lifehub` 容器并自动清理旧镜像），实现 `git push` 后自动更新重启；若无需自动更新可删去或注释掉 `watchtower` 段。
- 若 `junjiezxl/life` 为**私有仓库**，需先在飞牛 SSH 中执行一次 `docker login ghcr.io`（使用 GitHub PAT 登录），并取消 `watchtower` 中 `config.json` 挂载行的注释；或在 GitHub Packages 中将该镜像设为 Public。

### 方式 B：从源码本地构建

```bash
git clone https://github.com/junjiezxl/life.git lifehub
cd lifehub
docker compose up -d --build
```

启动后访问：`http://<NAS_IP>:8000`

- **数据持久化**：SQLite 数据库自动保存在项目同级 `./data/life_hub.db`（映射到容器 `/app/backend/data`）。如需迁移本机已有数据，将本机 `backend/data/life_hub.db` 复制到 `./data/life_hub.db` 即可。
- **更新版本**：使用方式 A 可在 Docker 界面重新拉取镜像或配合 Watchtower 自动更新；使用方式 B 执行 `git pull && docker compose up -d --build`，数据卷不受影响。
- **思源笔记连接**：容器内建议使用「直连思源内核 API (`siyuan_api`)」，地址填写 NAS/宿主机局域网 IP（如 `http://192.168.1.x:6806`）。
- **外网访问安全**：已内置登录鉴权（PBKDF2 密码哈希 + `HttpOnly` 会话 Cookie + 登录防爆破锁定）。首次启动可通过页面向导初始化管理员账号，或在部署到公网前直接通过环境变量预设初始账号密码：`LIFEHUB_AUTH_USERNAME`（默认 `admin`）与 `LIFEHUB_AUTH_PASSWORD`；公网部署请务必配合 HTTPS 反向代理（如 Nginx / Caddy / Cloudflare）。
