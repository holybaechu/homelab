#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request


def parse_natpmp_port(output: str) -> int:
    match = re.search(r"Mapped public port\s+(\d+)\s+protocol\s+TCP", output)
    if not match:
        raise ValueError("natpmpc output did not include a TCP public port")
    return int(match.group(1))


def run_natpmp(gateway: str) -> int:
    udp = subprocess.run(
        ["natpmpc", "-a", "1", "0", "udp", "60", "-g", gateway],
        check=True,
        text=True,
        capture_output=True,
    )
    tcp = subprocess.run(
        ["natpmpc", "-a", "1", "0", "tcp", "60", "-g", gateway],
        check=True,
        text=True,
        capture_output=True,
    )
    print(udp.stdout)
    print(tcp.stdout)
    return parse_natpmp_port(tcp.stdout)


def qbittorrent_login(base_url: str, username: str, password: str) -> str:
    data = urllib.parse.urlencode({"username": username, "password": password}).encode()
    request = urllib.request.Request(f"{base_url}/api/v2/auth/login", data=data)
    with urllib.request.urlopen(request, timeout=10) as response:
        cookie = response.headers.get("Set-Cookie")
        if not cookie:
            raise RuntimeError("qBittorrent login did not return a session cookie")
        return cookie


def qbittorrent_set_port(base_url: str, cookie: str, port: int) -> None:
    payload = {"listen_port": port, "upnp": False, "random_port": False}
    data = urllib.parse.urlencode({"json": json.dumps(payload)}).encode()
    request = urllib.request.Request(
        f"{base_url}/api/v2/app/setPreferences",
        data=data,
        headers={"Cookie": cookie},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status not in (200, 204):
            raise RuntimeError(f"qBittorrent setPreferences returned {response.status}")


def update_qbittorrent_port(base_url: str, username: str, password: str, port: int) -> None:
    cookie = qbittorrent_login(base_url, username, password)
    qbittorrent_set_port(base_url, cookie, port)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", default="10.2.0.1")
    parser.add_argument("--qbt-url", default="http://127.0.0.1:8080")
    parser.add_argument("--qbt-user", default="admin")
    parser.add_argument("--qbt-password", default=os.environ.get("QBITTORRENT_PASSWORD"))
    args = parser.parse_args()

    if not args.qbt_password:
        parser.error("--qbt-password or QBITTORRENT_PASSWORD is required")

    try:
        port = run_natpmp(args.gateway)
    except Exception as exc:
        print(f"NAT-PMP refresh failed: {exc}; setting qBittorrent listen port to 0", file=sys.stderr)
        try:
            update_qbittorrent_port(args.qbt_url, args.qbt_user, args.qbt_password, 0)
        except Exception as qbt_exc:
            print(f"Failed to set qBittorrent listen port to 0: {qbt_exc}", file=sys.stderr)
        raise

    update_qbittorrent_port(args.qbt_url, args.qbt_user, args.qbt_password, port)
    print(f"Updated qBittorrent listen port to {port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
