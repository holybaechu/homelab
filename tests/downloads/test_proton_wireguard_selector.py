from apps.downloads.scripts.proton_select_wireguard_server import (
    choose_fastest_server,
    filter_wireguard_servers,
    format_selection,
)


def test_filters_japan_wireguard_port_forward_servers():
    servers = [
        {
            "vpn": "openvpn",
            "country": "Japan",
            "port_forward": True,
            "hostname": "openvpn.example",
        },
        {
            "vpn": "wireguard",
            "country": "Japan",
            "port_forward": False,
            "hostname": "wg-no-pf.example",
            "wgpubkey": "bad",
            "ips": ["192.0.2.10"],
        },
        {
            "vpn": "wireguard",
            "country": "Japan",
            "port_forward": True,
            "hostname": "wg-jp.example",
            "wgpubkey": "good",
            "ips": ["192.0.2.11"],
        },
        {
            "vpn": "wireguard",
            "country": "Korea",
            "port_forward": True,
            "hostname": "wg-kr.example",
            "wgpubkey": "other",
            "ips": ["192.0.2.12"],
        },
    ]

    matches = filter_wireguard_servers(servers, country="Japan", port_forward_only=True)

    assert [server["hostname"] for server in matches] == ["wg-jp.example"]


def test_chooses_lowest_latency_candidate_and_formats_endpoint():
    servers = [
        {
            "country": "Japan",
            "city": "Osaka",
            "server_name": "JP#200",
            "hostname": "node-jp-14.protonvpn.net",
            "wgpubkey": "osaka-key",
            "ips": ["45.14.71.6"],
        },
        {
            "country": "Japan",
            "city": "Tokyo",
            "server_name": "JP#174",
            "hostname": "node-jp-33.protonvpn.net",
            "wgpubkey": "tokyo-key",
            "ips": ["103.125.235.20"],
        },
    ]

    selected = choose_fastest_server(
        servers,
        latency_fn=lambda ip: {"45.14.71.6": 45.0, "103.125.235.20": 21.5}[ip],
    )

    assert selected["hostname"] == "node-jp-33.protonvpn.net"
    assert format_selection(selected, endpoint_port=51820) == {
        "country": "Japan",
        "city": "Tokyo",
        "server_name": "JP#174",
        "hostname": "node-jp-33.protonvpn.net",
        "public_key": "tokyo-key",
        "endpoint_ip": "103.125.235.20",
        "endpoint": "103.125.235.20:51820",
        "latency_ms": 21.5,
    }

