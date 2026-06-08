#!/usr/bin/env python3
import argparse
import json
import math
import re
import subprocess
import sys
import urllib.request


DEFAULT_SERVERS_URL = (
    "https://raw.githubusercontent.com/qdm12/gluetun-servers/"
    "refs/heads/main/pkg/servers/protonvpn.json"
)


def load_server_list(url: str) -> list[dict]:
    with urllib.request.urlopen(url, timeout=20) as response:
        payload = json.load(response)
    return payload.get("servers", [])


def filter_wireguard_servers(
    servers: list[dict], country: str, port_forward_only: bool
) -> list[dict]:
    matches = []
    for server in servers:
        if server.get("vpn") != "wireguard":
            continue
        if server.get("country") != country:
            continue
        if port_forward_only and server.get("port_forward") is not True:
            continue
        if not server.get("wgpubkey"):
            continue
        if not server.get("ips"):
            continue
        matches.append(server)
    return matches


def ping_latency_ms(ip_address: str, timeout_seconds: int = 2) -> float | None:
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout_seconds), ip_address],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    match = re.search(r"time[=<]([0-9.]+)\s*ms", result.stdout)
    if not match:
        return None
    return float(match.group(1))


def choose_fastest_server(servers: list[dict], latency_fn=ping_latency_ms) -> dict:
    if not servers:
        raise ValueError("no matching Proton WireGuard servers found")

    best: tuple[tuple[bool, float, int], dict] | None = None
    for index, server in enumerate(servers):
        endpoint_ip = server["ips"][0]
        latency = math.inf
        for candidate_ip in server["ips"]:
            candidate_latency = latency_fn(candidate_ip)
            if candidate_latency is not None and candidate_latency < latency:
                endpoint_ip = candidate_ip
                latency = candidate_latency

        selected = dict(server)
        selected["_endpoint_ip"] = endpoint_ip
        selected["_latency_ms"] = None if math.isinf(latency) else latency
        key = (math.isinf(latency), latency, index)
        if best is None or key < best[0]:
            best = (key, selected)

    assert best is not None
    return best[1]


def format_selection(server: dict, endpoint_port: int) -> dict:
    endpoint_ip = server.get("_endpoint_ip", server["ips"][0])
    return {
        "country": server["country"],
        "city": server.get("city"),
        "server_name": server.get("server_name"),
        "hostname": server["hostname"],
        "public_key": server["wgpubkey"],
        "endpoint_ip": endpoint_ip,
        "endpoint": f"{endpoint_ip}:{endpoint_port}",
        "latency_ms": server.get("_latency_ms"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Select the fastest matching Proton WireGuard server."
    )
    parser.add_argument("--servers-url", default=DEFAULT_SERVERS_URL)
    parser.add_argument("--country", default="Japan")
    parser.add_argument("--endpoint-port", type=int, default=51820)
    parser.add_argument("--port-forward-only", action="store_true")
    args = parser.parse_args()

    servers = load_server_list(args.servers_url)
    candidates = filter_wireguard_servers(
        servers, country=args.country, port_forward_only=args.port_forward_only
    )
    selected = choose_fastest_server(candidates)
    json.dump(format_selection(selected, args.endpoint_port), sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

