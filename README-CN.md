# Anki Card App

[English](README.md)

一个面向机器学习面试准备的 AI 辅助间隔复习系统。它把 Markdown 或
Obsidian 笔记转换为卡片草稿，由用户审核后进入 FSRS-6 复习队列。

生产环境：<https://web-production-a42e0.up.railway.app>

## 当前状态

核心流程已经部署并通过 Mac 与 iPhone 同步验收：

```text
Markdown 笔记
    -> AI 生成草稿
    -> 人工批准、编辑或拒绝
    -> 每日复习队列
    -> Again、Hard、Good、Easy
    -> PostgreSQL 保存历史和 FSRS 排程
```

当前版本支持 Markdown/ZIP 导入、Normal/Cloze/Skeleton Recall 卡片、代码高亮、
LaTeX 公式显示、按原笔记顺序审核草稿、已批准卡片编辑、持久化卡片收藏、FSRS-6
排程、JSON 完整备份与恢复，以及 PWA 安装。所有学习数据接口都需要登录，写操作
使用 CSRF 防护。

## 安装和使用

普通用户不需要下载代码。网页版和 PWA 使用同一个云端账户与数据库。

### iPhone

1. 用 Safari 打开生产环境网址并登录。
2. 点击 Safari 的分享按钮。
3. 选择 **Add to Home Screen**。
4. 从主屏幕打开 Anki Card App。

### Mac

可以直接用 Safari 或其他浏览器，也可以安装成 PWA：

1. 用 Safari 打开生产环境网址并登录。
2. 选择 **File > Add to Dock**。
3. 从 Dock 或 Applications 打开应用。

Mac 网页版、Mac PWA 和 iPhone PWA 功能相同。当前版本需要联网才能导入、批准、
编辑或复习。静态页面可以被缓存，但离线复习写入尚未实现。

### 第一次使用

1. 在 **Create > Import** 上传 Markdown 文件或包含 Markdown 的 ZIP。
2. 等待 AI 生成卡片草稿。
3. 在 **Modify > Drafts** 批准、编辑或拒绝草稿。
4. 在 **Review** 完成当天的复习。
5. 在 **Utils > Export** 下载第一份 JSON 备份。

## 本地开发安装

### 依赖

| 工具 | 用途 |
|---|---|
| Python 3.12+ | 应用运行环境 |
| `uv` | Python 依赖和命令管理 |
| Docker Compose | 本地 PostgreSQL |
| OpenAI API key | 仅 AI 卡片生成需要 |

### 启动

```bash
cp .env.example .env
docker compose up -d postgres
uv sync --all-groups
uv run alembic upgrade head
uv run uvicorn anki_card_app.main:app --reload
```

打开 <http://localhost:8000>。

如需 AI 生成功能，把 key 加到 `.env`，不要加引号：

```dotenv
OPENAI_API_KEY=your_api_key_here
```

修改 `.env` 后需要重启应用。`.env` 包含秘密信息，不能提交到 Git。
没有 API key 时，手动创建卡片、复习、导出和恢复仍然可以使用。

本地默认使用 `AUTH_MODE=development`。如需测试密码登录：

```bash
uv run anki-card-admin create-user --email you@example.com
```

然后修改 `.env` 并重启：

```dotenv
AUTH_MODE=password
SESSION_COOKIE_SECURE=false
```

密码至少 12 个字符。修改密码使用：

```bash
uv run anki-card-admin set-password --email you@example.com
```

## 项目结构

```text
Anki_card_app/
├── src/anki_card_app/
│   ├── app.py                 # FastAPI 应用、静态资源和 PWA 路由
│   ├── main.py                # ASGI 入口
│   ├── models.py              # SQLAlchemy 数据模型
│   ├── database.py            # 数据库连接和 readiness 检查
│   ├── web.py                 # 卡片、草稿和复习页面
│   ├── imports_web.py         # 笔记上传和生成任务
│   ├── notes_web.py           # Imported Notes 页面
│   ├── generation.py          # OpenAI 结构化卡片生成
│   ├── import_service.py      # Markdown/ZIP 解析和分块
│   ├── card_service.py        # 卡片版本和状态变化
│   ├── review_service.py      # 每日队列和原子复习写入
│   ├── fsrs_adapter.py        # FSRS-6 状态转换
│   ├── export_service.py      # JSON 备份生成
│   ├── restore_service.py     # JSON 校验和原子恢复
│   ├── auth*.py               # 登录、Session 和身份隔离
│   ├── security.py            # CSRF、CSP 和安全响应头
│   ├── templates/             # Jinja 页面
│   └── static/                # CSS、JavaScript、PWA 和图标
├── migrations/                # Alembic 数据库迁移
├── tests/                     # 单元、服务和 Web 测试
├── docs/                      # PRD、开发计划和运维说明
├── compose.yaml               # 本地 PostgreSQL
├── railway.json               # Railway 构建、迁移和健康检查
└── pyproject.toml             # 依赖、测试和代码质量配置
```

核心分层：

```text
Jinja 页面和少量 JavaScript
            |
            v
       FastAPI 路由
            |
            v
 Service 层: import / generation / card / review / restore
            |
            v
 SQLAlchemy 2 + PostgreSQL + FSRS 状态
```

网页不会直接操作数据库。排程算法在服务器端执行，因此 Mac、iPhone 和未来的其他
客户端都可以共享同一套学习规则。

## 技术栈

| 层 | 技术 | 作用 |
|---|---|---|
| 后端 | Python 3.12、FastAPI、Uvicorn | 页面、身份验证和业务接口 |
| 前端 | Jinja、CSS、少量 JavaScript | 轻量服务器渲染 PWA |
| 数据 | PostgreSQL、SQLAlchemy 2、Alembic | 云端状态和数据库迁移 |
| 排程 | Py-FSRS、FSRS-6 | 计算下一次复习时间 |
| AI | OpenAI Responses API、Pydantic structured output | 从笔记生成结构化草稿 |
| 内容 | markdown-it-py、Pygments、latex2mathml | 安全 Markdown、代码高亮和 MathML 公式 |
| 工程 | uv、Pytest、Ruff、Mypy、coverage | 依赖、测试和静态检查 |
| 部署 | Railway、Railpack、GitHub | Web 服务、PostgreSQL 和发布 |

## Mac 和 iPhone 如何共享数据

```text
Mac Browser / PWA ----\
                       \
iPhone Safari / PWA ---- HTTPS ---> FastAPI ---> Railway PostgreSQL
                       /
Other Browser --------/
```

共享机制有四个关键点：

1. 两台设备登录同一个生产账户。
2. 所有卡片、草稿、复习历史和排程都保存在 PostgreSQL。
3. 每次批准、编辑或评分都通过 FastAPI 写入服务器。
4. 另一台设备刷新后从同一个 PostgreSQL 读取最新状态。

设备之间不直接传文件，也不依赖 iCloud 同步应用数据。每个设备拥有独立的登录
Session，因此在一个设备退出不会自动让另一个设备退出。

一次复习会在同一数据库事务中保存 review log 和新的 FSRS 排程。`attempt_id`
保证网络重试不会重复记录同一次评分。当前没有 IndexedDB 离线写入、冲突合并或
后台同步，断网时不要继续复习。

每日复习统计按 `America/Los_Angeles` 的午夜重置。IANA 时区会自动处理太平洋
夏令时 UTC-7 和冬令时 UTC-8。数据库时间戳仍统一保存为 UTC。

详细部署和验收步骤见
[Railway Deployment and Sync Acceptance](docs/RAILWAY_DEPLOYMENT.md)。

## 数据备份与恢复

### 推荐的低成本方案

当前 Railway 页面只向 Pro 计划开放原生 Backups 和 PITR。非 Pro 计划应使用应用
内置的 JSON 备份。不要为了测试恢复而删除、重建或覆盖生产数据库。

创建备份：

1. 登录生产环境。
2. 打开 **Utils > Export**。
3. 下载文件，例如 `anki-card-app-2026-08-17.json`。
4. 保存到私人 iCloud Drive、加密磁盘或其他受保护位置。
5. 每周备份一次，并在大量导入或编辑后额外备份。

JSON 备份包含：

| 数据 | 是否包含 |
|---|---|
| 导入笔记和分块 | 是 |
| 生成记录、草稿、卡片和所有卡片版本 | 是 |
| FSRS 排程、复习 Session 和 review logs | 是 |
| 用户时区和学习偏好 | 是 |
| 密码 hash、Session token、OpenAI API key | 否 |

备份仍然包含邮箱、原始笔记和学习内容，必须按私人数据保管，不要提交到 GitHub。

### 恢复 JSON

1. 登录一个没有学习数据的空账户。
2. 打开 **Utils > Restore**。
3. 选择由本应用导出的 version 1 JSON 文件。
4. 确认空账户提示并执行恢复。
5. 检查笔记、草稿、卡片、due 状态和复习历史。

恢复会先完整验证文件，再在一个数据库事务中写入。任何错误都会回滚，不会留下
一半数据。恢复保留当前登录账户的邮箱、密码和 Session，只恢复学习数据与偏好。

重要限制：恢复只接受空账户，不支持合并、选择性恢复或覆盖已有学习数据。

### Railway Pro

如果以后升级 Railway Pro，可以在 PostgreSQL 服务的 **Backups** 页面启用原生
volume snapshots 和 PITR。即使开启 Railway 原生备份，仍建议保留 JSON 导出，
因为 JSON 更容易迁移到其他服务。

## 主要业务规则

| 规则 | 当前行为 |
|---|---|
| AI 生成 | 只生成草稿，不自动进入复习 |
| Review history | Append-only，不覆盖历史评分 |
| Card edits | 新增不可变 CardVersion |
| 收藏 | 保存在 Card 上，并在已登录设备之间同步 |
| Daily queue | 条件允许时至少 10 Normal 和 3 Skeleton Recall |
| Skeleton prompt | 只有明确 Markdown 才加粗 |
| Markdown | 禁止嵌入 raw HTML 和不安全链接 |
| 数学公式 | 服务端把 LaTeX 转换为经过白名单过滤的 MathML |
| Offline | 只缓存静态资源，不接受离线写入 |

## Markdown 和数学公式

草稿、已批准卡片和复习卡片使用同一个渲染器。行内公式使用 `$...$`，独立公式使用
`$$...$$`：

```text
调整后的值是 $Y_{adj}$。

$$
Y_{adj} = Y - \theta \cdot (X - \bar{X})
$$
```

同时支持 `\(...\)` 和 `\[...\]`。如果整行是包含 LaTeX 命令的公式，即使没有
分隔符也会自动识别，因此下面的内容也可以正确显示：

```text
Y_{adj} = Y - \theta \cdot (X - \bar{X})
```

从笔记导入时会保留已有公式分隔符。AI 生成新公式时会按提示加入分隔符。行内代码
和 fenced code block 始终按代码显示，不会被误判成公式。

## 配置

完整配置见 [.env.example](.env.example)。生产环境至少需要：

```dotenv
APP_ENV=production
APP_DEBUG=false
AUTH_MODE=password
SESSION_COOKIE_SECURE=true
DATABASE_URL=${{Postgres.DATABASE_URL}}
OPENAI_API_KEY=stored_in_railway_secrets
OPENAI_MODEL=gpt-5.6-terra
```

Railway 在部署前执行 `alembic upgrade head`，使用 `/ready` 验证 PostgreSQL，只有
数据库可用时新版本才会接收流量。生产数据库保持私有，Web 服务通过 Railway
reference variable 获取连接地址。

## 测试

```bash
uv run ruff format --check src tests migrations
uv run ruff check src tests migrations
uv run mypy src
uv run pytest --cov=anki_card_app --cov-report=term-missing
node --check src/anki_card_app/static/app.js
node --check src/anki_card_app/static/service-worker.js
```

当前基线：117 项测试通过，总覆盖率 93.19%。

## 已知限制

| 范围 | 限制 |
|---|---|
| AI 任务 | 当前使用 FastAPI 进程内 background task，部署可能中断生成 |
| 导入 | 笔记是不可变快照，不会自动扫描 Obsidian 文件夹 |
| 恢复 | 只能恢复到空账户，不能合并 |
| 同步 | 需要联网，没有离线写入和冲突处理 |
| 账户 | 尚无自助注册、密码恢复和账户删除流程 |

## 文档

- [Product requirements](docs/PRD.md)
- [Development plan](docs/DEVELOPMENT_PLAN.md)
- [Session handoff specification](docs/SESSION_HANDOFF.md)
- [Authentication decision and threat model](docs/AUTHENTICATION.md)
- [Railway deployment and sync acceptance](docs/RAILWAY_DEPLOYMENT.md)

继续开发前先阅读 `docs/SESSION_HANDOFF.md`。它记录当前部署、数据约束、测试基线和
下一项任务。
