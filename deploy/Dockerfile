# 单镜像：FastAPI 后端 + 网页前端，同一端口对外提供服务。
# 字幕引擎为 CPython 3.12 字节码，基础镜像必须保持 Python 3.12。
FROM python:3.12-slim-bookworm
ARG APP_VERSION= GIT_SHA=dev BUILD_TIME=
ENV APP_VERSION=$APP_VERSION GIT_SHA=$GIT_SHA BUILD_TIME=$BUILD_TIME \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data PUID=1000 PGID=1000 UMASK=022 PORT=8080
WORKDIR /app
COPY requirements.txt ./
# The optional secret is used only by managed environments with a custom HTTPS CA.
RUN --mount=type=secret,id=pip_ca \
    if [ -f /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY VERSION *.py ./
COPY engine ./engine
COPY static ./static
COPY --chmod=755 deploy/entrypoint.sh /entrypoint.sh
RUN mkdir -p /app/data
# 入口以 root 启动，修正数据目录权限后按 PUID/PGID 降权运行
VOLUME /app/data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import os,urllib.request,base64; p=os.environ.get('APP_PASSWORD',''); r=urllib.request.Request('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/api/health'); r.add_header('Authorization','Basic '+base64.b64encode(('admin:'+p).encode()).decode()) if p else None; urllib.request.urlopen(r,timeout=3).read()"
ENTRYPOINT ["/entrypoint.sh"]
