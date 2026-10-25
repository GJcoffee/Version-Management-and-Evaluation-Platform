# REST API（P2-A：Agent / 草稿 / 版本 / 资产 / 数据集 / 工作区同步）

面向前端和客户端开发者。设计依据：详设 4、5、10、11。

## 0. 通用约定

- 前缀 `/api/v1`；请求和响应都是 JSON（UTF-8）；鉴权见详设 11.1（`Authorization: Bearer {token}` 或 Cookie）。
- ID 带类型前缀：`agt_` agent、`drf_` 草稿、`ver_` 版本、`ast_` 资产、`arv_` 资产修订、`cas_` 用例。时间为 UTC ISO8601（毫秒，`Z` 结尾）。
- 分页：`?limit=50&cursor=…`（limit 1–200），响应 `{"items": [...], "next_cursor": "…" | null}`，默认最新的在前。
- 错误：`{"error": {"code", "message", "details"}}`。下表只列本模块会返回的错误码。

| HTTP | code | 场景 | details |
|---|---|---|---|
| 400 | `VALIDATION_ERROR` | 请求或 Spec 结构校验失败、资产内容不合法 | `errors: [{path, message}]`；工作区推送时可能带 `file` |
| 400 | `SPEC_INVALID` | Spec 业务校验失败（Provider / 模型 / 资产不存在、工具名重复、模板语法错误等） | `errors`、`warnings` |
| 400 | `GIT_DIRTY` | 代码型 agent 工作区有未提交改动且 `allow_dirty=false` | `repo`、`changed_files`、`total` |
| 400 | `CONTENT_MISSING` | 工作区 push 时，`content` 为空且服务端没有该 hash 的内容 | `missing: [{path, asset, hash}]` |
| 404 | `NOT_FOUND` | 资源不存在 | |
| 409 | `DRAFT_CONFLICT` | 草稿 revision 不一致 | `current_revision`（工作区 push 还有 `diff`、`server_spec`） |
| 409 | `ASSET_IN_USE` | 被版本或评测引用的资产不能硬删除 | `versions`、`evals`（计数） |
| 409 | `ASSET_DELETED` | 往已软删除的资产追加修订（先 restore） | |
| 409 | `AGENT_IN_USE` | 已有版本的 agent 不能删除（请归档） | `versions` |
| 409 | `CONFLICT` | 名称重复（agent、草稿） | `name` |
| 422 | `DATASET_PARSE_ERROR` | 数据集解析失败 | `lines`（出错的行号）、`errors: [{line, message}]`、`total_errors` |

**版本引用**：凡是接受"版本"的地方（`/versions/{ref}`、`/versions/diff`、`to-draft`、标签接口），除版本 id 外还可以写成
`agent名:v12`（第 12 号版本）、`agent名:标签`、`agent名:latest`（序号最大的版本）。`{agent_ref}` 可以是 agent 的 id 或名称。

## 1. AgentSpec

`GET /spec/schema` 返回 AgentSpec 的 JSON Schema（`kind` 为判别字段；`schema` 固定为 `evalp/v1`），供前端生成表单。

```json
{"$defs": {"AssetPin": {"…": "…"}, "BuiltinTool": {"…": "…"}}, "oneOf": [{"$ref": "#/$defs/ConfigSpec"}, {"$ref": "#/$defs/CodeSpec"}], "discriminator": {"propertyName": "kind", "mapping": {"config": "#/$defs/ConfigSpec", "code": "#/$defs/CodeSpec"}}}
```

配置型（省略的字段取默认值，默认值参与哈希，显式写默认值与省略等价）：

```json
{
  "schema": "evalp/v1", "kind": "config",
  "model": {"provider": "deepseek", "name": "deepseek-chat",
            "params": {"temperature": 0.3, "top_p": 1.0, "max_tokens": 4096, "seed": null}},
  "prompts": {"system": "asset://prompt/cs-system@latest", "variables": {"company": "示例公司"}},
  "tools": ["asset://tool/query-order@3",
            {"builtin": "knowledge_search", "with": {"sources": ["asset://knowledge/faq@latest"], "top_k": 5}}],
  "knowledge": {"inject": ["asset://knowledge/style-guide@latest"]},
  "runtime": {"max_steps": 12, "turn_timeout_s": 180, "parallel_tool_calls": true, "tool_timeout_s": 30}
}
```

代码型：

```json
{
  "schema": "evalp/v1", "kind": "code", "transport": "http",
  "http": {"base_url": "http://127.0.0.1:8001", "headers": {"Authorization": "Bearer ${env:MY_AGENT_TOKEN}"}, "timeout_s": 300},
  "source": {"repo": "/abs/path/to/agent", "capture_git": true, "allow_dirty": false},
  "params": {"retrieval_top_k": 8},
  "runtime": {"turn_timeout_s": 300}
}
```

`transport=subprocess` 时改用 `"subprocess": {"command": ["python", "-m", "my_agent.entry"], "cwd": "/abs", "env": {}}`；未使用的那一段会被丢弃。
`source.repo` 相对路径相对于服务进程的当前目录，建议客户端传绝对路径。`source.git` 由服务端在发布时写入，不要手写。
可选的顶层字段 `name` / `description` / `notes` 仅用于显示，不参与哈希。

**引用写法**（`prompts.system` 也可以直接写内联文本）：

| 写法 | 含义 | 可出现在 |
|---|---|---|
| `asset://{kind}/{name}@latest` | 最新修订 | 草稿 |
| `asset://{kind}/{name}@{rev}` | 指定修订 | 草稿、版本 |
| `file:相对路径` | 工作区本地文件 | 只用于 `POST /workspace/push` 的 spec |
| `${env:NAME}` | 运行时环境变量（只识别，不解析） | 任意字符串 |

位置约束：`prompts.system` → prompt；`tools[*]` → tool；`tools[*].with.sources[*]`、`knowledge.inject[*]` → knowledge。

**版本中的固化形式**（发布后，引用位置变成）：

```json
{"$asset": {"kind": "prompt", "name": "cs-system", "rev": 7, "hash": "<修订内容的 sha256>"}}
```

## 2. Agents

### POST /agents → 201

创建 agent，同时创建 `main` 草稿。`spec` 可省略（省略时生成该 kind 的最小模板；配置型会填入第一个已登记的 Provider 及其第一个模型）。
`name`：字母（含中文）、数字、`_`、`.`、`-`，最长 64，不含空格和冒号。

```json
{"name": "customer-support", "kind": "config", "description": "客服机器人", "spec": {"kind": "config", "model": {"provider": "deepseek", "name": "deepseek-chat"}}}
```
```json
{
  "agent": {"id": "agt_01…", "name": "customer-support", "kind": "config", "description": "客服机器人", "archived": false,
            "created_at": "2026-10-25T08:00:00.000Z", "updated_at": "2026-10-25T08:00:00.000Z", "latest_version": null},
  "draft": {"id": "drf_01…", "agent_id": "agt_01…", "name": "main", "spec": {"schema": "evalp/v1", "kind": "config", "…": "…"},
            "base_version_id": null, "revision": 1, "created_at": "…", "updated_at": "…"}
}
```

### GET /agents

`?include_archived=false&kind=config|code&q=关键字&limit&cursor` → `{"items": [Agent…], "next_cursor": null}`。
`latest_version` 为 `{"id", "seq", "spec_hash", "created_at"}` 或 `null`。

### GET /agents/{agent_ref} / PATCH /agents/{agent_ref} / DELETE /agents/{agent_ref}

PATCH 请求体（都可选）：`{"name": "新名字", "description": "…", "archived": true}` → 返回 Agent。
DELETE → 204；已有版本时返回 409 `AGENT_IN_USE`，请改为归档。

## 3. 草稿

### GET /agents/{agent_ref}/drafts

→ `[Draft…]`（数组，不分页）。

### POST /agents/{agent_ref}/drafts → 201

```json
{"name": "experiment", "spec": {"kind": "config", "…": "…"}, "from_version_id": null}
```

`spec` 与 `from_version_id`（用该版本的 Spec 作为初始内容，`@rev` 保持原样）都可省略；两者都省略时使用最小模板。→ `{"draft": Draft}`。

### GET /drafts/{id} → Draft

### PUT /drafts/{id}（乐观锁）

```json
{"spec": {"kind": "config", "…": "…"}, "expected_revision": 1}
```
```json
{"draft": {"id": "drf_01…", "revision": 2, "spec": {"…": "…"}, "base_version_id": null, "…": "…"}}
```

保存时只检查 `spec` 是对象、`kind` 与 agent 一致（缺省时自动补 `kind`、`schema`），草稿允许处于未完成状态。
`expected_revision` 不等于当前 revision 时返回 409：

```json
{"error": {"code": "DRAFT_CONFLICT", "message": "草稿已被修改（当前 revision=8）", "details": {"current_revision": 8, "updated_at": "…"}}}
```

### POST /drafts/{id}/validate

无请求体。返回结构校验 + 业务校验的结果；通过时 `resolved_preview` 是固化后的规范化 Spec（即发布时会存储的内容），有错误时为 `null`。

```json
{
  "ok": false,
  "errors": [{"path": "model.provider", "message": "Provider 不存在: deepseek"},
             {"path": "tools[0]", "message": "资产不存在: tool/query-order"}],
  "warnings": [{"path": "prompts.system", "message": "模板使用了未定义的变量: company（请在 prompts.variables 中定义，或运行时传入）"}],
  "resolved_preview": null
}
```

结构错误（字段缺失、类型不对、未知字段）会先单独返回；通过后才做业务校验：Provider 与模型存在（Provider 的 `models` 为空时只给警告）、
引用的资产存在且未删除、tool 资产内容能解析、工具名不重复、prompt 模板语法正确。

### POST /drafts/{id}/publish

```json
{"expected_revision": 3, "notes": "调低温度", "via": "ui"}
```

`via`：`ui | cli | mcp | api`（默认 `api`）。整个发布在一个事务内完成；同一 agent 下规范化后的 `spec_hash` 相同时返回已有版本。

```json
{"version": {"id": "ver_01…", "agent_id": "agt_01…", "seq": 13, "spec_hash": "ab12…", "spec": {"…": "固化后的规范化 Spec"},
             "source": {}, "parent_version_id": "ver_00…", "from_draft_id": "drf_01…", "notes": "调低温度", "created_via": "ui",
             "created_at": "…", "tags": [],
             "assets": [{"spec_path": "prompts.system", "kind": "prompt", "name": "cs-system", "rev": 7, "asset_revision_id": "arv_01…"}]},
 "existed": false}
```

错误：409 `DRAFT_CONFLICT`、400 `VALIDATION_ERROR`（结构）、400 `SPEC_INVALID`（业务）、400 `GIT_DIRTY`。
代码型且 `capture_git=true` 时 `source` 为 `{"git_commit", "git_branch", "dirty", "patch_blob"}`（`dirty` 且 `allow_dirty` 时 `patch_blob` 是补丁的 blob 哈希，补丁哈希参与 `spec_hash`）。

## 4. 版本

### GET /agents/{agent_ref}/versions

分页，新版本在前。条目没有 `spec` 和 `assets`，有 `tags`：

```json
{"items": [{"id": "ver_01…", "agent_id": "agt_01…", "seq": 13, "spec_hash": "ab12…", "source": {}, "parent_version_id": "ver_00…",
            "from_draft_id": "drf_01…", "notes": "调低温度", "created_via": "ui", "created_at": "…", "tags": ["prod"]}],
 "next_cursor": null}
```

### GET /versions/{ref}

→ 同 publish 响应里的 `version`（含 `spec`、`tags`、`assets`）。`ref` 见"版本引用"。

### POST /versions/{ref}/tags

`{"tag": "prod"}` → `{"agent_id": "agt_01…", "tag": "prod", "version_id": "ver_01…", "updated_at": "…"}`。
UPSERT：同一个标签会从旧版本移到这个版本。标签名：字母、数字、`_`、`.`、`-`，最长 64；不能是 `latest`，也不能形如 `v12`。

### DELETE /versions/{ref}/tags/{tag} → 204

### GET /agents/{agent_ref}/tags → `[{"agent_id", "tag", "version_id", "updated_at"}]`

### POST /versions/{ref}/to-draft

`{"draft_name": "main"}`（可省略，默认 `main`）。用该版本的 Spec 覆盖草稿（不存在则新建），`@rev` 保持原样，`base_version_id` 设为该版本，已存在的草稿 revision +1。
→ `{"draft": Draft}`。

### GET /versions/diff?left=&right=

`left` / `right` 可以是版本 id、`agent名:v12`、`agent名:标签`。

```json
{
  "left":  {"id": "ver_01…", "seq": 12, "spec_hash": "…"},
  "right": {"id": "ver_02…", "seq": 13, "spec_hash": "…"},
  "changes": [
    {"path": "model.params.temperature", "op": "replace", "left": 0.3, "right": 0.7},
    {"path": "tools[query-order]", "op": "asset_changed", "left": {"rev": 3}, "right": {"rev": 4},
     "content_diff": {"format": "unified", "text": "@@ -3,7 +3,8 @@ …"}},
    {"path": "prompts.system", "op": "replace", "left": "第一行\n第二行", "right": "第一行\n第二行（改）",
     "content_diff": {"format": "unified", "text": "@@ -1,2 +1,2 @@ …"}},
    {"path": "tools[extra]", "op": "add", "right": {"kind": "tool", "name": "extra", "rev": 1}},
    {"path": "runtime", "op": "remove", "left": {"max_steps": 3}}
  ],
  "summary": {"add": 1, "remove": 1, "replace": 2, "asset_changed": 1}
}
```

- `op`：`add` / `remove` / `replace` / `asset_changed`；`path` 用点号连接键名；列表元素按身份键匹配：`tools[工具资产名]`、`tools[knowledge_search]`（内置工具）、
  `knowledge.inject[资产名]`、`tools[knowledge_search].with.sources[资产名]`；其他列表按下标 `a[1]`。
- 资产引用在 `add`/`remove`/`replace` 里以 `{"kind","name","rev"}` 表示；内容哈希相同视为未变化（即使修订号不同）。
- 多行或超过 80 个字符的文本变更附 `content_diff`（unified diff，从 `@@` 开始，不含文件头）；文本类资产（prompt / tool / knowledge）的 `asset_changed` 同样附 `content_diff`。
- 前端用 Monaco DiffEditor 展示完整文本时，用 `GET /assets/revisions/{revision_id}` 取两侧内容。

## 5. 资产

资产名：只允许小写字母、数字、连字符，首尾必须是字母或数字，最长 64。`kind`：`prompt | tool | knowledge | dataset | file`。

内容校验（失败 400 `VALIDATION_ERROR`，数据集 422）：

| kind | 校验 | `revision.meta` |
|---|---|---|
| prompt | UTF-8；Jinja2 模板语法可解析 | `{"variables": ["now", "variables"]}` |
| tool | YAML / JSON 解析为 ToolDefinition，`parameters` 是合法的 JSON Schema | `{"tool_name": "query_order", "impl_type": "http"}` |
| knowledge | 大小 ≤ 20MB；`content_type=application/jsonl` 时逐行校验 `{id,text,meta}` | `{"format": "text"}` / `{"format": "jsonl", "doc_count": 3}` |
| dataset | JSONL / CSV 逐行解析，见 §6 | `{"case_count", "schema_warnings", "format"}` |
| file | 大小 ≤ 100MB | `{}` |

`content_type` 省略时按 kind 取默认值（prompt/knowledge `text/markdown`，tool `application/x-yaml`（以 `{` 开头为 `application/json`），dataset `application/jsonl`，file `application/octet-stream`）。

### POST /assets

`content`（文本）与 `blob_upload_id`（见 `/assets/upload`）二选一。同名资产已存在时等价于追加修订（已软删除的同名资产会被恢复）。
新建资产返回 201，否则 200。

```json
{"kind": "prompt", "name": "cs-system", "description": "客服系统提示词", "content": "你是客服。{{ variables.company }}", "content_type": "text/markdown", "message": "初版"}
```
```json
{
  "asset": {"id": "ast_01…", "kind": "prompt", "name": "cs-system", "description": "客服系统提示词", "latest_rev": 1, "deleted_at": null,
            "created_at": "…", "latest": {"id": "arv_01…", "asset_id": "ast_01…", "rev": 1, "blob_hash": "5dc6…", "content_type": "text/markdown",
                                          "size": 30, "meta": {"variables": ["variables"]}, "message": "初版", "created_at": "…"}},
  "revision": {"id": "arv_01…", "asset_id": "ast_01…", "rev": 1, "blob_hash": "5dc6…", "content_type": "text/markdown", "size": 30,
               "meta": {"variables": ["variables"]}, "message": "初版", "created_at": "…"},
  "existed": false,
  "created": true
}
```

**去重**：内容与该资产已有修订相同时不新建修订，返回已有修订，`existed=true`。如果相同的是更早的修订（改回旧内容），`latest_rev` 会指回该修订；
修订号只增不减，下一个新修订取当前最大修订号 + 1。

### POST /assets/upload（multipart，字段名 `file`，≤ 100MB）

```json
{"blob_upload_id": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08", "size": 4}
```

`blob_upload_id` 就是内容的 sha256。创建资产或修订时用它代替 `content`；未被引用的上传内容 24 小时后被 GC。

### GET /assets

`?kind=&q=关键字&include_deleted=false&limit&cursor` → `{"items": [Asset…], "next_cursor": null}`。默认不含已软删除的。

### GET /assets/{id} → Asset　PATCH /assets/{id} `{"description": "…"}` → Asset

### POST /assets/{id}/restore → Asset（恢复软删除）

### DELETE /assets/{id}

默认软删除（列表不再显示；已有版本仍可读取其内容）；`?hard=true` 物理删除，被版本或评测引用时返回 409：

```json
{"deleted": "soft", "id": "ast_01…", "versions": 1, "evals": 0}
```
```json
{"error": {"code": "ASSET_IN_USE", "message": "资产被 1 个版本、0 个评测引用，无法硬删除（可软删除）", "details": {"versions": 1, "evals": 0}}}
```

### 修订

- `GET /assets/{id}/revisions` → 分页（新修订在前）`{"items": [Revision…], "next_cursor"}`。
- `POST /assets/{id}/revisions`：`{"content" | "blob_upload_id", "content_type"?, "message"?, "column_map"?}` → 同 `POST /assets` 的响应（`created` 恒为 false）；新修订返回 201，去重命中返回 200。
- `GET /assets/{id}/revisions/{rev}`（`rev` 为整数或 `latest`）和 `GET /assets/revisions/{revision_id}` → 修订及内容：

```json
{"id": "arv_01…", "asset_id": "ast_01…", "rev": 2, "blob_hash": "…", "content_type": "text/markdown", "size": 30, "meta": {}, "message": null,
 "created_at": "…", "kind": "prompt", "name": "cs-system", "content": "你是客服。", "content_available": true}
```

  非 UTF-8 内容或大于 5MB 时 `content` 为 `null`、`content_available=false`，用 `GET /assets/revisions/{revision_id}/raw` 下载（一律 `attachment`）。

### GET /assets/diff?left=arv_…&right=arv_…

两个修订必须同类型。文本类（含可解码为 UTF-8 的 file）：

```json
{"kind": "prompt", "type": "text", "identical": false,
 "left": {"id": "arv_01…", "rev": 1, "name": "cs-system", "…": "…"}, "right": {"id": "arv_02…", "rev": 2, "name": "cs-system", "…": "…"},
 "content_diff": {"format": "unified", "text": "@@ -1,2 +1,3 @@\n …"}}
```

二进制：`"type": "binary", "content_diff": null, "sizes": {"left": 3, "right": 4}`。数据集：

```json
{"kind": "dataset", "type": "dataset", "identical": false, "left": {"…": "…"}, "right": {"…": "…"},
 "summary": {"added": 1, "removed": 1, "modified": 2, "unchanged": 5, "left_count": 8, "right_count": 8},
 "added": [{"case_key": "new-1", "input": {"turns": ["新增"]}, "tags": []}],
 "removed": [{"case_key": "refund-002", "input": {"turns": ["…"]}, "tags": ["refund"]}],
 "modified": [{"case_key": "refund-001", "changed_fields": ["expected"], "left": {"expected": "旧"}, "right": {"expected": "新"}}],
 "truncated": false}
```

`changed_fields` 取自 `input / expected / rubric / tags / metadata`；明细每类最多 500 条（`truncated`）。

### GET /assets/{id}/references?rev=

```json
{
  "asset": {"id": "ast_01…", "kind": "prompt", "name": "cs-system", "…": "…"},
  "versions": [{"version_id": "ver_01…", "agent_id": "agt_01…", "agent_name": "customer-support", "seq": 13, "rev": 7,
                "revision_id": "arv_01…", "spec_path": "prompts.system"}],
  "evals": [{"run_id": "evr_01…", "batch_id": "evb_01…", "status": "done", "agent_id": "agt_01…", "version_id": "ver_01…",
             "dataset_rev_id": "arv_01…", "rev": 2}],
  "drafts": [{"draft_id": "drf_01…", "draft_name": "main", "agent_id": "agt_01…", "agent_name": "customer-support"}],
  "in_use": true
}
```

`in_use` 只看版本和评测（草稿不阻止删除）；`?rev=` 限定修订号。

## 6. 数据集

数据集是 `kind=dataset` 的资产，修订内容为 JSONL 或 CSV，导入时解析为用例（`eval_cases`）。

每行（JSONL）：

```json
{"case_key": "refund-001", "input": "我要退货，订单 A001", "expected": "应说明退货流程", "rubric": "提到 7 天无理由", "tags": ["refund", "p0"],
 "metadata": {"variables": {"company": "测试公司"}, "tool_mocks": {"query_order": [{"match": {"order_id": "A001"}, "output": {"status": "已签收"}}]}}}
```

- `input`：字符串（单轮）、字符串数组（多轮），统一存为 `{"turns": [...]}`；
- 缺少 `case_key` 时用 `sha256(input)[:12]` 自动生成，并在 `warnings` 中给出 `MISSING_CASE_KEY`；
- 未知字段放进 `metadata`（并警告 `UNKNOWN_FIELD`）；顶层的 `variables` / `tool_mocks` 等价于写在 `metadata` 里；
- `case_hash = sha256(canonical({"input", "variables", "tool_mocks"}))`，`expected` / `rubric` / `tags` / `case_key` 不参与。

### POST /datasets/import

新建数据集返回 201，同名数据集已存在时新增修订（200；内容相同则 `existed=true`）。

```json
{"name": "regression", "format": "jsonl", "content": "{\"case_key\":\"a\",\"input\":\"你好\"}\n", "description": "回归集", "message": "初版"}
```

CSV 需要 `column_map`（`{字段名: 列名}`）；字段名可用 `case_key / input / expected / rubric / tags / metadata / variables / tool_mocks`，`input` 的列名可以是数组（多轮）。
`tags` 列用 `;` 分隔；`metadata / variables / tool_mocks` 列是 JSON 文本。省略 `column_map` 时按同名列。

```json
{"name": "csv-ds", "format": "csv", "content": "问题,参考答案,标签(;分隔)\n我要退货,说明退货流程,refund;p0\n",
 "column_map": {"input": "问题", "expected": "参考答案", "tags": "标签(;分隔)"}}
```
```json
{
  "asset": {"id": "ast_01…", "kind": "dataset", "name": "csv-ds", "latest_rev": 1, "…": "…"},
  "revision": {"id": "arv_01…", "rev": 1, "content_type": "text/csv", "meta": {"case_count": 1, "schema_warnings": [], "format": "csv", "column_map": {"input": "问题"}}, "…": "…"},
  "case_count": 1,
  "warnings": [{"code": "MISSING_CASE_KEY", "message": "缺少 case_key，已用 sha256(input)[:12] 自动生成；输入文本变化后跨修订对比可能对不上", "count": 1, "lines": [2]}],
  "existed": false,
  "created": true
}
```

解析失败（422）：

```json
{"error": {"code": "DATASET_PARSE_ERROR", "message": "数据集解析失败（共 3 处错误）：第 2 行：不是合法的 JSON（…）",
           "details": {"lines": [2, 3, 4], "errors": [{"line": 2, "message": "不是合法的 JSON（…）"}], "total_errors": 3}}}
```

`details.lines` 最多列出 100 个；`POST /assets`（`kind=dataset`）走同样的解析。

### GET /datasets/{rev_id}/cases

`rev_id` 是数据集的修订 id（`arv_…`）。`?tag=p0&tag=refund&case_key=a&q=模糊&limit&cursor`：`tag` 可重复（含任一）；`tag` 与 `case_key` 同时给出时取交集。按 `idx` 升序分页。

```json
{"items": [{"id": "cas_01…", "dataset_rev_id": "arv_01…", "case_key": "refund-001", "idx": 0, "input": {"turns": ["我要退货，订单 A001"]},
            "expected": "应说明退货流程", "rubric": null, "tags": ["refund", "p0"], "metadata": {"variables": {"company": "测试公司"}},
            "case_hash": "…"}],
 "next_cursor": null}
```

## 7. 工作区同步

客户端（CLI / MCP）读取本地文件后调用，服务端在**一个事务内**完成 10.2 节第 1 到 3 步。

### POST /workspace/push

```json
{
  "agent_name": "customer-support",
  "agent_kind": "config",
  "draft_name": "main",
  "spec": {"kind": "config", "model": {"provider": "deepseek", "name": "deepseek-chat"},
           "prompts": {"system": "file:prompts/system.md"}, "tools": ["file:tools/query.yaml"]},
  "files": [
    {"path": "prompts/system.md", "asset": "prompt/cs-system", "hash": "<sha256>", "content": "你是客服助手。\n"},
    {"path": "tools/query.yaml", "asset": "tool/query", "hash": "<sha256>"},
    {"path": "files/logo.png", "asset": "file/logo", "hash": "<sha256>", "content": "<base64>", "encoding": "base64"}
  ],
  "expected_revision": 3,
  "force": false
}
```

- `files[].hash` 是文件内容的 sha256（十六进制）；`content` 为空表示客户端认为服务端已有该 hash 的内容（`encoding` 默认 `utf-8`，二进制用 `base64`）。
  可选字段：`content_type`、`column_map`（数据集 CSV）。
- agent 不存在时创建（`agent_kind`、`spec.kind`、默认 `config` 依次决定类型）；草稿不存在时创建（新建 agent 且 `draft_name` 不是 `main` 时同时建一个 `main` 草稿）。
- 对每个文件：与资产当前最新修订的 hash 相同 → `skipped`；否则创建修订 → `uploaded`（hash 在该资产的旧修订里 → 把最新指针指回去，`existed=true`；
  `content` 为空但库里有这个 hash 的内容 → 直接用它创建修订）。
- spec 中引用位置上的 `file:路径` 改写为 `asset://kind/name@latest`；指向没有出现在 `files` 里的文件 → 400。
- 草稿已存在时必须带 `expected_revision`，不一致返回 409 `DRAFT_CONFLICT`（`details` 里有 `current_revision`、`server_spec` 和服务端与本地的 `diff`，格式同版本 diff，左侧是服务端）；
  `force=true` 跳过检查。冲突或任何错误都会回滚整个事务（包括已创建的资产修订）。
- `content` 为空且服务端没有该内容 → 400 `CONTENT_MISSING`，`details.missing` 列出缺少内容的文件，客户端带上 `content` 重试。

```json
{
  "agent_id": "agt_01…",
  "agent_created": true,
  "draft": {"id": "drf_01…", "name": "main", "revision": 1, "spec": {"prompts": {"system": "asset://prompt/cs-system@latest"}, "…": "…"}, "…": "…"},
  "uploaded": [{"path": "prompts/system.md", "asset": "prompt/cs-system", "hash": "…", "rev": 1, "revision_id": "arv_01…",
                "asset_id": "ast_01…", "asset_created": true, "existed": false}],
  "skipped": [{"path": "tools/query.yaml", "asset": "tool/query", "hash": "…", "rev": 3, "asset_id": "ast_02…"}]
}
```

### POST /workspace/pull

`asset_map` 是 `{"kind/name": "本地路径"}`。

```json
{"agent_name": "customer-support", "draft_name": "main", "asset_map": {"prompt/cs-system": "prompts/system.md", "tool/query": "tools/query.yaml"}}
```
```json
{
  "agent_id": "agt_01…",
  "draft": {"id": "drf_01…", "revision": 4, "spec": {"prompts": {"system": "asset://prompt/cs-system@latest"}, "…": "…"}, "…": "…"},
  "local_spec": {"prompts": {"system": "file:prompts/system.md"}, "…": "…"},
  "assets": [{"asset": "prompt/cs-system", "path": "prompts/system.md", "rev": 7, "hash": "…", "content_type": "text/markdown",
              "encoding": "utf-8", "content": "你是客服助手。\n"}],
  "missing": ["prompt/not-exist"]
}
```

`local_spec` 是把 `asset_map` 中有映射的 `@latest` 引用还原为 `file:路径` 后的 Spec（指定了修订号的引用保持原样）；`assets` 是映射中各资产的最新内容（二进制为 `base64`）；
不存在或已删除的资产列在 `missing` 里。agent 或草稿不存在 → 404。
