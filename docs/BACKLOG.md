# 后续优化方向（一期收窄后保留）

一期按"先用起来"收窄范围（见下方"一期最小闭环"）。以下条目在设计上保留、接口上预留，但**不在本次实现**。每条注明详设出处，后续迭代时可直接按详设展开。

## 一期最小闭环（本次实现）

- 资产 / 草稿 / 版本 / 版本 diff / 工作区同步（已完成）
- 三类 agent 对话、trace 查看（已完成）
- 评测：并发执行、失败重试、重启恢复、结果缓存；评估器 exact_match / contains / regex / llm_judge；基础汇总指标；两次评测逐 case 对比（Δ + 改善/退化/不变分类）；取消
- 对比：双栏实时对话、历史会话对比（轮次对齐 + 步骤 LCS 对齐）、历史重放（仅锚定模式）
- 接入：MCP 核心工具（push_draft、pull_draft、publish_version、list_versions、diff_versions、chat、run_eval、get_eval_report、get_eval_compare）；CLI 核心命令（serve、init、push、pull、publish、versions、diff、chat、eval run/report/compare、mcp）
- 前端：评测发起与报告、评测对比、双栏对比页

## 评测

| 条目 | 出处 |
|---|---|
| 暂停 / 恢复、失败项一键重跑 | 8.2、8.3 |
| 仅重新评分（rescore，按 evaluator_hash 增量） | 8.4 |
| 成本预估与 dry_run、预算上限自动暂停 | 8.1、8.3 |
| 评估器：json_schema、trace 行为断言、python 自定义、人工评审（含标注页） | 8.5、14.2 |
| LLM 评审多次采样取平均（samples>1） | 8.5 |
| LLM 评审调用记录为 judge 会话（一期只把评审请求与回复摘要写入 score.detail） | 8.5 |
| pass@k、不稳定用例（flaky）标记 | 8.6、8.7 |
| 配对 bootstrap 置信区间、符号检验 p 值 | 8.7 |
| 按标签分组统计（by_tag） | 8.6 |
| CI 门禁（`evalp eval gate` / `/evals/{id}/gate`） | 13 |
| 评测会话保留期清理（retention.eval_sessions_days） | 16.2 |

## 对比与重放

| 条目 | 出处 |
|---|---|
| 重放"自由模式"（新版本用自己的回复续写上下文） | 9.4 |
| 双栏中单侧发送（分叉消息） | 9.3 |
| 前端运行中增量对齐（目前以服务端对齐结果为准） | 9.2 |
| 保存对比的标题 / 备注编辑 | 9.1 |

## 接入层

| 条目 | 出处 |
|---|---|
| 其余 MCP 工具：list_agents、get_draft、update_draft、list_assets、upsert_asset、import_dataset、get_eval_status、get_session、replay_session、compare_sessions | 12.2 |
| MCP 启动时自动拉起 `evalp serve` | 12.1 |
| `.evalp.toml` 的 `[eval]` 默认评测配置 | 12.2 |
| 其余 CLI：asset、dataset import、replay、adapter check、gc、eval status | 13 |

## 后端改进（来自前端联调反馈）

| 条目 | 说明 |
|---|---|
| `GET /agents` 列表直接返回 tags | 现在前端逐行请求 `/agents/{id}/tags`（N+1） |
| 版本 diff 的 path 与 `assets[].spec_path` 统一 | 现在一个用身份键 `tools[name]`、一个用下标 `tools[0]` |
| `PATCH /sessions/{id}` 修改会话标题 | 前端目前只读 |
| 取消 / 出错的轮次保存已流出的部分文本 | 现在 llm.delta 不落库，刷新后只剩"已取消" |
| 大 payload 存根改为字段级 `{"$blob"}`，或在 api.md 写明整 payload 存根 | 16.x / 6.2 |
| SSE 业务事件 `error` 改名（如 `agent.error`） | 与 EventSource.onerror 同名，易被客户端误判为断线 |
| `/versions/diff` 支持草稿对比基础版本 | 状态栏改动数目前由前端计算 |

## 前端

| 条目 | 说明 |
|---|---|
| ChatPane 虚拟滚动（几百轮的长会话） | 性能 |
| 窄屏（≤800px）布局，Trace 面板自适应 | 体验 |
| Monaco 按需打包语言，缩小静态资源体积（目前约 15MB） | 体积 |
| 处理 `npm audit` 的传递依赖告警 | 安全 |

## 平台与工程

| 条目 | 出处 |
|---|---|
| Windows 上 token 文件 ACL 限制（目前仅 POSIX chmod 600） | 16.1 |
| 性能压测（1 万用例 × 并发 64）与优化 | 15.1 |
| Windows / macOS / Linux CI 矩阵 | 15.1 |
| wheel 打包发布流程（前端产物内置）与安装文档 | 12 |
