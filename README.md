# Sub-Dog · 字幕工作台 Docker Web 版

从用户提供的「电影字幕下载工具.exe」提取 Python 业务模块，增加浏览器界面，原生运行于 Linux Docker。无需 Windows、Wine、VNC 或桌面环境。

**当前为可运行的试用版。核心下载模块仍是恢复的 CPython 3.12 字节码，不是完整还原的源码。** Web 后端、前端、兼容适配层及提取脚本均提供源码。原程序内部版本 1.2.2，作者信息为 lohiking，提取来源与 SHA-256 记录在 `engine/provenance.json`。

## 快速启动

克隆仓库并启动：

```bash
git clone https://github.com/traceonx/sub-dog.git
cd sub-dog
docker compose up -d --build
```

浏览器打开 http://127.0.0.1:8080 。默认只绑定宿主机本地地址。

如果使用提供的离线镜像包：

```bash
docker load -i subtitle-web-image.tar.gz
docker compose up -d --no-build
```

### NAS 或局域网访问

复制 `.env.example` 为 `.env`，修改：

```dotenv
BIND_ADDRESS=0.0.0.0
PORT=8080
APP_PASSWORD=请替换为自己的强密码
```

再次启动 `docker compose up -d`，访问 `http://NAS的IP:8080`，用户名为 `admin`，密码为上面填写的值。需要跨公网访问时，通过 HTTPS 反向代理访问；HTTP Basic 凭据需要 HTTPS 保护。

### 数据存放

Compose 默认使用两个命名卷：

- `subtitle-config`：配置、字幕站 Cookie；容器路径 `/config`。
- `subtitle-downloads`：下载文件；容器路径 `/downloads`。

可以在网页“已下载文件”中直接取回文件。重建和重启容器会保留上述卷，不要使用 `docker compose down -v` 删除数据。

如需映射 NAS 文件夹，可将 Compose 的 volumes 改成：

```yaml
volumes:
  - ./config:/config
  - ./downloads:/downloads
```

容器以 UID/GID **10001:10001** 运行。使用文件夹映射时，先创建目录并赋予该用户读写权限，例如 Linux 下：

```bash
mkdir -p config downloads
sudo chown 10001:10001 config downloads
```

不要将原程序中 Windows 下载路径写入容器配置。Web 版统一使用 `/downloads`，每次下载使用独立子目录，避免重名覆盖。

## 界面预览

![字幕工作台界面](artifacts/desktop.png)

## 功能

- 按片名搜索字幕库、SubHD、伪射手，可筛选格式和语言。
- 搜索结果可切换历史任务，查看详情，发起下载。
- 自动识别沿用原模板库；不能可靠识别时在网页输入验证码，支持换图和取消。
- 验证码绑定当前任务和会话，等待 180 秒后取消；原引擎的识别阈值、站点限速和镜像轮换逻辑予以保留。
- 可以关闭自动识别，仅通过网页人工输入；该选择会沿用到所选搜索结果的下载和详情任务。
- 保存配置及 Cookie；Cookie 可能过期，重启后不保证永远免验证。
- 下载 ZIP 时先检查全部成员路径、特殊文件与展开大小，再提取字幕。最大 300 个条目、总展开大小 100 MiB。
- RAR/7z 保留原包，支持下载到本机解压；此版不自动解压这两种格式。
- 修复原程序对 HTTP 响应头大小写的假设，正确处理 gzip 页面与下载文件名；移除下载文件名中误带的 URL 查询参数。
- HTTP 错误会作为对应字幕源错误展示，不再把错误页解析成“0 条结果”。

## 实现结构

```text
浏览器 HTML / CSS / JavaScript
             ↓
FastAPI → 单线程任务队列 → 原 Aggregator / ZimukuClient → 字幕站
                 ↕
       网页验证码输入（Event 等待）
                 ↓
          /config /downloads
```

- `app.py`：Web API、任务、会话串行化、验证码桥接、配置和文件访问。
- `engine_adapter.py`：恢复模块加载、ZIP 安全解压、HTTP 响应与文件名兼容、Cookie 主机名兼容。
- `static/`：无构建步骤的前端。
- `engine/zimuku_dl/`：恢复的 `.pyc` 模块及原始验证码模板库，未包含原桌面 GUI。
- `tools/extract_engine.py`：重新从 EXE 提取业务模块；不复制 Windows DLL 或第三方二进制库。
- `tests/test_app.py`：真实引擎与本地模拟站点的集成测试。

单工作进程、单个引擎任务串行执行，避免原有 Cookie 文件与会话相互覆盖。一次搜索内仍可使用原聚合器并行访问多个来源。最多 8 个等待/执行中的任务，保留最近约 40 个任务。任务记录在内存中，重启后需重新搜索；已下载文件、设置及 Cookie 保留。

取消会唤醒等待验证码的任务；进行中的网络请求和原有重试/退避需要返回后才能结束。单个慢任务可能延迟后续任务。不要增加 Uvicorn workers，否则内存任务与验证码不会共享。

## 验证情况

本环境 Linux amd64 已完成：

- Docker 镜像构建、非 root/只读根文件系统运行、健康检查。
- 本地模拟站点：验证页识别、验证码刷新和提交、`srcurl` Cookie、Cookie 落盘和新客户端复用、下载并从 Web API 取回文件。
- 取消验证任务、认证、跨站写请求限制、文件路径越界/符号链接、ZIP 路径穿越与条目数量限制、HTTP 402 错误处理。
- Chromium 浏览器：搜索、轮询时保留验证码输入、提交验证码、显示结果、发起并取回下载；手机视口无横向溢出、无 JavaScript 异常。
- 真实字幕库：“星际穿越”搜索返回 5 条；原 OCR 通过实际验证；获取详情并成功下载 251040 字节 ASS 文件，浏览器文件接口取回后包含 `[Script Info]`。
- 修复响应压缩处理后，三个来源均能搜索；聚合搜索上限 12 条时，三来源各展示 4 条。伪射手成功保存 RAR（站点返回的文件名带有 ZIP 后缀，现已按实际格式修正）；最终复测 SubHD 完整下载成功，251040 字节 ASS 与字幕库下载内容的 SHA-256 一致。

真实字幕站的测试记录见 `artifacts/live-check.json`。浏览器交互检查使用明确标注的模拟任务，避免测试依赖外网，记录见 `artifacts/browser-check.json`。真实页面截图为 `artifacts/desktop.png` 和 `artifacts/mobile.png`。

SubHD 在部分测试中曾遇到临时下载页面失效，最终复测完整下载成功。遇到这类失效时仍会沿用原程序的预览回退；预览文本不等于原始 ASS 字幕，界面会提示并以 `.preview.txt` 保存，请核对可用性。站点改版、反爬策略和不同地区网络会影响可用性。**ARM64 镜像尚未实测**，恢复字节码要求 CPython 3.12；不支持直接升级到 3.13/3.14。

## 本地开发与测试

需要 Python 3.12：

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8080 --workers 1
```

不在容器中运行时，默认数据目录位于 `runtime/config` 和 `runtime/downloads`。可用 `ZIMUKU_DL_HOME`、`DOWNLOAD_DIR` 覆盖。

测试：

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

浏览器测试需要 Chromium；脚本默认使用 `/usr/bin/chromium`，本地应用需先启动：

```bash
python tools/browser_check.py
```

重新提取原引擎（输入应为你自己的、可信的原始 EXE；Python 字节码加载会执行程序逻辑）：

```bash
python tools/extract_engine.py /path/to/电影字幕下载工具.exe engine
```

提取脚本只读取 PyInstaller 的 PKG/PYZ 格式，没有将程序上传到在线反编译服务。若以后获得完整源码，可将 `engine/zimuku_dl/` 替换成对应的 Python 源码模块，再调整适配层并重跑测试。

## 代理和证书

普通部署不依赖此次云环境。Docker 构建需要访问 Docker Hub 和 Python 包源，运行时需要访问相关字幕站及下载域名。

如果你的环境必须使用企业代理和自定义 CA，保持 TLS 校验开启。Dockerfile 支持可选构建 secret：

```bash
docker build --secret id=pip_ca,src=/path/to/combined-ca-bundle.pem -t subtitle-web:local .
```

运行时可只读挂载完整 CA bundle 并设置 `SSL_CERT_FILE`，代理通过标准 `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` 环境变量配置。不要把 Cookie、密码或代理凭据打进镜像。
