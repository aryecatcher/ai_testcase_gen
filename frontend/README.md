# Frontend Web V2

当前目录是项目的新版 Web 前端，技术栈为 `Vue 3 + TypeScript + Vite + Arco Design Vue`。

## 本地开发

1. 在项目根目录准备后端并启动 `FastAPI`
2. 在当前目录安装依赖：

```bash
npm install
```

3. 复制环境模板：

```bash
cp .env.example .env.local
```

4. 启动开发服务器：

```bash
npm run dev -- --host 127.0.0.1 --port 5173
```

默认通过 `VITE_BACKEND_URL` 连接后端。

## 生产构建

```bash
npm run build
```

构建产物位于 `dist/`，建议交给 `Nginx` 托管。

## 环境变量

- `VITE_BACKEND_URL`
  - 开发模式下的后端 API 地址
  - 默认建议使用 `http://127.0.0.1:8002`

## 推荐搭配

- 本地开发：`Vite + FastAPI`
- 生产部署：`Nginx + FastAPI`
