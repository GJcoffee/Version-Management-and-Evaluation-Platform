# EvalP · 版本管理与评测平台

本地运行的 agent 版本管理与评测平台：草稿与不可变版本、资产管理、并发评测、选版本对话、版本 diff、双栏实时对比、历史会话对比与重放。可以通过 MCP 让 Claude Code / Cursor 等 agent 开发工具直接操作。

## 功能（一期）

- **草稿与版本**：草稿可随意修改，发布后生成以内容哈希标识的不可变版本；标签、回滚、任意两个版本的 diff
- **资产**：prompt / tool / knowledge / dataset / file，以及 agent 快照入库的 Skill / MCP 插件 / 知识库配置 / 数据库 / 应用配置；修订历史与 diff、引用关系；Agent 详情页「资产」标签按类型列出所选版本绑定的资产（来自 agent 快照的资产在 EvalP 里只读，规则见 [docs/hiagent-接入指南.md](docs/hiagent-接入指南.md)）
- **对话**：选定版本发起会话，流式输出，完整 trace（LLM 调用、工具调用、token、耗时）
- **三类 agent**：配置型（prompt + 模型 + 工具，由平台执行）；代码型（HTTP 或子进程接入，见 `sdk/`）
- **并发评测**：多版本 × 数据集 × 评估器（contains / exact_match / regex / LLM 评审），重试、断点续跑、结果缓存
- **对比**：评测逐 case 对比（改善/退化/不变）、双栏实时对话、历史会话对比、锚定重放
- **接入**：Web UI、`evalp` CLI、MCP Server（stdio）

后续优化方向见 [docs/BACKLOG.md](docs/BACKLOG.md)。

## 环境要求

- Python ≥ 3.11
- Node.js ≥ 20（仅构建前端时需要）
- 一个 OpenAI 兼容的模型服务（DeepSeek、通义、vLLM、Ollama 等）

## 安装

```bash
git clone https://github.com/GJcoffee/Version-Management-and-Evaluation-Platform.git
cd Version-Management-and-Evaluation-Platform

# 后端
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e "backend[dev]"
pip install -e sdk            # 可选：代码型 agent 接入 SDK

# 前端（构建产物输出到 backend/evalp/static/，由后端托管）
cd frontend
npm ci
npm run build
cd ..
```

### 从 wheel 安装

wheel 内置前端产物（`evalp/static`）和 Alembic 迁移，装好即可 `evalp serve`，不需要 Node.js：

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install evalp-0.1.0-py3-none-any.whl
evalp --help
evalp serve
```

自己构建 wheel（需要 Node.js ≥ 20 和 `pip install build`）：

```bash
python scripts/build_wheel.py                  # 先构建前端，再打 wheel，输出到 dist/，并检查 wheel 内容
python scripts/build_wheel.py --skip-frontend  # 复用已有的 backend/evalp/static
python scripts/build_wheel.py --check dist/evalp-0.1.0-py3-none-any.whl   # 只检查已有 wheel
```

检查项：`index.html` 与静态资源、全部迁移脚本（`env.py`、`script.py.mako`、`versions/*.py`）、`evalp` 命令入口；缺任何一项退出码为 1。
`dist/`、`build/`、`*.egg-info/` 已在 `.gitignore` 中。

## 启动

```bash
evalp serve                    # 默认 http://127.0.0.1:7860，数据目录 ~/.evalp
```

浏览器打开 http://127.0.0.1:7860 ，先到「设置」登记模型 Provider（base_url、API Key、模型名与价格）。

## Docker 部署

```bash
export EVALP_MACHINE_ID="现有密钥库使用的机器标识"
export EVALP_SECRET_USER="现有密钥库使用的 USER"
export EVALP_IMAGE="evalp:platform-trace-20261111"
docker compose up -d --build   # 构建镜像（含前端）并在后台启动，http://127.0.0.1:7860
docker compose logs -f         # 查看日志
docker compose down            # 停止
```

- 数据目录挂载宿主机的 `~/.evalp`（可用环境变量 `EVALP_HOME` 改），与本机的 `evalp` CLI / MCP 共用 token 和数据库，宿主机上的 `evalp push`、`evalp eval run` 等命令可以直接连容器里的服务。不要同时在宿主机上运行 `evalp serve`（端口冲突）。
- Compose 必须设置 `EVALP_MACHINE_ID` 和 `EVALP_SECRET_USER`（传入容器的 `USER`）；使用现有密钥库的原值，重建时保持一致。新库可固定这两个值；与宿主共用库时使用宿主身份。配置缺失时 Compose 会拒绝启动。
- 镜像固定为 `EVALP_IMAGE` 指定的 tag；标准 Dockerfile 包含完整后端、迁移与前端，重建容器直接使用镜像，不需向运行容器拷贝代码。环境配置与迁移注意事项见 [部署指南](docs/platform-trace-deployment.md)。
- 端口只映射到宿主机的 `127.0.0.1`：镜像里打开了 `server.allow_remote_bootstrap`（浏览器经 Docker 网关访问，不是回环地址），不要改成映射到 `0.0.0.0`。
- 模型服务跑在宿主机上（如 Ollama、vLLM）时，Provider 的 base_url 写 `http://host.docker.internal:端口/v1`，不能写 `127.0.0.1`。
- 镜像里没有 git 和 agent 项目代码，代码型 agent 的子进程接入需要在宿主机上运行 `evalp serve`；HTTP 接入不受影响（agent 服务在宿主机上时同样用 `host.docker.internal`）。

## 在 agent 项目里使用（CLI）

```bash
cd my-agent-project
evalp init --kind config       # 生成 .evalp.toml、agent.yaml、示例 prompt / 数据集 / evals.yaml
# 编辑 agent.yaml：把 model.provider / model.name 改成已登记的 Provider
evalp publish -m "初版" --tag baseline      # push 草稿并发布 v1
# ……修改 prompt……
evalp publish -m "调整语气"                  # 发布 v2
evalp diff baseline v2
evalp eval run --versions baseline,v2 --baseline baseline --wait   # 数据集 / 评估器取自 .evalp.toml 的 [eval]
evalp eval gate <run_id> --baseline <基线 run_id> --max-regressions 0   # CI 门禁：不通过时退出码为 1
evalp chat --version v2
```

版本多了可以把阶段性成果标出来：`evalp milestone v5 --title "第一阶段：语气优化"` 把 v5 设为里程碑，它之前的过程版本归档到它名下
（`evalp versions` 默认隐藏已归档的版本，`--all` 显示全部；归档只影响列表展示，已归档版本照常可用）；`evalp milestone --remove v5` 取消。

其他常用命令：`evalp eval status|report|compare <run_id>`、`evalp asset ls|show|add|diff`、`evalp dataset import <文件> --name <名称>`、
`evalp replay <会话> --to v2 --wait`、`evalp adapter check`、`evalp agent conventions show|set`（agent 的发布规范）、`evalp gc`；`evalp <命令> --help` 查看参数。

## 接入 Claude Code / Cursor（MCP）

在项目根目录的 `.mcp.json` 中添加：

```json
{"mcpServers": {"evalp": {"command": "evalp", "args": ["mcp"]}}}
```

`evalp mcp` 发现服务没有运行时会自动在后台启动 `evalp serve`（`--no-autostart` 关闭），之后就可以让 agent 调用
`push_draft`、`publish_version`、`run_eval`、`get_eval_compare`、`update_draft`、`replay_session` 等 20 个工具（发布、推送前先调用 `get_conventions` 了解 agent 的发布规范）。详见 [docs/mcp-接入指南.md](docs/mcp-接入指南.md)。

## 开发

```bash
pytest tests -q                # 后端测试（使用内置 Fake OpenAI 服务，不调用真实模型）
ruff check .
cd frontend && npm run typecheck && npm run lint && npm test
npm run dev                    # 前端开发服务器，/api 代理到 7860（EVALP_BACKEND_PORT 可改）
```

CI（`.github/workflows/ci.yml`）：只在 macOS 上跑，手动触发（GitHub Actions 页面 → CI → Run workflow），推送时不自动跑；依次跑前端 `tsc`、`eslint`、`vitest`、`vite build` 与后端 `ruff check .`、`pytest`。

### 性能压测

```bash
python scripts/loadtest_eval.py                         # 2000 用例 × 并发 64，Fake 模型延迟 200ms
python scripts/loadtest_eval.py --cases 10000           # 详设 15.1 的完整规模
python scripts/loadtest_eval.py --profile /tmp/x.prof   # 用 cProfile 包住服务进程的主线程
```

脚本用临时数据目录启动真实的 `evalp serve` 子进程，本进程里启动 Fake OpenAI，输出吞吐、平台开销、服务进程 CPU / 内存、数据库体积和报告首屏耗时。
有效并发取 `min(评测并发, scheduler.workers, Provider.max_concurrency)`（默认值分别是 8 / 32 / 8），脚本会把后两者设为 `--concurrency`；
实际使用时要跑到 64 并发，需要在「设置」里同时调高调度器的 workers 和 Provider 的最大并发。
实测结果与瓶颈分析见 [docs/DEVIATIONS.md](docs/DEVIATIONS.md)。

### 窄屏与安全说明

- 视口宽度 ≤800px 时，侧边栏收成图标栏，双栏对比上下堆叠，会话页的 Trace 面板改为从底部弹出的抽屉（默认收起，点「Trace」或某一轮下方的「查看 Trace」打开）。
- 超过 60 轮的会话，对话区使用虚拟滚动，只渲染可视区附近的轮次；自动贴底、流式输出、「回到底部」的行为不变。
- API token 与 `secrets.enc` 只允许当前用户读写：POSIX 为 `chmod 600`，Windows 通过系统自带的 `icacls` 去掉继承权限、只授予当前用户。
- `.gitignore` 与 `.dockerignore` 排除本地凭据、平台数据目录、数据库及备份、业务导出、截图和运行日志；
  `.env.example`、`.env.sample` 可保留无真实凭据的示例配置。Git 忽略规则不移除已跟踪文件，上传前仍需检查差异。
- 平台技术方案、接口文档、研发记录、单元与集成测试及合成测试夹具纳入仓库。
  包含真实 Agent 业务配置、会话输入输出、产物及凭据的文件保存在本地私有目录，
  不纳入 Git 和 Docker 打包。Docker 对 `docs/` 的排除仅用于精简镜像，不影响 Git 上传文档。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/01-概要设计.md](docs/01-概要设计.md) | 目标、架构、领域模型、关键流程 |
| [docs/02-详细设计.md](docs/02-详细设计.md) | 表结构、算法、协议、接口细节 |
| [docs/03-轮间中间件方案.md](docs/03-轮间中间件方案.md) | 轮间产物解析、输入注入及实施核验 |
| [docs/api.md](docs/api.md) | REST 接口（以实现为准） |
| [docs/contract-eval.md](docs/contract-eval.md) | 评测与对比接口契约 |
| [docs/DEVIATIONS.md](docs/DEVIATIONS.md) | 实现与设计的偏离记录 |
| [docs/BACKLOG.md](docs/BACKLOG.md) | 后续优化方向 |
| [docs/mcp-接入指南.md](docs/mcp-接入指南.md) | MCP 配置与工作流 |
| [sdk/README.md](sdk/README.md) | 代码型 agent 接入 SDK |
