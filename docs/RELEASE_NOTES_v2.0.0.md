# Release Notes v2.0.0

## 概览

`v2.0.0` 将项目从“单一原型应用”推进到“可部署、可协作、可发布”的仓库形态。

本版本推荐主线为 `Web V2`：

- 前端：`Vue 3 + TypeScript + Vite`
- 后端：`FastAPI`
- 数据库：统一使用 `PostgreSQL`
- 任务：支持生成任务后台队列与状态持久化

## 重点更新

### 1. Web V2 成为推荐版本

- 新增 `frontend/` 前端工程
- 补齐首页、生成、评审导出、统计、知识图谱等页面
- 增加启动检查与模型连接状态提示

### 2. 生成链路增强

- 生成任务支持后台队列
- 生成状态支持持久化
- 页面离开后任务可继续执行
- 评审页支持“最新生成批次”筛选

### 3. 数据与部署能力增强

- 支持通过 `DATABASE_URL` 切换到 PostgreSQL
- 补齐 PostgreSQL 初始化脚本与部署文档
- 增加 Docker Compose 部署方案
- 增加 Linux `systemd` 与 `Nginx` 模板

### 4. 仓库工程化增强

- 补齐 README、版本说明、部署矩阵、发布流程
- 补齐 LICENSE、CHANGELOG、CONTRIBUTING、SECURITY
- 补齐 GitHub Issue / PR 模板、CODEOWNERS
- 增加 GitHub Tag Release 工作流

## 升级提示

- 新部署优先使用 `Web V2`
- 历史使用者仍可继续使用 `Legacy V1`
- 服务器部署建议使用 PostgreSQL
- 如果计划多实例部署，后续建议升级为独立任务队列

## 已知边界

- 当前后台任务仍是应用内队列，不是分布式队列
- 本地模型能力依赖宿主机资源
- Neo4j 属于可选能力，不是默认必需组件
