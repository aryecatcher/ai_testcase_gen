# 发布流程

## 推荐版本策略

- 主线版本：`v2.x.x`
- 兼容版本：`v1.x.x`

## 一次正式发布建议包含

- 更新 `CHANGELOG.md`
- 确认 `README.md` 文档可用
- 确认 `.env.example` 与部署模板同步
- 确认 `npm run build` 成功
- 确认最小回归测试通过
- 创建 Git tag
- 在 GitHub 发布 Release

## 推荐发布步骤

1. 从 `develop` 合入 `main`
2. 更新 `CHANGELOG.md`
3. 提交版本收口改动
4. 打标签：

```bash
git tag v2.0.0
git push origin v2.0.0
```

5. 等待 GitHub Actions 自动构建发布产物

## 发布前检查单

- 后端：
  - `/health` 正常
  - `/startup-status` 正常
- 前端：
  - 首页可打开
  - 生成页可用
  - 评审导出页可用
- 部署：
  - PostgreSQL 可连接
  - Docker 或 Linux 服务器文档未过期

## 发布产物建议

- 源码压缩包
- Docker 镜像
- 发布说明
- 升级说明
