"""适配器架构门禁：协议翻译边界、无运行时补丁、无 agent 版本分支、一个平台一个活跃大版本。"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest
from evalp.errors import ValidationFailed
from evalp.runtime.adapters import registry
from evalp.runtime.adapters.binding import normalize_binding
from evalp.spec.models import AdapterBinding

ADAPTERS = Path(registry.__file__).parent
PLATFORMS = ADAPTERS / "platforms"

STDLIB = set(sys.stdlib_module_names)
THIRD_PARTY = {"httpx", "pydantic", "sqlalchemy"}
ALLOWED_EVALP = (
    "evalp.context",
    "evalp.errors",
    "evalp.runtime.adapters.base",
    "evalp.runtime.adapters.binding",
    "evalp.runtime.adapters.normalize",
    "evalp.runtime.adapters.registry",
    "evalp.runtime.adapters.http_adapter",
    "evalp.runtime.adapters.platforms",
    "evalp.runtime.events",
    "evalp.runtime.credentials",
    "evalp.runtime.hiagent_auth",
    "evalp.runtime.messages",
    "evalp.runtime.llm.ratelimit",
    "evalp.spec.models",
    "evalp.store.models",
    "evalp.utils.",
)
FORBIDDEN_PREFIXES = ("evalp.services", "evalp.mcp", "evalp.api", "integrations", "evalp_sdk")
VERSION_LITERAL = re.compile(r"""["']v\d{2,}["']""", re.IGNORECASE)


def platform_files() -> list[Path]:
    return sorted(PLATFORMS.rglob("*.py"))


def imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
    return modules


def allowed(module: str) -> bool:
    top = module.split(".")[0]
    if top in STDLIB or top in THIRD_PARTY:
        return True
    return any(module == prefix or module.startswith(prefix) for prefix in ALLOWED_EVALP)


def test_adapters_only_import_protocol_and_platform_modules():
    problems = []
    for path in platform_files():
        for module in imported_modules(path):
            if module.startswith(FORBIDDEN_PREFIXES):
                problems.append(f"{path.relative_to(PLATFORMS)}: 业务依赖 {module}")
            elif not allowed(module):
                problems.append(f"{path.relative_to(PLATFORMS)}: 未登记依赖 {module}")
    assert not problems, "\n".join(problems)


def test_no_runtime_patch_mechanism_in_adapters():
    assert not (PLATFORMS / "patches").exists()
    for path in platform_files():
        assert "importlib" not in imported_modules(path), f"{path} 不允许运行时动态加载"
        assert "patches" not in path.parts


def test_adapters_do_not_branch_on_agent_version_literals():
    problems = []
    for path in platform_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if VERSION_LITERAL.search(line):
                problems.append(f"{path.relative_to(PLATFORMS)}:{lineno}: {line.strip()}")
    assert not problems, "适配器不得出现 agent 版本字面量：\n" + "\n".join(problems)


def test_one_active_major_per_platform_and_contract_capabilities():
    definitions = registry.definitions()
    by_platform: dict[str, list] = {}
    for definition in definitions:
        by_platform.setdefault(definition.adapter, []).append(definition)
        if definition.sunset:
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", definition.sunset)
        assert len(definition.impl_digest) == 64
    for adapter, items in by_platform.items():
        active = [item for item in items if not item.sunset]
        assert len(active) == 1, f"{adapter} 有多个活跃实现: {[item.major for item in active]}"
    hiagent = next(item for item in definitions if item.adapter == "hiagent")
    assert hiagent.major == 1
    assert {"trace", "refresh_trace"} <= set(hiagent.capabilities)


def test_binding_accepts_new_and_legacy_forms():
    new = normalize_binding({
        "adapter": "hiagent", "major": 1, "config": {"app_id": "a"}, "credential_ref": "adapter:prv_x",
    })
    legacy = normalize_binding({
        "id": "hiagent", "version": "1.4.0", "config": {"app_id": "a"}, "credential_ref": "adapter:prv_x",
    })
    assert (new.adapter, new.major, new.version) == ("hiagent", 1, "1.0.0")
    assert (legacy.adapter, legacy.major) == ("hiagent", 1)

    model = AdapterBinding.model_validate({
        "id": "hiagent", "version": "1.4.0", "config": {}, "credential_ref": "adapter:prv_x",
    })
    assert (model.adapter, model.major) == ("hiagent", 1)
    stored = AdapterBinding.model_validate({
        "adapter": "hiagent", "major": 1, "config": {}, "credential_ref": "adapter:prv_x",
    }).model_dump()
    assert "id" not in stored and "version" not in stored

    _, adapter_cls = registry.resolve(new)
    assert adapter_cls.adapter == "hiagent" and adapter_cls.major == 1


def test_unknown_platform_and_major_are_rejected():
    with pytest.raises(ValidationFailed):
        registry.resolve({"adapter": "unknown", "major": 1, "config": {}, "credential_ref": "x"})
    with pytest.raises(ValidationFailed):
        registry.resolve({"adapter": "hiagent", "major": 99, "config": {}, "credential_ref": "x"})
    with pytest.raises(ValidationFailed):
        registry.validate_binding({
            "id": "hiagent", "version": "2.0.0", "config": {}, "credential_ref": "x",
        })


def test_services_use_capabilities_instead_of_platform_branches():
    """会话与重放服务不能直接依赖某个平台实现，也不能出现平台名分支。"""
    import evalp.services.compare as compare
    import evalp.services.session as session

    for module in (session, compare):
        path = Path(module.__file__)
        modules = imported_modules(path)
        leaked = [m for m in modules if "hiagent" in m or ".platforms." in m]
        assert not leaked, f"{path.name} 直连平台实现: {leaked}"
        assert "hiagent" not in path.read_text(encoding="utf-8").lower(), (
            f"{path.name} 仍含平台名，应改为适配器能力"
        )
