# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ZIMUKU_DL_HOME=/config \
    DOWNLOAD_DIR=/downloads
WORKDIR /app
COPY requirements.txt ./
# The optional secret is used only by managed environments with a custom HTTPS CA.
RUN --mount=type=secret,id=pip_ca \
    if [ -f /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi; \
    pip install --no-cache-dir -r requirements.txt
RUN groupadd --gid 10001 subtitle \
    && useradd --uid 10001 --gid subtitle --no-create-home subtitle \
    && mkdir /config /downloads \
    && chown subtitle:subtitle /config /downloads
COPY --chown=10001:10001 app.py engine_adapter.py linker.py ./
COPY --chown=10001:10001 engine ./engine
COPY --chown=10001:10001 static ./static
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import os,urllib.request,base64; p=os.environ.get('APP_PASSWORD',''); r=urllib.request.Request('http://127.0.0.1:8080/api/health'); r.add_header('Authorization','Basic '+base64.b64encode(('admin:'+p).encode()).decode()) if p else None; urllib.request.urlopen(r,timeout=3).read()"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
