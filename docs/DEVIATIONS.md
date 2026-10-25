# 与详设的偏离记录

格式：`## <模块> — <简述>`，写明原因与影响。

## 工程结构 — Alembic 迁移目录放在包内

详设 1 节把迁移放在 `backend/alembic/`。实际放在 `backend/evalp/store/migrations/`（含 `env.py`、`script.py.mako`、`versions/`），
通过 `evalp.store.migrate.run_migrations(db_path)` 程序化执行，无需 `alembic.ini`。原因：迁移脚本需要随 wheel 发布，
并在服务启动时自动执行。影响：新增迁移时，用 `evalp.store.migrate.make_alembic_config(db_path)` 构造配置后调用
`alembic.command.revision(cfg, autogenerate=True, ...)`；`env.py` 已启用 `render_as_batch`（SQLite 改表需要）。

## 存储/models — 手工维护的两个表达式索引

`ix_sessions_agent_mode`（含 `created_at DESC`）与 `ix_jobs_claim`（含 `priority DESC`）是带排序方向的表达式索引，
SQLite 方言无法反射比较。它们在初始迁移中用原生 SQL 创建，并在 `models.MANUAL_INDEXES` 中登记，autogenerate 与
一致性测试会忽略这两个索引（排序方向由 `test_indexes_and_without_rowid` 单独核对）。

## 存储/models — 小的实现细节

- `eval_cases.metadata` 列在 ORM 中的属性名为 `metadata_`（`metadata` 是 SQLAlchemy 声明式基类的保留名）；列名仍为 `metadata`。
- `turns.error`、`eval_runs.error`、`eval_items.error` 按 DDL 为普通 TEXT（不做 JSON 序列化），需要结构化错误时调用方自行 `json.dumps`。
- 迁移与 DDL 的约束名按命名规范自动生成（`uq_/ck_/fk_/pk_` 前缀），DDL 中的匿名约束因此有了名字。
- 读连接额外设置 `PRAGMA query_only=ON`；写连接使用 `BEGIN IMMEDIATE`。

## 错误码 — 新增 11.2 之外的通用错误码

11.2 没有覆盖以下场景，新增：`INTERNAL_ERROR`（500，未捕获异常，响应不含内部细节）、`FORBIDDEN`（403，非回环地址访问
`/auth/bootstrap`）、`CONFLICT`（409，通用冲突，如 Provider 名称重复；`Conflict(msg, code="...")` 可指定其他 code）、
`METHOD_NOT_ALLOWED`（405）、`HTTP_ERROR`（其他框架级 HTTP 错误）。

## API — 分页越界与文档路径

- `limit` 小于 1 或大于 200 时返回 400 `VALIDATION_ERROR`（不静默截断）。
- OpenAPI 文档放在 `/api/docs` 与 `/api/openapi.json`（位于 `/api` 下，因此同样需要 token / Cookie）。

## 前端脚手架 — 依赖版本与 UI 实现（总控代记）

- UI 未使用 shadcn/Radix，改为 Tailwind v3.4 + 手写组件（风格参考 shadcn）。
- 选用稳定版本而非最新大版本：react-router-dom 6.30、@tanstack/react-table 8.21、echarts 5.6（配合 echarts-for-react 3）、tailwind-merge 2.6（Tailwind v3 配套）、vite 7 / vitest 3 / TypeScript 5.9 / ESLint 9。
- 新增列表路由 `/agents`、`/evals`（14.1 节未列，侧边栏需要）。
- 健康检查路径为 `/api/v1/health`（16.3 节简写为 `/health`）。
