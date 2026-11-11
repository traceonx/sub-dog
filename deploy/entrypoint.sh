#!/bin/sh
# 单容器启动：数据统一在 $DATA_DIR（配置、Cookie、字幕库默认位置）。
# 以 root 启动时修正数据目录权限，然后按 PUID/PGID 降权运行（与 MoviePilot 一致）。
# 使用硬链接功能时，PUID/PGID 需与 NAS 上媒体文件的所有者一致，否则无法在媒体目录创建链接。
set -e
DATA_DIR="${DATA_DIR:-/app/data}"
export ZIMUKU_DL_HOME="${ZIMUKU_DL_HOME:-$DATA_DIR/config}"
export DOWNLOAD_DIR="${DOWNLOAD_DIR:-$DATA_DIR/subtitles}"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$ZIMUKU_DL_HOME" "$DOWNLOAD_DIR"
  chown "$PUID:$PGID" "$DATA_DIR" "$ZIMUKU_DL_HOME" "$DOWNLOAD_DIR"
  # 只修正应用自己的数据；字幕库若在媒体卷中，不递归改动其中已有文件
  chown -R "$PUID:$PGID" "$ZIMUKU_DL_HOME"
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$0" "$@"
fi

umask "${UMASK:-022}"
echo "Sub-Dog v${APP_VERSION:-$(cat VERSION)}（提交 $(echo "${GIT_SHA:-dev}" | cut -c1-7)${BUILD_TIME:+，构建于 $BUILD_TIME}）"
echo "数据目录 $DATA_DIR · 字幕库 $DOWNLOAD_DIR${MEDIA_DIR:+ · 媒体目录 $MEDIA_DIR} · 运行用户 $(id -u):$(id -g)"
exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-8080}" --workers 1
