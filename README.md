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
## 用户需求分析
<img width="1641" height="921" alt="57e158b4e34e9ec765cb2b5db39c421e" src="https://github.com/user-attachments/assets/014a1876-d84d-414d-a1a0-26b5c69c2311" />
<img width="1638" height="909" alt="1f460600a98c385e977a3d80f5ac829b" src="https://github.com/user-attachments/assets/ff9577a5-c1e3-4d82-a8a1-6b91515a8bbd" />


## 架构设计
<img width="1635" height="918" alt="d7cda5a2349d9e238e8d0a55ebc02027" src="https://github.com/user-attachments/assets/b317903a-1399-403d-ae2a-611fc39de99d" />
<img width="1638" height="909" alt="8585f6f228558ce05ed4c8383fc4a651" src="https://github.com/user-attachments/assets/a63c80fb-7fbd-4a90-88e6-b9a36bd27e4f" />


## 移动端
<img width="1911" height="1077" alt="372c71cf341d9b5fe738ea1c0bc45b77" src="https://github.com/user-attachments/assets/c2a764dc-8258-40e8-b6d1-3df1b45e5162" />
<img width="1908" height="1077" alt="09ef3061b156bb7449f2d472a1bf6dfd" src="https://github.com/user-attachments/assets/359aeec5-4699-4105-9072-c06e9df6fa7a" />

## Web端
<img width="1916" height="1077" alt="9335635494b867a053e191a0e6ef8b30" src="https://github.com/user-attachments/assets/c9c1f52b-90f0-44a1-8a68-7b86882897b2" />
<img width="1917" height="1071" alt="a4eed6f698453efc4d263808c6ac6c15" src="https://github.com/user-attachments/assets/7e116b3e-48a1-40b1-98fb-fca44f692929" />
<img width="1916" height="1077" alt="7c1ad23b1123036f033016b3ec227606" src="https://github.com/user-attachments/assets/f2e8408a-4024-4c4a-b301-9a2ca873fc4a" />

## 数据设计
<img width="1914" height="1077" alt="db30c38bc2fefa735d79471dff7c5a64" src="https://github.com/user-attachments/assets/f14f9e35-9d23-47df-b789-b664c62f7620" />
