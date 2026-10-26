# 后续优化方向（一期收窄后保留）

一期按"先用起来"收窄范围（见下方"一期最小闭环"）。以下条目在设计上保留、接口上预留，但**不在本次实现**。每条注明详设出处，后续迭代时可直接按详设展开。

## 一期最小闭环（本次实现）

- 资产 / 草稿 / 版本 / 版本 diff / 工作区同步（已完成）
- 三类 agent 对话、trace 查看（已完成）
- 评测：并发执行、失败重试、重启恢复、结果缓存；评估器 exact_match / contains / regex / llm_judge；基础汇总指标；两次评测逐 case 对比（Δ + 改善/退化/不变分类）；取消
- 对比：双栏实时对话、历史会话对比（轮次对齐 + 步骤 LCS 对齐）、历史重放（锚定 / 自由模式）
- 接入：MCP 核心工具（push_draft、pull_draft、publish_version、list_versions、diff_versions、chat、run_eval、get_eval_report、get_eval_compare）；CLI 核心命令（serve、init、push、pull、publish、versions、diff、chat、eval run/report/compare、mcp）
- 前端：评测发起与报告、评测对比、双栏对比页

## 评测

> 说明：评测的暂停 / 恢复、失败项重跑、重新评分、成本预估与预算、全部评估器类型（含人工评审标注页）、pass@k / flaky / by_tag、对比的置信区间与符号检验、CI 门禁的前端界面均已补齐。下表是评测里仍未完成的部分。

| 条目 | 出处 |
|---|---|
| LLM 评审调用记录为 judge 会话（一期只把评审请求与回复摘要写入 score.detail） | 8.5 |
| 评测会话保留期清理（retention.eval_sessions_days） | 16.2 |
| 重新评分时换绑数据集修订（`rescore` 的 `dataset` 参数，只改了 expected / rubric 时；界面目前只能改评估器） | 8.4 |
| 报告页用例表逐行标记 flaky（目前只有汇总数量和「不稳定」筛选；需要 items 接口返回每个用例的通过率） | 8.7 |

## 对比与重放

> 说明：重放自由模式、双栏单侧发送、前端运行中增量对齐、对比标题 / 备注编辑、重放状态字段与 SSE 不在轮次间隙发 idle、锚定重放轮内并发、alignment 缓存、会话选择器过滤重放会话均已完成，这一节目前没有未完成条目。

## 接入层

| 条目 | 出处 |
|---|---|
| chat 支持直接与草稿对话（目前只能选已发布版本）。会话以版本为锚（`sessions.version_id` 非空，重放 / 评测 / trace 都依赖它），支持草稿需要改会话模型、Runner 与前端，不是小补丁 | 13 |

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
