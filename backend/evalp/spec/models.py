"""AgentSpec 的 Pydantic 模型（详设 4.1、4.2）。

``kind`` 是判别字段（config / code）；``schema`` 固定为 ``evalp/v1``。

草稿中的资产引用是字符串（``asset://kind/name@latest|rev``）；固化后的版本 Spec 中，引用位置是
``{"$asset": {"kind","name","rev","hash"}}``。两种形式都能被同一套模型解析，所以
``parse_spec(version.spec)`` 可以直接用于运行时。

引用可以出现的位置：``prompts.system``（也可以是内联文本）、``tools[*]``、
``tools[*].with.sources[*]``、``knowledge.inject[*]``。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from evalp.spec.refs import (
    ASSET_KEY,
    AssetKindName,
    RefError,
    is_asset_ref,
    is_file_ref,
    parse_asset_ref,
)
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "evalp/v1"
BUILTIN_TOOLS: tuple[str, ...] = ("knowledge_search",)

TAG_STR = "t:str"
TAG_BUILTIN = "t:builtin"
TAG_PIN = "t:pin"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 资产引用


class AssetPin(_Model):
    """固化后的资产修订：``hash`` 是该修订内容（blob）的 sha256。"""

    kind: AssetKindName
    name: str
    rev: int = Field(ge=1)
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResolvedRef(_Model):
    """``{"$asset": {...}}``：版本 Spec 中引用的固化形式。"""

    asset: AssetPin = Field(alias=ASSET_KEY)


def _check_ref_str(kind: str | None):
    """生成校验函数：字符串必须是指定类型的资产引用（``kind`` 为 None 时不限类型）。"""

    def check(v: str) -> str:
        if is_file_ref(v):
            raise ValueError("file: 引用只能出现在工作区的 agent.yaml 中（push 时会转换成资产引用）")
        try:
            ref = parse_asset_ref(v)
        except RefError as e:
            raise ValueError(str(e)) from None
        if kind is not None and ref.kind != kind:
            raise ValueError(f"这里需要 {kind} 类型的资产，实际是 {ref.kind}: {v}")
        return v.strip()

    return check


def _check_pin_kind(kind: str | None):
    def check(v: ResolvedRef) -> ResolvedRef:
        if kind is not None and v.asset.kind != kind:
            raise ValueError(f"这里需要 {kind} 类型的资产，实际是 {v.asset.kind}")
        return v

    return check


def _tag_of(v: Any) -> str:
    """可调用判别器：区分引用字符串 / 内置工具 / 固化引用。"""
    if isinstance(v, ResolvedRef):
        return TAG_PIN
    if isinstance(v, BuiltinTool):
        return TAG_BUILTIN
    if isinstance(v, dict):
        return TAG_PIN if ASSET_KEY in v else TAG_BUILTIN
    return TAG_STR


def _ref_tag(v: Any) -> str:
    if isinstance(v, ResolvedRef):
        return TAG_PIN
    if isinstance(v, dict):
        return TAG_PIN
    return TAG_STR


def _pin_type(kind: str | None):
    return Annotated[ResolvedRef, AfterValidator(_check_pin_kind(kind))]


def _ref_union(kind: str | None):
    """``asset://kind/...`` 字符串 或 固化引用（``kind`` 为 None 时不限类型）。"""
    return Annotated[
        Union[  # noqa: UP007
            Annotated[Annotated[str, AfterValidator(_check_ref_str(kind))], Tag(TAG_STR)],
            Annotated[_pin_type(kind), Tag(TAG_PIN)],
        ],
        Discriminator(_ref_tag),
    ]


ToolRef = _ref_union("tool")
KnowledgeRef = _ref_union("knowledge")
SnapshotRef = _ref_union(None)  # 快照里的资产可以是任意类型（按 snapshot.map 入库）


def _check_system(v: Any) -> Any:
    if isinstance(v, str) and is_file_ref(v):
        raise ValueError("file: 引用只能出现在工作区的 agent.yaml 中（push 时会转换成资产引用）")
    if isinstance(v, str) and is_asset_ref(v):
        _check_ref_str("prompt")(v)
    return v


SystemPrompt = Annotated[
    Union[  # noqa: UP007
        Annotated[Annotated[str, AfterValidator(_check_system)], Tag(TAG_STR)],
        Annotated[_pin_type("prompt"), Tag(TAG_PIN)],
    ],
    Discriminator(_ref_tag),
]


# ---------------------------------------------------------------- 配置型


class ModelParams(_Model):
    """模型参数；允许额外字段（原样透传给兼容 OpenAI 协议的服务）。"""

    model_config = ConfigDict(extra="allow")

    temperature: float | None = Field(0.3, ge=0)
    top_p: float | None = Field(1.0, ge=0, le=1)
    max_tokens: int | None = Field(4096, ge=1)
    seed: int | None = None


class ModelSpec(_Model):
    provider: str = Field(min_length=1, description="providers.name")
    name: str = Field(min_length=1, description="模型名")
    params: ModelParams = Field(default_factory=ModelParams)


class PromptsSpec(_Model):
    system: SystemPrompt = Field("", description="内联文本或 asset://prompt/… 引用")
    variables: dict[str, Any] = Field(default_factory=dict, description="Jinja2 变量")


class BuiltinWith(_Model):
    sources: list[KnowledgeRef] = Field(default_factory=list)
    top_k: int = Field(5, ge=1, le=100)


class BuiltinTool(_Model):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    builtin: Literal["knowledge_search"]
    with_: BuiltinWith = Field(default_factory=BuiltinWith, alias="with")


ToolItem = Annotated[
    Union[  # noqa: UP007
        Annotated[Annotated[str, AfterValidator(_check_ref_str("tool"))], Tag(TAG_STR)],
        Annotated[BuiltinTool, Tag(TAG_BUILTIN)],
        Annotated[_pin_type("tool"), Tag(TAG_PIN)],
    ],
    Discriminator(_tag_of),
]


class KnowledgeSpec(_Model):
    inject: list[KnowledgeRef] = Field(default_factory=list, description="全文注入的知识资产")


class ConfigRuntime(_Model):
    max_steps: int = Field(12, ge=1, description="单轮内 LLM 调用次数上限")
    turn_timeout_s: float = Field(180, gt=0)
    parallel_tool_calls: bool = True
    tool_timeout_s: float = Field(30, gt=0)


# ---------------------------------------------------------------- 代码型


class HttpTransport(_Model):
    base_url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = Field(300, gt=0)


class SubprocessTransport(_Model):
    command: list[str] = Field(min_length=1)
    cwd: str = "."
    env: dict[str, str] = Field(default_factory=dict)


class GitPin(_Model):
    """发布时由服务端写入的 git 信息（参与 spec_hash）：commit 与补丁哈希。"""

    commit: str
    patch_hash: str | None = None


class SourceSpec(_Model):
    repo: str = "."
    capture_git: bool = True
    allow_dirty: bool = False
    git: GitPin | None = Field(None, json_schema_extra={"readOnly": True}, description="发布时由服务端填充")


class CodeRuntime(_Model):
    turn_timeout_s: float = Field(300, gt=0)


class SnapshotRule(_Model):
    """快照入库规则：``glob`` 匹配目录内相对路径，命中的文件作为 ``kind`` 类型的资产入库。

    ``name`` 是资产名模板，变量：``{stem}``（文件名去扩展名）、``{stem_nover}``（再去掉末尾的版本号，
    如 ``-0.5.3`` / ``-v1.2``）、``{agent}``（agent 名）、``{path}``（目录内相对路径去扩展名）。
    变量展开后统一为合法的资产名（小写字母、数字、连字符）。

    ``name`` 与 ``id`` 的值以 ``$.`` 开头时，表示从 JSON 文件内容按点路径取值
    （如 ``$.name``、``$.meta.skill_id``；
    目录 / ZIP 形式的 skill 改为从包内 SKILL.md 的 YAML frontmatter 取值），
    否则按上述模板展开。``display`` 是显示名（可含中文），省略时取 ``name`` 的展开原文；
    ``id`` 是来源平台上的稳定 ID，相同的 ``(kind, id)`` 始终是同一个资产，
    文件改名 / 资产名变了会自动改名而不是新建资产。
    """

    glob: str = Field(min_length=1, description="匹配目录内相对路径；* 不跨目录，** 跨目录")
    kind: AssetKindName
    name: str = Field(min_length=1, description="资产名模板，或 $.点路径（取 JSON 文件内容）")
    id: str | None = Field(
        None, min_length=1, description="来源 ID：$.点路径（取 JSON 文件内容）或模板；省略时按资产名识别"
    )
    display: str | None = Field(
        None,
        min_length=1,
        description="显示名：$.点路径或模板；省略时取 name 的展开结果（原名是中文时用它，name 改用文件名）",
    )


class SnapshotSpec(_Model):
    """版本快照：只做记录、不参与运行。``dir`` 下的全部文件在 push 时作为资产上传并固化到版本里。

    先按 ``map`` 的顺序取第一条匹配的规则决定资产类型与名称；没有命中的文件存为 file 资产。
    """

    dir: str = Field(min_length=1, description="本地目录（相对项目根）")
    map: list[SnapshotRule] = Field(default_factory=list, description="入库规则，按顺序取第一条匹配的")
    files: dict[str, SnapshotRef] = Field(
        default_factory=dict, description="目录内相对路径 -> 资产引用；push 时由客户端按目录内容填充"
    )


# ---------------------------------------------------------------- 顶层


class _SpecBase(_Model):
    """公共字段。``name`` / ``description`` / ``notes`` 仅用于显示，不参与哈希。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["evalp/v1"] = Field(SCHEMA_VERSION, alias="schema")
    name: str | None = Field(None, description="显示用，不参与哈希")
    description: str | None = Field(None, description="显示用，不参与哈希")
    notes: str | None = Field(None, description="备注，不参与哈希")


class ConfigSpec(_SpecBase):
    """配置型 agent（``kind: config``）。"""

    kind: Literal["config"] = "config"
    model: ModelSpec
    prompts: PromptsSpec = Field(default_factory=PromptsSpec)
    tools: list[ToolItem] = Field(default_factory=list)
    knowledge: KnowledgeSpec = Field(default_factory=KnowledgeSpec)
    runtime: ConfigRuntime = Field(default_factory=ConfigRuntime)


class CodeSpec(_SpecBase):
    """代码型 agent（``kind: code``）。"""

    kind: Literal["code"] = "code"
    transport: Literal["http", "subprocess"]
    http: HttpTransport | None = None
    subprocess: SubprocessTransport | None = None
    source: SourceSpec = Field(default_factory=SourceSpec)
    params: dict[str, Any] = Field(default_factory=dict, description="原样透传给 agent")
    runtime: CodeRuntime = Field(default_factory=CodeRuntime)
    snapshot: SnapshotSpec | None = Field(
        None, description="版本快照目录（可简写为目录字符串）；为空时不出现在规范化结果中"
    )

    @field_validator("snapshot", mode="before")
    @classmethod
    def _snapshot_shorthand(cls, v: Any) -> Any:
        """``snapshot: exports/current`` 等价于 ``snapshot: {dir: exports/current}``。"""
        return {"dir": v} if isinstance(v, str) else v

    @model_validator(mode="after")
    def _check_transport(self) -> CodeSpec:
        if self.transport == "http":
            if self.http is None:
                raise ValueError("transport=http 时必须提供 http 配置")
            self.subprocess = None
        else:
            if self.subprocess is None:
                raise ValueError("transport=subprocess 时必须提供 subprocess 配置")
            self.http = None
        return self


AgentSpec = Annotated[Union[ConfigSpec, CodeSpec], Field(discriminator="kind")]  # noqa: UP007
"""AgentSpec：以 kind 为判别字段的联合类型。"""

AgentSpecAdapter: TypeAdapter[Any] = TypeAdapter(AgentSpec)


def parse_spec(raw: Any) -> ConfigSpec | CodeSpec:
    """解析并校验 Spec（dict 或已有模型）；失败抛 pydantic.ValidationError。"""
    if isinstance(raw, ConfigSpec | CodeSpec):
        return raw
    return AgentSpecAdapter.validate_python(raw)


def dump_spec(spec: ConfigSpec | CodeSpec) -> dict[str, Any]:
    """导出为 JSON 兼容的 dict：使用别名（schema / with），补齐全部默认值（含 None）。"""
    return spec.model_dump(by_alias=True, mode="json", exclude_none=False)


def spec_json_schema() -> dict[str, Any]:
    """导出 AgentSpec 的 JSON Schema（供前端生成表单）。"""
    return AgentSpecAdapter.json_schema(by_alias=True, mode="validation")


_MSG_BY_TYPE = {
    "missing": "缺少必填字段",
    "extra_forbidden": "不允许的字段",
}


def _loc_to_path(loc: tuple[Any, ...]) -> str:
    parts: list[str] = []
    for i, seg in enumerate(loc):
        if isinstance(seg, str) and (seg.startswith("t:") or (i == 0 and seg in ("config", "code"))):
            continue  # 判别器标签不属于字段路径
        if isinstance(seg, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{seg}]"
            else:
                parts.append(f"[{seg}]")
        else:
            parts.append(str(seg))
    return ".".join(parts)


def format_validation_errors(exc: ValidationError) -> list[dict[str, str]]:
    """把 pydantic 错误转成 ``[{path, message}]``（不回显输入值，避免泄露密钥）。"""
    out: list[dict[str, str]] = []
    for err in exc.errors():
        path = _loc_to_path(tuple(err.get("loc", ())))
        etype = err.get("type", "")
        if etype in ("union_tag_not_found", "union_tag_invalid") and not path:
            path, msg = "kind", "kind 必须是 config 或 code"
        else:
            msg = _MSG_BY_TYPE.get(etype) or str(err.get("msg", ""))
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, ") :]
        if etype == "extra_forbidden" and path:
            msg = f"{msg}: {path.rsplit('.', 1)[-1]}"
        out.append({"path": path or "(root)", "message": msg})
    return out
