# Docker / NAS 部署

与 tg-vault、shop-cashback 一样使用 Compose、数据卷、自动重启和日志轮转。Sub-Dog 是单容器（FastAPI 后端 + 网页前端同一镜像），默认端口 8082，避免与 tg-vault（8080）、shop-cashback（8081）冲突。

## 推荐：拉取 Docker Hub 镜像

GitHub Actions 推送到 main 后会发布多架构镜像 `docker.io/sunqz/sub-dog`（amd64 / arm64）。把本目录的 `compose.yaml`、`.env.example` 复制到 NAS 的独立项目目录：

```sh
cp .env.example .env
docker compose pull
docker compose up -d --wait
docker compose ps
docker compose logs --tail=100 app
```

打开 `http://NAS地址:8082`。生产环境建议在 `APP_IMAGE` 中固定 `sha-xxxxxxx` 或版本号标签，便于回滚。

## 本机构建并传输镜像

在项目根目录执行：

```sh
docker compose build
docker save -o sub-dog-image.tar sub-dog:local
```

把镜像包和本目录文件复制到 NAS，执行 `docker load -i sub-dog-image.tar`，并把 `.env` 中的 `APP_IMAGE` 改为 `sub-dog:local`。构建机与 NAS 的 CPU 架构需一致。

## 字幕库与硬链接

要把字幕硬链接到媒体库，字幕库与媒体必须在**同一存储卷**，并且**只挂载一次它们的共同父目录**：

```
/volume1/data/              → 容器内 /media-root（MEDIA_ROOT）
├── subtitles/              → DOWNLOAD_DIR=/media-root/subtitles
└── media/                  → MEDIA_DIR=/media-root/media
```

- `PUID` / `PGID` 需与媒体文件的所有者一致（群晖可在 SSH 中执行 `id 用户名` 查看，常见 `1026` / `100`），否则无法在媒体目录中创建链接。与 MoviePilot 的 PUID / PGID 设置相同即可。
- 分别挂载 `subtitles` 和 `media` 两个目录会导致 `Invalid cross-device link`，即使它们在同一个卷上。
- 不使用硬链接时，`MEDIA_DIR` 留空，字幕保存在 `appdata` 卷中。

## 验证码识别

默认使用 ppllocr（ONNX 模型随镜像提供，无需联网下载）。如需回退到原内置识别，在 compose 的 `environment` 中设置 `OCR_ENGINE: glyph`。可通过 `http://NAS地址:8082/api/health` 中的 `ocr` 字段查看当前引擎与加载状态。

## 数据、更新和备份

配置、站点 Cookie、硬链接记录保存在 `appdata` 卷（`/app/data/config`）。普通重建容器和 `docker compose down` 会保留数据，**不要使用 `down -v`**。

更新：拉取或加载新版本镜像后执行 `docker compose up -d --wait`。

备份：

```sh
docker run --rm -v sub-dog_appdata:/data -v "$PWD":/backup alpine tar czf /backup/sub-dog-data.tar.gz -C /data .
```

字幕库若放在媒体卷中，随媒体卷一起备份即可。
