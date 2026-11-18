"""通过官方 hicli 查询观测数据；平台不读取、复制或实现账号密钥签名。"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from evalp.runtime.adapters.platforms.hiagent.config import HiAgentConfig

READ_ACTIONS = frozenset({"GetTraceInfo", "ListTraceSpans"})


class HicliObserveError(RuntimeError):
    """可展示的错误；不包含 CLI stdout/stderr 中的潜在凭据。"""


def _command() -> list[str]:
    executable = shutil.which("hicli")
    if not executable:
        raise HicliObserveError("找不到 hicli，请安装并完成 hicli auth 认证")
    path = Path(executable)
    # 官方 Windows 包的 cmd 包装器执行同目录 hicli.pyz；直接启动以避免 shell。
    bundle = path.with_name("hicli.pyz")
    if path.suffix.lower() in {".cmd", ".bat"} and bundle.is_file():
        return [sys.executable, str(bundle)]
    return [str(path)]


async def query_hicli_trace(
    config: HiAgentConfig, action: str, body: dict[str, Any], *, identity: dict | None = None,
) -> dict:
    if action not in READ_ACTIONS:
        raise HicliObserveError("hicli 观测只允许查询执行记录和调用明细")
    if config.observe_transport != "hicli" or not config.observe_hicli_base_url or not config.workspace_id:
        raise HicliObserveError("hicli 观测配置不完整")
    if body.get("WorkspaceID", config.workspace_id) != config.workspace_id:
        raise HicliObserveError("观测请求的工作空间与连接配置不一致")
    values = {**body, "WorkspaceID": config.workspace_id}
    command = _command()
    with tempfile.TemporaryDirectory(prefix="evalp-hicli-observe-") as directory:
        request = Path(directory) / "request.json"
        request.write_text(json.dumps(values), encoding="utf-8")
        options: dict[str, Any] = {}
        auth_args: list[str] = []
        if identity is not None:
            ak, sk = identity.get("access_key_id"), identity.get("secret_access_key")
            if not isinstance(ak, str) or not ak.strip() or not isinstance(sk, str) or not sk.strip():
                raise HicliObserveError("密钥连接缺少完整账号密钥，请在设置中更换")
            # 独立认证文件阻止回退到本机账号；秘密只在单个子进程环境中传递。
            empty_auth = Path(directory) / "auth.json"
            empty_auth.write_text("{}", encoding="utf-8")
            auth_args = ["--auth-file", str(empty_auth)]
            env = {k: v for k, v in os.environ.items()
                   if not k.upper().startswith(("HIAGENT_", "HICLI_", "REPO2CLI_"))
                   and k.upper() != "IS_GM"}
            env.update({"HIAGENT_ACCESS_KEY_ID": ak, "HIAGENT_ACCESS_KEY_SECRET": sk})
            options["env"] = env
        try:
            process = await asyncio.create_subprocess_exec(
                *command, "observe", "--base-url", config.observe_hicli_base_url,
                *auth_args, "--json", "--timeout", "10", "actions", "call", action,
                "--body-file", str(request),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **options,
            )
        except OSError as exc:
            raise HicliObserveError("无法启动 hicli，请检查本机安装与运行权限") from exc
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.communicate()
            raise
        if process.returncode:
            raise HicliObserveError(
                f"hicli 观测查询失败（退出码 {process.returncode}），请检查 hicli auth 和服务权限",
            )
        try:
            response = json.loads(stdout)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HicliObserveError("hicli 观测未返回有效 JSON") from exc
        if not isinstance(response, dict) or response.get("ok") is False:
            raise HicliObserveError("hicli 观测返回失败结果")
        result = response.get("data", response)
        if not isinstance(result, dict) or result.get("ResponseMetadata", {}).get("Error"):
            raise HicliObserveError("hicli 观测服务拒绝查询，请核对权限与请求参数")
        result = result.get("Result", result)
        if not isinstance(result, dict):
            raise HicliObserveError("hicli 观测返回了不支持的数据结构")
        return result
