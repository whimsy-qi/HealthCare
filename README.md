# HealthCare

HealthCare 是一个面向医学健康咨询场景的多智能体系统工程，包含后端 Agent 编排、知识检索、前端 Web 应用、移动端原型和热点服务模块。

## 项目结构

```text
.
├── backend/        # Python 后端、Agent、RAG、图谱与接口服务
├── frontend/       # Web 前端
├── mobile-app/     # 移动端原型/移动 Web 工程
├── daily-hot-api/  # 热点内容服务
└── docs/           # 工程说明与集成评审文档
```

## 后端

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python api_server.py
```

启动前需要在 `backend/.env` 中配置模型服务、数据库、Neo4j、DashVector、RAG 等环境变量。

## Web 前端

```bash
cd frontend
npm install
npm run dev
```

## 移动端

```bash
cd mobile-app
npm install
npm run dev
```

## 热点服务

```bash
cd daily-hot-api
npm install
npm run dev
```

## 仓库说明

本仓库只保留核心工程源码、配置模板和必要文档。以下内容不提交到 GitHub：

- Python/Node 依赖目录，如 `.venv/`、`node_modules/`
- 真实环境变量文件，如 `.env`
- 日志、上传文件、测试缓存、构建产物
- 大型药品数据、PDF 语料、实验结果、RAG 缓存
- 外部工程归档和压缩包

大型数据文件建议通过对象存储、网盘、GitHub Releases 或 Git LFS 单独管理。
