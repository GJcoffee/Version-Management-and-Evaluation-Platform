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

> 说明：ChatPane 虚拟滚动（超过 60 轮启用）、窄屏（≤800px）布局与 Trace 抽屉、Monaco 按需打包（静态资源 15MB → 6MB）与编辑器页面路由懒加载、`npm audit` 告警处理（11 → 5，无 critical）均已完成，这一节目前没有未完成条目。`npm audit` 剩余的 5 条（react-router-dom 6.x、echarts 5.x、vitest 3.x 及其 `@vitest/mocker`）都只能靠大版本升级消除，见 `docs/DEVIATIONS.md`。

## 平台与工程

> 说明：Windows token 文件 ACL（icacls）、wheel 打包发布流程与安装文档（`scripts/build_wheel.py`）、Windows / macOS / Linux CI 矩阵（`.github/workflows/ci.yml`）、压测脚本（`scripts/loadtest_eval.py`）均已完成。CI 与 Windows ACL 只做了本地检查（YAML 合法性、mock 平台判断后的调用参数），尚未在真实的 GitHub runner / Windows 机器上跑过。下表是仍未完成的部分。

| 条目 | 出处 |
|---|---|
| 评测吞吐优化：压测（2000 用例 × 并发 64，模型延迟 200ms）实测约 10 条/秒（理想值 320 条/秒），服务进程单核跑满；瓶颈是每个用例约 75 条 SQL（12 次写事务 + 11 次读事务）带来的事件循环与 aiosqlite 线程往返开销，详见 `docs/DEVIATIONS.md`。要显著提升需要合并写事务（会话创建 / Turn 收尾 / 评分落库）、缓存不可变的版本 / 用例读取，改动面较大，本次未做。完整规模（1 万用例）尚未实测，按线性外推约 16 分钟 | 15.1 |
