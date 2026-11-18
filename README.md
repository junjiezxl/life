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

启动后访问：`http://127.0.0.1:8000`

### 开发模式（前后端分离热更新）

```bash
# 终端 1：启动后端
cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

# 终端 2：启动前端
cd frontend && npm run dev
```
