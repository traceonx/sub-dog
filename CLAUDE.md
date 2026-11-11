# 项目约定

- 每次提交都要升级版本号：修改根目录 `VERSION`，并在 `CHANGELOG.md` 顶部新增该版本条目。修复升 patch，新功能 / 部署变化升 minor。**正式版发布前版本号保持 0.x，不得升到 1.0**（由用户决定何时发布 1.0）。
- 提交前用 Python 3.12 运行全部测试：`python -m unittest discover -s tests`（字幕引擎是 CPython 3.12 字节码，其他版本无法导入）。镜像通过 GitHub Actions 构建并推送到 Docker Hub `sunqz/sub-dog`。
- 部署为单容器，数据在 `/app/data` 卷；部署文件在 `deploy/`，与 shop-cashback 保持一致。默认端口 8082（8080 / 8081 已被 tg-vault、shop-cashback 使用）。不要提交真实密钥或 `.env`。
- 前端风格参照 MoviePilot。
