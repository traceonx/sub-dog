# 更新日志

## 0.2.1

- 内部：新增字幕匹配模块（解析视频文件名中的片名、年份、季集、制作组、片源、版本，给候选字幕打分，从季包中挑选对应集数），为外部调用做准备，尚未接入接口。

## 0.2.0

- 新增：字幕硬链接到媒体库（「关联到媒体」），按视频文件名 + 语言后缀命名。
- 新增：媒体中的硬链接全部删除后，按设定延迟（默认 30 秒）自动删除字幕库中的对应字幕；可「保留」取消。
- 部署：与 shop-cashback 统一。文件移至 `deploy/`（Dockerfile、entrypoint、compose、NAS 说明），GitHub Actions 构建 amd64 / arm64 多架构镜像并推送到 `docker.io/sunqz/sub-dog`。
- 部署：数据统一放在 `/app/data`（配置在 `config/`，默认字幕库在 `subtitles/`）；入口以 root 修正权限后按 `PUID` / `PGID` 降权运行。
- 部署：默认端口改为 8082；`.env` 中 `BIND_ADDRESS` / `PORT` 改名为 `WEB_BIND` / `WEB_PORT`。
- 迁移：旧版 `subtitle-config` 卷中的文件复制到新卷的 `config/` 目录，`subtitle-downloads` 卷中的文件复制到 `subtitles/` 目录。
- `/api/health` 返回版本号、提交与构建时间。

## 0.1.0

- 首个版本：聚合字幕库、SubHD、伪射手搜索与下载，验证码识别与手动输入，Docker 部署。
