# 部署方式矩阵

## 1. 本地快速试用

- 适用：个人验证、功能体验
- 组合：`SQLite + Web V2`
- 要求：
  - Python
  - Node.js
  - 可选：Ollama
- 启动：
  - `scripts/start-backend.*`
  - `scripts/start-frontend.*`

## 2. 本地兼容模式

- 适用：沿用旧 UI 习惯
- 组合：`SQLite + Legacy V1`
- 启动：
  - `scripts/start-backend.*`
  - `scripts/start-legacy-ui.*`

## 3. Linux 单机部署

- 适用：小团队共享使用
- 组合：`PostgreSQL + Web V2 + Nginx + systemd`
- 要求：
  - Linux
  - PostgreSQL
  - Python
  - Node.js 或预构建前端产物
  - 可选：Ollama / Neo4j

## 4. Docker 单机部署

- 适用：快速上线、环境一致化
- 组合：`Docker Compose + PostgreSQL + Web V2`
- 启动：

```bash
cp .env.docker.example .env.docker
docker compose --env-file .env.docker up --build -d
```

## 5. 生产推荐

- 前端：`Nginx`
- 后端：`FastAPI`
- 数据库：`PostgreSQL`
- 任务：当前版本使用应用内后台队列

## 6. 后续扩展

- 多实例后端
- 独立任务队列
- Redis / Celery
- 对象存储
- 监控与告警
