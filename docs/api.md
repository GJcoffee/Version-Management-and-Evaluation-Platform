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

**草稿引用**：只有 `/versions/diff` 的两侧还接受草稿（取**已保存**的内容）：草稿 id（`drf_…`）或 `agent名:@草稿名`（如 `bot:@main`）。
`@` 开头不是合法的标签名，所以不会与 `agent名:标签` 混淆。

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
            "created_at": "2026-10-25T08:00:00.000Z", "updated_at": "2026-10-25T08:00:00.000Z", "latest_version": null, "tags": []},
  "draft": {"id": "drf_01…", "agent_id": "agt_01…", "name": "main", "spec": {"schema": "evalp/v1", "kind": "config", "…": "…"},
            "base_version_id": null, "revision": 1, "created_at": "…", "updated_at": "…"}
}
```

### GET /agents

`?include_archived=false&kind=config|code&q=关键字&limit&cursor` → `{"items": [Agent…], "next_cursor": null}`。
`latest_version` 为 `{"id", "seq", "spec_hash", "created_at"}` 或 `null`。
`tags` 是该 agent 全部版本标签的名称（字符串数组，按字典序，没有则为 `[]`），列表一次查询批量取出，不需要再逐个请求 `/agents/{id}/tags`
（要知道标签指向哪个版本时才用那个接口）。`GET /agents/{agent_ref}` 与 PATCH 的响应同样带 `tags`。

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

请求体可选：`{"spec": {...}}` 时对传入的 spec 做**干跑校验**（不保存、不改 revision）；无请求体时校验已保存的草稿。返回结构校验 + 业务校验的结果；通过时 `resolved_preview` 是固化后的规范化 Spec（即发布时会存储的内容），有错误时为 `null`。

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
`assets[].spec_path` 是资产引用在 Spec 里的位置，列表元素用**身份键**：`tools[query-order]`、`tools[knowledge_search].with.sources[faq]`、
`knowledge.inject[faq]`，与 `GET /versions/diff` 的 `changes[].path` 同一套写法（身份键有重复时退回下标，如 `knowledge.inject[1]`）。
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

`left` / `right` 可以是版本 id、`agent名:v12`、`agent名:标签`，也可以是草稿（见"草稿引用"：`drf_…` 或 `agent名:@草稿名`）。

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
  `knowledge.inject[资产名]`、`tools[knowledge_search].with.sources[资产名]`；其他列表（或身份键有重复时）按下标 `a[1]`。
  版本详情 `assets[].spec_path`、`GET /assets/{id}/references` 的 `versions[].spec_path` 用的是**同一套路径**，
  所以 `asset_changed` 的 `path` 可以直接在 `assets[]` 里按 `spec_path` 相等找到两侧的资产修订。
  （库里 `version_asset_refs.spec_path` 仍存下标路径，如 `tools[0]`，接口输出时换算；`validate` 报告里的 `errors[].path` 指向草稿里的位置，也仍是下标。）
- 资产引用在 `add`/`remove`/`replace` 里以 `{"kind","name","rev"}` 表示；内容哈希相同视为未变化（即使修订号不同）。
- 多行或超过 80 个字符的文本变更附 `content_diff`（unified diff，从 `@@` 开始，不含文件头）；文本类资产（prompt / tool / knowledge）的 `asset_changed` 同样附 `content_diff`。
- 前端用 Monaco DiffEditor 展示完整文本时，用 `GET /assets/revisions/{revision_id}` 取两侧内容。
- **草稿一侧**：取已保存的草稿内容（编辑器里未保存的修改看不到），按发布时的流程固化引用（`@latest` 取当前最新修订）并规范化（补齐默认值），
  所以和"现在发布会得到的 Spec"比较；校验不通过时和发布一样报错（400 `VALIDATION_ERROR` / `SPEC_INVALID`，`details.errors`）。
  草稿不存在 404，`agent名:@` 缺草稿名 400。代码型 Spec 的 `source.git` 是发布时才采集的，涉及草稿时两侧都忽略它。
  草稿一侧的摘要是 `{"id": "drf_…", "draft": true, "name": "main", "revision": 3, "spec_hash": "…"}`（没有 `seq`）；版本一侧不变。
  典型用法：`left=agent:v12&right=<草稿 id>` 得到草稿相对基础版本的改动（前端状态栏的改动数用 `summary` 各项之和）。

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

`versions[].spec_path` 与版本详情的 `assets[].spec_path` 相同（身份键路径，如 `tools[query-order]`）。

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

## 8. 会话与对话（P3-A）

设计依据：详设 6、7、9.4。前缀同样是 `/api/v1`；以下接口的错误码补充：

| HTTP | code | 场景 |
|---|---|---|
| 409 | `SESSION_BUSY` | 会话正在执行一轮，不能同时执行第二轮（`details.turn_id`） |
| 404 | `NOT_FOUND` | 会话 / 轮次 / 版本 / blob 不存在，或 blob 不允许读取 |
| 400 | `VALIDATION_ERROR` | 请求体不合法、`include` 取值不支持、blob 哈希格式不对 |

**执行失败不是 HTTP 错误**：agent 报错、超时都以 `200/202 + turn.status="error"|"timeout"` 返回，原因在 `turn.error = {code, message, retriable}`
（`code` 如 `TIMEOUT`、`AGENT_PROTOCOL_ERROR`、`MAX_STEPS_EXCEEDED`、`LLM_*`、`INTERRUPTED`）。会话 `status`：`idle` | `running` | `error`（最近一轮失败）。

### POST /sessions → 201
```json
{"version_id": "ver_…", "title": "可选", "variables": {"lang": "zh"}}
{"agent": "客服助手", "tag": "prod", "title": "…"}        // tag 可为标签、v12、latest（缺省 latest）
```
`version_id` 与 `agent` 二选一（`version_id` 也接受 `agent:v3` 这样的引用）。返回 `{"session": SessionOut}`，
`SessionOut = {id, agent_id, agent_name, version_id, version_seq, mode, title, status, last_seq, stats, variables, source_session_id, replay_mode, created_at, updated_at}`。
通过本接口创建的会话 `mode` 恒为 `chat`；`stats = {turns, prompt_tokens, completion_tokens, llm_calls, tool_calls, cost}`。
`variables` 是会话级模板变量（优先级：Spec < 会话 < 单轮）。标题缺省时取第一轮用户输入的前几十个字。

### PATCH /sessions/{id}
```json
{"title": "退货流程咨询"}
```
修改会话标题，返回 `{"session": SessionOut}`。标题去掉首尾空白后必须是 1 到 120 个字符，不能清空（不接受 `null`），也不接受其他字段；
空串、纯空白、超长、缺字段或有多余字段返回 400 `VALIDATION_ERROR`，会话不存在返回 404。会话正在执行时也可以改。

### GET /sessions
`?agent_id=（id 或名称）&version_id=&mode=chat,replay&q=标题关键字&limit=&cursor=`；`mode` 可逗号分隔，缺省为 `chat,replay`（**不含** `eval`、`judge`）。
新的在前，分页同 0 节。

### GET /sessions/{id}?include=turns,events
`include` 缺省 `turns`；传空字符串则只要会话信息。返回 `{"session", "turns": [TurnOut], "events": [AgentEvent] | null}`。
事件的 `payload` 序列化后超过 `events.payload_inline_limit_kb`（默认 32）KB 时，**整个 payload** 会被转存为 blob，库里和本接口返回的 `payload` 被替换成存根：
`{"$blob": "<sha256>", …}`——除 `$blob` 外只保留序列化后不超过 256 字节的顶层字段（如 `name`、`call_id`、`model`、`usage`、`status`），
体积大的字段（`arguments`、`output`、`messages`、`content`、`input` 等）都不在里面，也**不是**字段级替换。
取回原文：`GET /blobs/{hash}`（可加 `?session_id=` 加速校验）返回被替换前的**完整 payload**（`application/json`，与未转存时的 `payload` 完全一致）。
不超限的事件原样内联。实时 SSE 推送的是完整内容（不含存根）；只有从库里读取的事件（`GET /sessions/{id}?include=events`、SSE 回放）是存根。
`turns[].input` / `output` 存在 `turns` 表里，始终是完整的，不受影响。

`TurnOut = {id, session_id, idx, pair_index, status: running|ok|error|cancelled|timeout, input: Message, output: {final, messages, partial_text?} | null, usage, cost, latency_ms, ttft_ms, error, anchored_from_turn_id, started_at, finished_at}`。
`usage = {prompt_tokens, completion_tokens, llm_calls, tool_calls}`；`output.messages` 是本轮新增的 assistant / tool 消息（含工具调用），下一轮历史由它们拼接。

**取消 / 出错 / 超时的轮次保留部分文本**：`llm.delta` 不落库，但轮次以 `cancelled` / `error` / `timeout` 结束时，如果某次 LLM 调用已经流出了文本、
却没等到它的 `llm.response`，这部分文本会保存下来：`output = {"final": null, "messages": [], "partial_text": "…"}`（此时 `output` 非 null 但没有 `final`），
同一份文本也放在 `turn.end` 事件的 `payload.partial_text` 里，所以刷新页面后 SSE 回放仍能还原。已有 `llm.response` 的调用不算（内容已在事件里）；
超过 10000 个字符时截断并以 `…` 结尾；正常结束的轮次没有该字段。它不属于对话历史（`status != ok` 的轮次本来就不参与历史拼接）。

### POST /sessions/{id}/turns
```json
{"input": "你好" | {"role": "user", "content": "…"},
 "variables": {"lang": "en"},                       // 单轮变量，覆盖会话变量
 "tool_mocks": {"query_order": [{"match": {"order_id": "A001"}, "output": {"status": "已发货"}}, {"default": {"status": "未找到"}}]},
 "pair_index": null, "wait": false}
```
- 默认 **202**：轮次已创建并在后台执行，响应 `{"turn_id", "stream_url", "turn"}`（`turn.status="running"`）；`stream_url` 是带 `after_seq` 的 SSE 地址，用它订阅过程。
- `wait=true`（请求体或 `?wait=true`）：阻塞到本轮结束，返回 **200**，`turn` 是最终状态。客户端断开连接时本轮会被取消。
- 会话正在执行时返回 409 `SESSION_BUSY`。

### POST /turns/{id}/cancel → TurnOut
取消执行中的轮次，等它收尾后返回（`status="cancelled"`，没有 `error`；子进程型 agent 的整个进程树会被终止，HTTP 型会收到 `/evalp/cancel` 并断开连接）。
轮次已结束时原样返回，不报错。

### GET /sessions/{id}/stream（SSE）
先回放库里 `seq > after_seq` 的事件，再订阅实时事件；回放与实时之间不会重复也不会丢失。`after_seq` 与请求头 `Last-Event-ID` 同时给出时取较大者，断线重连时浏览器会自动带上它。
帧格式：
```
id: 12                       // 事件 seq；llm.delta 没有 id（不落库、重连后不重放）
event: tool.call             // 帧名：turn.start / llm.request / llm.delta / llm.response / tool.call / tool.result / span.* / log / message.assistant / agent.error / turn.end
data: {"seq":12,"turn_id":"trn_…","ts":"…","type":"tool.call","step_id":"s2","parent_step_id":"s1","payload":{…}}

event: idle                  // 没有轮次在运行（或正在运行的这一轮已结束且 turn.end 已落库）后发送，随后服务端关闭连接
data: {"status":"idle","last_seq":12}
```
**`error` 事件的帧名是 `agent.error`**（避开浏览器 `EventSource.onerror` 的同名事件，客户端不会再把它误判为断线）。只有 `event:` 这一行改名：
`data` 里的 `type` 仍是 `error`，库里的事件类型、`GET /sessions/{id}?include=events` 返回的 `type`、agent 对外协议（SDK / HTTP / 子进程）里的事件类型都不变。
客户端要用 `addEventListener("agent.error", …)` 监听，不再有名为 `error` 的帧。
空闲的会话回放完立即收到 `idle`。每 15 秒有一次 `: ping` 心跳注释帧。订阅者消费太慢（积压超过 1000 条）会被断开且没有 `idle` 帧——带 `Last-Event-ID` 重连即可从库里补齐。

### GET /blobs/{hash}[?session_id=]
读取被事件引用（`{"$blob": hash}`）的大 payload（`application/json`），或资产修订的内容（作为附件下载）。其他 blob 一律 404，避免枚举任意内容。

### POST /versions/{ref}/adapter-check → CheckReport
对一个版本跑 Adapter 协议合规自检（`ref` 同"版本引用"）。不写库、不产生会话，但会真实调用 agent（配置型会真实调用 LLM，产生少量费用）；耗时一般几秒到十几秒。
HTTP 状态恒为 200（版本不存在 404），agent 的任何问题都体现在报告里：
```json
{"version_id": "ver_…", "agent_name": "…", "kind": "code", "transport": "http", "ok": true,
 "summary": {"pass": 5, "skip": 1}, "duration_ms": 1234,
 "items": [{"name": "health", "title": "健康检查", "status": "pass|fail|warn|skip", "message": "…", "details": {}, "duration_ms": 8}]}
```
检查项顺序：`health`（健康检查）、`single`（单轮成功且回复非空）、`multi_turn`（第二轮带着历史；agent 在 `turn.start` 里报告的 `history_len` 要等于传入的条数，
没有报告则看 `llm.request` 是否带上了上一轮内容，都没有给 `warn`）、`timeout`（极短超时后以 `timeout` 结束并终止进程）、`cancel`（执行中取消后以 `cancelled` 结束并终止进程）、
`events`（逐条列出事件顺序 / 成对 / 协议违例等问题，`details.problems`）。agent 响应得比探测超时还快时 `timeout` / `cancel` 为 `skip`；`single` 失败时后续项为 `skip`。`ok` 表示没有 `fail`。

## 对比与重放（P3-C）

契约来源 `docs/contract-eval.md` 第 3 节；这里补充实现细节。对比组 id 前缀 `cmp_`。

### POST /compare → 201 CompareGroup
```json
{"kind": "session", "mode": "live", "left": {"version_id": "ver_a"}, "right": {"version_id": "customer-support:v13"}, "title": "…"}
{"kind": "session", "mode": "history", "left": {"session_id": "ses_x"}, "right": {"session_id": "ses_y"}}
```
- `live`：`version_id` 可写版本 id、`agent:vN`、`agent:标签`；自动创建两个 `mode=chat` 的会话（出现在会话列表里）。两侧可以是不同的 agent。
- `history`：两侧是已有会话（任意 mode），会话不存在返回 404。
- `replay` 模式只能由重放生成，不能直接创建。`kind` 目前只支持 `session`。
- 响应：`{id, kind, mode, left:{session_id, version_id, version_seq, agent_name}, right:{…}, title, created_at}`。

### GET /compare、GET /compare/{id}
列表按新到旧分页（只含 `kind=session`）；详情结构同上。

### POST /compare/{id}/turns → 202（仅 live 模式）
请求 `{"input": "…" | Message, "variables"?: {}}`，响应 `{"pair_index": 3, "left_turn_id": "trn_…", "right_turn_id": "trn_…"}`。
- 两侧并发执行，立即返回；前端分别订阅两侧的 `GET /sessions/{id}/stream`（可用响应里的 turn id 区分，或用 `after_seq` = 发送前的 `last_seq`）。
- `pair_index` = 两侧已有 pair_index 的最大值 + 1（从 0 开始）；两侧的轮次共享同一个 `pair_index`。
- 任意一侧会话正在执行 → **409 `SESSION_BUSY`**（`details.session_id` 是忙的那一侧；此时两侧都不会启动）。
- 非 live 模式 → 409 `INVALID_STATE`。一侧执行出错不影响另一侧（看各自 turn 的 `status`）。

### GET /compare/{id}/alignment
```json
{"turns": [{
  "pair": 0, "left_turn_id": "trn_…", "right_turn_id": "trn_…",
  "steps": [{"op": "equal", "left": "s1", "right": "s3", "sig": "llm"},
            {"op": "insert", "right": "s2", "sig": "tool:query_order"},
            {"op": "delete", "left": "s2", "sig": "tool:knowledge_search"}],
  "tool_args_diff": [{"left": "s2", "right": "s2",
                      "changes": [{"path": "order_id", "left": "A1", "right": "A2", "op": "change"}]}],
  "metrics": {"left":  {"latency_ms": 2100, "ttft_ms": 400, "tokens": 1830, "cost": 0.01, "llm_calls": 2, "tool_calls": 1},
              "right": {"latency_ms": 3400, "ttft_ms": 380, "tokens": 2410, "cost": 0.02, "llm_calls": 3, "tool_calls": 2}},
  "final_text": {"left": "…", "right": "…"},
  "status": {"left": "ok", "right": "error"},
  "input": {"left": "用户输入", "right": "用户输入"}
}]}
```
- **轮次对齐**：live 按 `pair_index`；history 按 `idx`；replay 时左侧（原会话）按 `idx`、右侧（新会话）按 `pair_index`（等于原轮次 idx）。`pair_index` 为空的轮次（单侧消息）不参与。只有一侧有的轮次，另一侧的 `*_turn_id`、`metrics`、`final_text`、`status` 为 null，`steps` 全为 delete 或 insert。
- **步骤对齐**：对两侧步骤签名序列（`llm` / `tool:{name}` / `span:{name}` / `final`）做 LCS（`difflib.SequenceMatcher`，取最长连续匹配，所以多出来的步骤不一定出现在开头），只有 `equal / insert / delete` 三种 op；签名不同的步骤是一个 delete 加一个 insert（delete 在前）。
- **step_id**：每轮从 `s1` 开始，必须与 turn id 组合才唯一；`message.assistant`（`final`）通常没有 step_id，这里固定记为 `"final"`；没有 step_id 的其他步骤记为 `"e{seq}"`。
- **tool_args_diff**：只列出参数有差异的、已对齐的工具调用；`changes[].path` 形如 `a.b`、`items[0]`，`op` 为 `change / add / remove`（缺的一侧值为 null）。工具结果的文本 diff 与 llm/final 的字符级 diff 由前端用 `final_text` 和 trace 自行完成。
- **metrics**：`tokens` = 输入 + 输出 token；轮次尚未结束时 `latency_ms` / `ttft_ms` 为 null，其余为已累计的值（结束前通常为 0）。
- 每次请求都会重新计算，包含正在执行的轮次的当前进度；前端可在两侧 `turn.end` 后重新拉取。

### POST /sessions/{id}/replay → 201
请求 `{"target_version_id": "ver_b | agent:v13 | agent:标签", "turns"?: [0, 2], "mode"?: "anchored"}`，响应 `{"session_id": "ses_new", "compare_id": "cmp_…"}`。
- 只支持锚定模式（`mode` 传 `free` 返回 400）。`turns` 是原会话的轮次 `idx`，缺省为全部（不含仍在执行的轮次）；不存在的 idx 返回 400。
- 立即返回；新会话（`mode=replay`、`source_session_id`=原会话、`replay_mode=anchored`、变量沿用原会话）和 `CompareGroup(mode=replay, left=原会话, right=新会话)` 在返回时已创建，前端可立刻打开对比页，随后订阅新会话的 SSE / 轮询 alignment 看到逐轮完成。尚未执行的轮次在 alignment 中 `right_turn_id` 为 null。
- 第 k 轮的上下文 = 原会话前 k 轮的历史（只含 `status=ok` 的轮次）；新轮次的 `anchored_from_turn_id` = 原轮次 id，`pair_index` = 原轮次 `idx`。某一轮失败不影响后面的轮次。
- 执行方式：后台 asyncio 任务，不经过调度器。同一次重放内按原轮次顺序**逐轮串行**（一个会话同一时间只能执行一轮），不同重放之间最多同时执行 4 个；服务关闭时取消未完成的重放。
- 没有"重放进度"接口：看新会话的 `turns` 数量与 `GET /sessions/{id}` 的 `status`，或订阅其 SSE。

## 评测（P3-B）

接口契约见 `docs/contract-eval.md` 第 1、2 节；下面只写实际行为和契约之外新增的内容。所有路径都在 `/api/v1` 下。

### 评估器配置（`evaluators[]`）

| type | 字段（括号内为默认值） |
|---|---|
| `exact_match` | `values`（空 = 与用例 `expected` 比较）、`normalize`（`["strip"]`；可选 `strip` `lower` `nfkc` `punct`）、`field`（`final`） |
| `contains` | `values`（空 = `expected`）、`mode`（`all` / `any`，默认 `all`）、`negate`（false）、`case_sensitive`（true） |
| `regex` | `pattern`、`flags`（字符串 `"im"` 或数组 `["i","m"]`，可选 `i m s x a`） |
| `llm_judge` | `provider`、`model`、`prompt`（默认 `asset://prompt/judge-default@latest`）、`scale`（`[1,5]`）、`pass_threshold`（缺省为量程的 60% 处，默认量程即 3.4 分）、`params` |

- 公共字段：`key`（缺省按类型生成，同一次评测内必须唯一）、`gate`（默认 true）。非法配置返回 400 `VALIDATION_ERROR`，`details.errors[].path` 形如 `evaluators[1].pattern`。
- 创建评测时，`llm_judge` 的 `prompt` 引用会被固化为具体修订（`asset://prompt/judge-default@1`），`GET /evals/{id}` 返回的是固化后的值。
- 内置评审 prompt `prompt/judge-default` 在应用首次启动时自动创建（中文；变量 `input`、`output`、`expected`、`rubric`，另有 `transcript`、`scale_min`、`scale_max`），要求模型只输出 `{"score": 整数, "reason": "…"}`。输出无法解析时重试 1 次，仍失败则该评分 `status=error`（不参与通过判定，计入 `stats.evaluators[key].errors`）。
- `score.detail.samples[0]` 含评审的请求摘要 `request_preview`（各变量前 200 字）、回复摘要 `reply_preview`、`attempts`、`raw` 和 `reason`；`detail.judge_sessions` 是评审会话 id。

### 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/evals` | 创建，201 `{batch_id, runs:[{id, version_id, status:"queued"}], estimate}`；每个版本一个 Run |
| GET | `/evals?agent_id=&version_id=&batch_id=&status=&limit=&cursor=` | 列表，新的在前；`status` 可逗号分隔；旧参数名 `agent`、`batch` 仍可用 |
| GET | `/evals/{id}` | Run 详情，带 `batch_runs`（同批次其他 Run） |
| GET | `/evals/{id}/items?status=&passed=&q=&class=&tag=&limit=&cursor=` | Item 列表（按用例顺序、重复序号）；`passed=true/false`（false 含执行失败）；`q` 模糊匹配 case_key、输入、输出 |
| GET | `/evals/{id}/items/{item_id}` | 单条，结构同上，另带 `output`（完整回复）、`turns`、`case_detail` |
| POST | `/evals/{id}/cancel` | 返回 Run；未开始的 Item 置 `cancelled`，正在执行的被取消，已完成的结果保留 |
| GET | `/evals/{id}/stream` | SSE |
| GET | `/evals/compare?left=&right=&threshold=0.05&class=&limit=&cursor=` | 两次评测逐用例对比 |

契约之外保留的接口（详设 8，一期前端 / CLI 不依赖）：`pause`、`resume`、`retry-failed`、`rescore`、`human-scores`、`gate`，以及 `POST /evals?dry_run=true`。

### EvalRun / EvalItem 的附加字段

- Run：`name`（同 `batch_name`）、`dataset_rev_id` / `dataset_name` / `dataset_rev`（另有嵌套的 `dataset`）、`case_filter`、`seed`、`budget`、`evaluators_hash`、`error`。`status` 在契约的取值之外还有 `paused`。
- `progress`：`total`、`pending`、`running`、`scoring`、`done`、`failed`、`cancelled`、`cached`、`cost_new`。`done` 不含 `failed`；缓存命中的 Item 在重新评分完成后同样计入 `done`。
- Item：契约字段之外有 `run_id`、嵌套的 `case`、`score`（门禁评估器分数的平均值；执行失败为 0）、`reused_from_item_id`、`started_at`、`finished_at`；`scores[]` 另带 `evaluator_type`、`gate`、`detail`、`judge_session_id`。
- `input_preview` / `output_preview` 最长 200 字符，超出加 `…`；`output_preview` 取会话最后一个成功轮次的最终回复。
- `error` 为 `{code, message, retriable}`。重试中的 Item 保持 `running`，`attempts` 递增。

### stats

契约字段之外：`cancelled`、`cost_new`（不含缓存命中）、`cost_wasted`（失败尝试的成本）、`pass_at_k`、`flaky_cases`、`by_tag`、`score_error_rate`；`evaluators[key]` 另有 `std`、`n`、`parse_failures`、`pending_human`。口径：

- 用例通过率 = 通过的重复次数 / 参与判定的重复次数；执行失败的 Item 算未通过；没有任何可用门禁评分（如评审解析失败）的 Item 不参与判定；`pass_rate` 是各用例通过率的平均值。
- `latency_ms`、`tokens` 只统计 `done` 的 Item，缓存命中的沿用来源 Item 的数值。

### SSE

事件的 `data` 都带 `run_id`：

- `eval.status`：`{status, stats, progress, started_at, finished_at, pause_reason}`；连接建立时先发一次当前快照，每次状态变化再发；
- `eval.progress`：`{progress}`，每个 Run 每秒最多 4 条；
- `eval.item_done`：`{item}`（完整 EvalItem），另有 `item_id`、`case_key`、`repeat_idx`、`status`、`passed`、`cached`、`latency_ms`、`cost`、`error`、`scores`（以 key 为索引的简表）；
- Run 到达终态（`completed` / `completed_with_errors` / `cancelled`）后发 `idle` 并关闭；连接时 Run 已结束的，只发 `eval.status` 和 `idle`。
- 另有 `eval.budget_exceeded`（预算暂停，一期不用）。

### 缓存

`cache_key = sha256(spec_hash | case_hash | repeat_idx | seed)`。`cache_policy=reuse`（默认）时，已有 `done` 状态的同 key Item 会被复用：新 Item 直接引用它的会话（`cached=true`，`reused_from_item_id`），不再调用被测 agent，只重新执行评估器；`refresh` 不复用。`seed` 不同则不命中。

### 调度、重试与重启恢复

- 任务持久化在 `jobs` 表；worker 数取设置 `scheduler.workers`，每个 Run 的并发上限为请求里的 `concurrency`；
- 可重试的错误（`TurnOut.error.retriable`，如模型 429 / 超时）按指数退避重新入队，最多 `scheduler.max_attempts` 次，超过后 Item 为 `failed`，Run 为 `completed_with_errors`；
- 进程重启：`running` 的任务和 Item 回到 `pending` 继续执行，已完成的 Item 不会重跑；被中断的会话标记为 `error`，其成本记在 `usage.failed_attempts` / `stats.cost_wasted`，不重复计入 `cost`；
- `GET /health` 的 `workers.busy` 为正在执行任务的 worker 数。

### 对比（`GET /evals/compare`）

返回契约结构（`left` / `right` 带 `stats`，`warnings` 为字符串数组，`summary`，`cases.items` / `cases.next_cursor`）。附加：

- `threshold` 回显；`class` 还接受 `unscored`（某一侧没有可用分数）和 `flaky`；另有 `tag`、`q`（case_key 模糊匹配）参数；`cursor` 为上一页最后一条的 `case_key`；
- `summary` 另有 `ci95`（配对 bootstrap 95% 置信区间）、`sign_test_p`、`wins` / `ties` / `losses`、`unscored`、`flaky`，以及 `metrics`（各指标 `{left, right, delta}` 的完整版）；契约的 `metric_deltas` 均为 `right - left`，`tokens_total` 为 prompt + completion；
- 用例行另有 `flaky`；`left` / `right` 的 `pass_rate` 为该用例通过的重复次数占比；
- 用例分数 = 各 gate 评估器分数的平均值（执行失败记 0 分），再对重复次数取平均；`|Δ| < threshold` 为 unchanged；排序：regressed、improved、unchanged、new、missing、unscored，同类按 `|Δ|` 从大到小。
