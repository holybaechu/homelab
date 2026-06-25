"""Hermes plugin for safe Newrrow login in the active browser-tool session.

The built-in browser_type tool echoes typed text in its result, so it must not
be used with Newrrow passwords. This plugin keeps the public Newrrow navigation
on Hermes' browser routing path (Browserbase for public URLs in homelab) while
reading 1Password refs inside the tool handler and injecting credentials through
CDP without returning secret values to the model.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from typing import Any

NEWRROW_HOME_URL = "https://gbsm.newrrow.com/csr-platform/home"


_SCHEMA = {
    "name": "newrrow_browser_login",
    "description": (
        "Log into Newrrow in the active Hermes browser-tool session using "
        "1Password refs from the gateway environment. Use this after "
        "browser_navigate to the Newrrow public URL shows the login page. "
        "It preserves Hermes browser routing (Browserbase for public URLs) "
        "and does not expose the username/password as tool arguments or output."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Newrrow URL to open before login. Defaults to the hardcoded home route.",
            }
        },
        "required": [],
    },
}


def _check_requirements() -> bool:
    return (
        bool(os.getenv("OP_SERVICE_ACCOUNT_TOKEN"))
        and bool(os.getenv("NEWRROW_USERNAME_REF"))
        and bool(os.getenv("NEWRROW_PASSWORD_REF"))
        and shutil.which(os.getenv("OP_BIN", "op")) is not None
        and _websockets_available()
    )


def _websockets_available() -> bool:
    try:
        import websockets  # noqa: F401
    except Exception:
        return False
    return True


def _json_response(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _read_1password_ref(env_name: str) -> str:
    ref = os.getenv(env_name, "").strip()
    if not ref:
        raise RuntimeError(f"{env_name} is not configured")

    op_bin = os.getenv("OP_BIN", "op")
    completed = subprocess.run(
        [op_bin, "read", ref],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0:
        # 1Password errors normally only mention the ref, not the secret. Keep
        # this short and never include stdout.
        err = (completed.stderr or "op read failed").strip().splitlines()[0]
        raise RuntimeError(f"failed to read {env_name}: {err}")

    value = completed.stdout.rstrip("\r\n")
    if not value:
        raise RuntimeError(f"{env_name} returned an empty value")
    return value


class _CdpClient:
    def __init__(self, cdp_url: str):
        self.cdp_url = cdp_url
        self._next_id = 0
        self._ws = None
        self.session_id: str | None = None

    async def __aenter__(self):
        import websockets

        self._ws = await websockets.connect(self.cdp_url, max_size=16 * 1024 * 1024)
        await self._attach_to_page()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._ws is not None:
            await self._ws.close()

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("CDP websocket is not connected")
        self._next_id += 1
        message: dict[str, Any] = {"id": self._next_id, "method": method}
        if params is not None:
            message["params"] = params
        if session_id is not None:
            message["sessionId"] = session_id
        await self._ws.send(json.dumps(message))
        while True:
            raw = await self._ws.recv()
            data = json.loads(raw)
            if data.get("id") != self._next_id:
                continue
            if "error" in data:
                raise RuntimeError(f"CDP {method} failed: {data['error'].get('message', data['error'])}")
            return data.get("result", {})

    async def _attach_to_page(self) -> None:
        targets = (await self.call("Target.getTargets")).get("targetInfos", [])
        page_targets = [target for target in targets if target.get("type") == "page"]
        if not page_targets:
            raise RuntimeError("no page target is available in the browser session")

        def score(target: dict[str, Any]) -> int:
            url = target.get("url") or ""
            if "newrrow.com" in url or "inhrplus.com" in url:
                return 0
            if url and url != "about:blank":
                return 1
            return 2

        target_id = sorted(page_targets, key=score)[0]["targetId"]
        attached = await self.call(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        self.session_id = attached.get("sessionId")
        if not self.session_id:
            raise RuntimeError("CDP did not return a sessionId for the page target")
        await self.call("Runtime.enable", session_id=self.session_id)

    async def evaluate(self, expression: str) -> Any:
        result = await self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "userGesture": True,
            },
            session_id=self.session_id,
        )
        if result.get("exceptionDetails"):
            raise RuntimeError("CDP Runtime.evaluate raised an exception")
        remote = result.get("result") or {}
        return remote.get("value")


async def _evaluate(cdp_url: str, expression: str) -> Any:
    async with _CdpClient(cdp_url) as client:
        return await client.evaluate(expression)


def _login_script(username: str, password: str) -> str:
    username_json = json.dumps(username)
    password_json = json.dumps(password)
    return f"""
(async () => {{
  const username = {username_json};
  const password = {password_json};
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const controls = Array.from(document.querySelectorAll('input, textarea'))
    .filter((el) => !el.disabled && el.type !== 'hidden');
  const passwordInput = controls.find((el) => String(el.type || '').toLowerCase() === 'password');
  const usernameInput = controls.find((el) => el !== passwordInput && ['email', 'text', 'tel', 'search', ''].includes(String(el.type || '').toLowerCase())) || controls.find((el) => el !== passwordInput);
  if (!usernameInput || !passwordInput) {{
    return {{ ok: false, reason: 'login inputs not found', url: location.href }};
  }}
  const setValue = (el, value) => {{
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) setter.call(el, value); else el.value = value;
    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
  }};
  setValue(usernameInput, username);
  setValue(passwordInput, password);
  await sleep(250);
  const candidates = Array.from(document.querySelectorAll('button, [role="button"], input[type="submit"]'));
  const loginButton = candidates.find((el) => /로그인|login/i.test((el.innerText || el.value || el.getAttribute('aria-label') || '').trim())) || candidates.find((el) => !el.disabled);
  if (!loginButton) {{
    return {{ ok: false, reason: 'login button not found', url: location.href }};
  }}
  loginButton.click();
  return {{ ok: true, url: location.href }};
}})()
"""


def _click_text_script(label: str) -> str:
    label_json = json.dumps(label)
    return f"""
(() => {{
  const label = {label_json};
  const candidates = Array.from(document.querySelectorAll('button, [role="button"], a'));
  const target = candidates.find((el) => (el.innerText || el.getAttribute('aria-label') || '').includes(label));
  if (!target) return {{ ok: false, reason: 'target text not found', url: location.href }};
  target.click();
  return {{ ok: true, url: location.href }};
}})()
"""


def _location_script() -> str:
    return "(() => ({ url: location.href, text: document.body.innerText.slice(0, 4000) }))()"


def _looks_like_login(url: str, text: str) -> bool:
    folded = f"{url}\n{text}".lower()
    return any(token in folded for token in ("auth.inhrplus.com", "login", "로그인", "비밀번호", "password"))


def _handler(args: dict[str, Any], **kwargs: Any) -> str:
    task_id = kwargs.get("task_id") or "default"
    url = (args.get("url") or NEWRROW_HOME_URL).strip() or NEWRROW_HOME_URL

    try:
        from tools import browser_tool

        nav = json.loads(browser_tool.browser_navigate(url, task_id=task_id))
        if not nav.get("success"):
            return _json_response({"success": False, "error": nav.get("error", "Newrrow navigation failed")})

        session_key = browser_tool._last_session_key(task_id)  # keep the active Hermes browser-tool route
        session_info = browser_tool._get_session_info(session_key)
        cdp_url = session_info.get("cdp_url")
        if not cdp_url:
            return _json_response(
                {
                    "success": False,
                    "error": (
                        "Newrrow browser-tool login requires a CDP-backed browser session "
                        "(Browserbase, Browser Use, Firecrawl, or browser.cdp_url). The active "
                        "session is local agent-browser, so using this tool would not verify the "
                        "Browserbase path."
                    ),
                }
            )

        state = asyncio.run(_evaluate(cdp_url, _location_script())) or {}
        current_url = str(state.get("url") or nav.get("url") or "")
        page_text = str(state.get("text") or nav.get("snapshot") or "")
        login_performed = False

        if _looks_like_login(current_url, page_text):
            username = _read_1password_ref("NEWRROW_USERNAME_REF")
            password = _read_1password_ref("NEWRROW_PASSWORD_REF")
            try:
                login_result = asyncio.run(_evaluate(cdp_url, _login_script(username, password))) or {}
            finally:
                # Drop secret references as soon as the CDP action has been sent.
                password = ""
                username = ""
            if not login_result.get("ok"):
                return _json_response(
                    {
                        "success": False,
                        "error": f"Newrrow login form automation failed: {login_result.get('reason', 'unknown reason')}",
                    }
                )
            login_performed = True
            time.sleep(5)
            nav = json.loads(browser_tool.browser_navigate(NEWRROW_HOME_URL, task_id=task_id))
            if not nav.get("success"):
                return _json_response({"success": False, "error": nav.get("error", "Newrrow post-login navigation failed")})
            state = asyncio.run(_evaluate(cdp_url, _location_script())) or {}
            current_url = str(state.get("url") or nav.get("url") or "")
            page_text = str(state.get("text") or nav.get("snapshot") or "")

        if "csr-platform/invitation" in current_url or "뉴로우 시작하기" in page_text:
            asyncio.run(_evaluate(cdp_url, _click_text_script("뉴로우 시작하기")))
            time.sleep(5)
            nav = json.loads(browser_tool.browser_navigate(NEWRROW_HOME_URL, task_id=task_id))
            if not nav.get("success"):
                return _json_response({"success": False, "error": nav.get("error", "Newrrow post-invitation navigation failed")})
            state = asyncio.run(_evaluate(cdp_url, _location_script())) or {}
            current_url = str(state.get("url") or nav.get("url") or "")
            page_text = str(state.get("text") or nav.get("snapshot") or "")

        if _looks_like_login(current_url, page_text):
            return _json_response({"success": False, "error": "Newrrow login did not reach an authenticated page"})

        return _json_response(
            {
                "success": True,
                "url": current_url,
                "login_performed": login_performed,
                "backend": "hermes-browser-tool-cdp",
                "routing": "Hermes browser tools own navigation; public Newrrow URLs use the configured cloud provider.",
                "secret_handling": "1Password values were read inside the plugin and were not returned as tool output or passed as model-visible browser_type arguments.",
            }
        )
    except Exception as exc:
        return _json_response({"success": False, "error": f"Newrrow browser login failed: {type(exc).__name__}: {exc}"})


def register(ctx):
    ctx.register_tool(
        name="newrrow_browser_login",
        toolset="browser",
        schema=_SCHEMA,
        handler=_handler,
        check_fn=_check_requirements,
        requires_env=["OP_SERVICE_ACCOUNT_TOKEN", "NEWRROW_USERNAME_REF", "NEWRROW_PASSWORD_REF"],
        description=_SCHEMA["description"],
        emoji="🔐",
    )
