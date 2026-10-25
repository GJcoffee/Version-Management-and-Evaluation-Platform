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

> 说明：下表中除"LLM 评审记录为 judge 会话"外，后端其实已经实现并有测试（开发过程中提前完成），一期只是**没有做前端界面**，后续只需补 UI。

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
| 重放期间新会话 SSE 会在轮次间隙发 idle 并关闭（前端目前每 1.5s 检查并 reopen） | 后端在重放任务活跃时不发 idle，或提供重放状态 |
| CompareGroup 返回重放状态（进行中 / 完成）与所选轮次（目前存于 notes） | 9.4 |
| 重放改为轮内并发（目前同一重放会话内按轮串行） | 9.4 |
| alignment 结果缓存（目前每次请求整体重算） | 9.2 |
| 会话列表默认排除 replay 会话，或前端选择器过滤 | — |

## 接入层

| 条目 | 出处 |
|---|---|
| 其余 MCP 工具：list_agents、get_draft、update_draft、list_assets、upsert_asset、import_dataset、get_eval_status、get_session、replay_session、compare_sessions | 12.2 |
| MCP 启动时自动拉起 `evalp serve` | 12.1 |
| `.evalp.toml` 的 `[eval]` 默认评测配置 | 12.2 |
| 其余 CLI：asset、dataset import、replay、adapter check、gc、eval status | 13 |
| chat 支持直接与草稿对话（目前只能选已发布版本） | 13 |
| pull 重写 agent.yaml 时保留注释 | 10.3 |

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
