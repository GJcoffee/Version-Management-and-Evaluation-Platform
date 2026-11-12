"""HiAgent 登录协议；无 CLI 子进程，不写验证码或认证正文到磁盘。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import threading
from http.cookiejar import Cookie
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

AUTH_HEADERS = {
    "Accept": "application/json", "Content-Type": "application/json; charset=utf-8",
    "X-Top-Region": "cn-north-1",
}


class AccountAuthError(Exception):
    """仅包含可安全展示的固定原因。"""

    def __init__(self, status: str, message: str) -> None:
        self.status = status
        super().__init__(message)


def encrypt_login_value(value: str) -> str:
    """兼容页面 CryptoJS passphrase AES-256-CBC / EVP_BytesToKey。"""
    salt, material, previous = os.urandom(8), b"", b""
    while len(material) < 48:
        previous = hashlib.md5(previous + b"bytedance.eps.ed" + salt).digest()
        material += previous
    padder = padding.PKCS7(128).padder()
    plaintext = padder.update(value.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(material[:32]), modes.CBC(material[32:48])).encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()
    return "$aes$" + base64.b64encode(b"Salted__" + salt + ciphertext).decode()


def csrf_token(cookies: httpx.Cookies, url: str) -> str | None:
    target = urlsplit(url)
    host, path = target.hostname or "", target.path or "/"
    candidates = []
    for cookie in cookies.jar:
        domain, prefix = cookie.domain.lstrip(".").lower(), cookie.path or "/"
        if (cookie.name.lower() != "x-csrf-token" or not cookie.value or cookie.is_expired()
                or (cookie.secure and target.scheme != "https")):
            continue
        if not (host == domain or (cookie.domain_specified and host.endswith("." + domain))):
            continue
        if path != prefix and not (path.startswith(prefix) and
                                   (prefix.endswith("/") or path[len(prefix):].startswith("/"))):
            continue
        candidates.append((host == domain, len(prefix), len(domain), cookie.value))
    return max(candidates)[3] if candidates else None


def dump_cookies(cookies: httpx.Cookies) -> str:
    return json.dumps([{
        **{key: value for key, value in vars(cookie).items() if key != "_rest"},
        "rest": cookie._rest,
    } for cookie in cookies.jar], sort_keys=True, separators=(",", ":"))


def load_cookies(value: str | None) -> httpx.Cookies:
    cookies = httpx.Cookies()
    for entry in json.loads(value or "[]"):
        cookies.jar.set_cookie(Cookie(**entry))
    return cookies


class HiAgentAuth:
    def __init__(self) -> None:
        self._ocr: Any = None
        self._ocr_lock = asyncio.Lock()
        self._classifier_lock = threading.Lock()

    async def recognize(self, image: bytes) -> str:
        async with self._ocr_lock:
            def classify() -> str:
                # to_thread 被取消后线程仍会结束；线程锁防止后续识别与它并行。
                with self._classifier_lock:
                    if self._ocr is None:
                        import ddddocr
                        import onnxruntime
                        from PIL import Image

                        onnxruntime.disable_telemetry_events()
                        if not hasattr(Image, "ANTIALIAS"):
                            Image.ANTIALIAS = Image.Resampling.LANCZOS
                        self._ocr = ddddocr.DdddOcr(show_ad=False)
                    return re.sub(r"[^A-Za-z0-9]", "", self._ocr.classification(image))

            return await asyncio.to_thread(classify)

    async def headers(self, client: httpx.AsyncClient, origin: str, path: str) -> dict[str, str]:
        token = csrf_token(client.cookies, origin + path)
        if not token:
            response = await client.get(origin + "/")
            response.raise_for_status()
            token = csrf_token(client.cookies, origin + path)
        if not token:
            raise AccountAuthError("verification_required", "未取得 CSRF，请补充已登录的 Cookie／CSRF")
        return {**AUTH_HEADERS, "X-Csrf-Token": token}

    async def check(self, client: httpx.AsyncClient, origin: str, username: str) -> bool:
        headers = await self.headers(client, origin, "/api/auth/checkLogin")
        response = await client.get(origin + "/api/auth/checkLogin", headers=headers)
        if response.status_code in (401, 403):
            return False
        response.raise_for_status()
        payload = response.json()
        error = payload.get("ResponseMetadata", {}).get("Error")
        if error:
            code = str(error.get("Code", "")).lower()
            if any(word in code for word in ("unauthorized", "notlogin", "notlogged", "invalidsession")):
                return False
            raise AccountAuthError("check_failed", "登录状态检查未完成，请稍后重试")
        result = payload.get("Result") or {}
        if result.get("PasswordExpired") or result.get("FirstLogin"):
            raise AccountAuthError(
                "verification_required", "账号需要修改密码或完成首次登录，请在站点处理后补充登录态",
            )
        if not any(result.get(key) for key in ("ID", "UserName", "FakeToken")):
            return False
        if result.get("UserName") and result["UserName"] != username:
            raise AccountAuthError("verification_required", "登录态与所选账号不一致，请重新登录")
        return True

    async def login(self, client: httpx.AsyncClient, origin: str, username: str,
                    password: str, tenant_name: str) -> None:
        for _ in range(3):
            headers = await self.headers(client, origin, "/api/iam")
            response = await client.post(origin + "/api/iam", headers=headers,
                                         params={"Action": "GetCaptcha", "Version": "2024-12-25"}, json={})
            response.raise_for_status()
            if not response.headers.get("id") or not response.content.startswith(b"\x89PNG\r\n\x1a\n"):
                raise AccountAuthError("verification_required", "站点要求额外验证，请手动登录后补充登录态")
            captcha = await self.recognize(response.content)
            if not captcha:
                continue
            body = {
                "UserName": username, "Password": encrypt_login_value(password),
                "Captcha": encrypt_login_value(json.dumps(
                    {"Captcha": captcha, "Id": response.headers["id"]}, separators=(",", ":"))),
                **({"TenantName": tenant_name} if tenant_name else {}),
            }
            response = await client.post(origin + "/api/auth/login", json=body,
                                         headers=await self.headers(client, origin, "/api/auth/login"))
            if response.status_code >= 500 or response.status_code == 429:
                response.raise_for_status()
            error = response.json().get("ResponseMetadata", {}).get("Error") or {}
            code = (str(error.get("Code", "")) + " " + str(error.get("Message", ""))).lower()
            if "captcha" in code or "验证码" in code:
                continue
            if error or response.is_error:
                if any(word in code for word in (
                    "password", "credential", "usernot", "unauthorized", "密码",
                )):
                    raise AccountAuthError("login_failed", "账号或密码未通过验证，请检查账号配置")
                raise AccountAuthError("verification_required", "自动登录未通过验证，请手动登录后补充登录态")
            if await self.check(client, origin, username):
                return
            raise AccountAuthError("verification_required", "登录后仍未取得有效会话，请手动登录后补充登录态")
        raise AccountAuthError("verification_required", "验证码识别未通过，请手动登录后补充登录态")

    @staticmethod
    def authentication_failed(response: httpx.Response) -> bool:
        if response.status_code in (401, 403):
            return True
        try:
            error = response.json().get("ResponseMetadata", {}).get("Error") or {}
            code = str(error.get("Code", "")).lower()
            return any(word in code for word in ("unauthorized", "notlogin", "notlogged", "invalidsession"))
        except (ValueError, AttributeError, TypeError):
            return False
