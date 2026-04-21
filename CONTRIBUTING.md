# Contributing

感谢你为这个项目做贡献。

## 开始之前

- 阅读 [README.md](file:///e:/internship/fang/ai_testcase_gen/README.md)
- 阅读 [PROJECT_STRUCTURE.md](file:///e:/internship/fang/ai_testcase_gen/PROJECT_STRUCTURE.md)
- 根据目标版本选择工作目录：
  - `frontend/` 对应 `Web V2`
  - `ui/` 对应 `Legacy V1`

## 本地开发

1. Fork 或克隆仓库
2. 复制 `.env.example` 为 `.env`
3. 执行依赖安装脚本
4. 启动后端与相应前端

Linux / macOS:

```bash
bash scripts/bootstrap.sh
bash scripts/start-backend.sh
bash scripts/start-frontend.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1
powershell -ExecutionPolicy Bypass -File scripts/start-backend.ps1
powershell -ExecutionPolicy Bypass -File scripts/start-frontend.ps1
```

## 分支建议

- `main`
  - 稳定主线
- `develop`
  - 日常开发集成
- `feature/...`
  - 新功能开发
- `fix/...`
  - 缺陷修复
- `release/...`
  - 发版准备

## 提交建议

- 保持提交信息简洁明确
- 推荐格式：
  - `feat: ...`
  - `fix: ...`
  - `docs: ...`
  - `refactor: ...`
  - `chore: ...`

## 提交前检查

- 后端相关改动至少运行：

```bash
pytest -q tests/test_progress_callback.py tests/test_minimal_regression.py
```

- 前端相关改动至少运行：

```bash
cd frontend
npm run build
```

## Pull Request 建议

- 说明改动目的
- 说明影响范围
- 提供验证方式
- 涉及 UI 时附截图
- 涉及部署时同步更新文档
