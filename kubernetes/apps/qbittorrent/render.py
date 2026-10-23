import configparser
import json
import os
from pathlib import Path
import re


def render(destination, username, password_hash):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", username):
        raise ValueError("invalid qBittorrent account name")
    if not re.fullmatch(r"@ByteArray\([A-Za-z0-9+/]{22}==:[A-Za-z0-9+/]{86}==\)", password_hash):
        raise ValueError("invalid qBittorrent PBKDF2 hash")
    config = configparser.RawConfigParser(strict=False)
    config.optionxform = str
    if destination.exists():
        config.read(destination)
    if not config.has_section("Preferences"):
        config.add_section("Preferences")
    preferences = config["Preferences"]
    defaults = {
        "Downloads\\SavePath": "/downloads/complete/",
        "Downloads\\TempPath": "/downloads/incomplete/",
        "Downloads\\TempPathEnabled": "true",
        "Connection\\UPnP": "false",
        "Connection\\RandomPort": "false",
    }
    for key, value in defaults.items():
        if key not in preferences:
            preferences[key] = value
    preferences.update({
        "WebUI\\Address": "0.0.0.0",
        "WebUI\\Port": "8080",
        "WebUI\\Username": username,
        "WebUI\\Password_PBKDF2": json.dumps(password_hash),
        "WebUI\\LocalHostAuth": "true",
        "WebUI\\AuthSubnetWhitelistEnabled": "false",
        "WebUI\\AlternativeUIEnabled": "true",
        "WebUI\\RootFolder": "/vuetorrent",
        "Connection\\PortRangeMin": "35435",
    })
    if not config.has_section("BitTorrent"):
        config.add_section("BitTorrent")
    if "Session\\QueueingSystemEnabled" not in config["BitTorrent"]:
        config["BitTorrent"]["Session\\QueueingSystemEnabled"] = "false"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    with temporary.open("w") as output:
        config.write(output, space_around_delimiters=False)
    temporary.chmod(0o600)
    temporary.replace(destination)


if __name__ == "__main__":
    os.umask(0o077)
    render(Path("/config/qBittorrent/qBittorrent.conf"),
           os.environ["QBITTORRENT_USERNAME"], os.environ["QBITTORRENT_PASSWORD_HASH"])
    Path("/downloads/complete").mkdir(exist_ok=True)
    Path("/downloads/incomplete").mkdir(exist_ok=True)
