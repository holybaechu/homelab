"""Bind the required Operator file to the default profile before gateway startup."""
import io
import json
import os
from pathlib import Path
import stat
import sys

from dotenv.parser import parse_stream
from hermes_cli.config import _env_write_lock, _quote_env_value, _write_env_lines

KEY = "BROWSER_USE_CUSTOM_PROXY_JSON"
SOURCE = Path("/var/run/hermes-browser-proxy/custom-proxy-json")
ENV = Path("/opt/data/.env")


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("Duplicate proxy key")
        obj[key] = value
    return obj


def reconcile(source, env):
    raw = source.read_bytes()
    if not raw or len(raw) > 16384:
        raise ValueError("Invalid proxy input")
    proxy = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(proxy, dict) or set(proxy) != {"host", "port", "protocol", "username", "password"}:
        raise ValueError("Invalid proxy schema")
    if (proxy["host"] != "proxy.hchu.me" or type(proxy["port"]) is not int or
            proxy["port"] != 443 or proxy["protocol"] != "https"):
        raise ValueError("Unexpected proxy endpoint")
    for key, limit in (("username", 2048), ("password", 8192)):
        value = proxy[key]
        if (not isinstance(value, str) or not value.strip() or len(value) > limit or
                any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise ValueError("Invalid proxy credentials")
    # JSON escapes keep dotenv interpolation from interpreting credential dollars.
    value = json.dumps(proxy, ensure_ascii=True, separators=(",", ":")).replace("$", "\\u0024")
    line = KEY + "=" + _quote_env_value(value) + "\n"
    with _env_write_lock(env):
        if env.is_symlink():
            raise ValueError("Unexpected environment file")
        if env.exists():
            info = env.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
                raise ValueError("Unexpected environment file")
            original = env.read_bytes().decode("utf-8")
        else:
            original = ""
        bom = "\ufeff" if original.startswith("\ufeff") else ""
        mappings = list(parse_stream(io.StringIO(original.removeprefix(bom) if bom else original)))
        if any(m.error for m in mappings):
            raise ValueError("Invalid environment file")
        lines, replaced = [bom], False
        for mapping in mappings:
            if mapping.key == KEY:
                if not replaced:
                    lines.append(line)
                    replaced = True
            else:
                lines.append(mapping.original.string)
        if not replaced:
            if original and not original.endswith("\n"):
                lines.append("\n")
            lines.append(line)
        # Native atomic writer and lock, without save_env_value's unrelated-line
        # normalization or process-global secret publication.
        _write_env_lines(env, lines, preserve_mode=env.exists())


def main():
    try:
        reconcile(SOURCE, ENV)
    except Exception:
        print("Browser proxy binding refused; gateway startup blocked", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
