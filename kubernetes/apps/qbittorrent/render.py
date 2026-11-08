import json
import os
from pathlib import Path
import re
import sys


def render(template, username, password_hash):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", username):
        raise ValueError("invalid username")
    if not re.fullmatch(r"@ByteArray\([A-Za-z0-9+/]{22}==:[A-Za-z0-9+/]{86}==\)", password_hash):
        raise ValueError("invalid PBKDF2 hash")
    body = template[template.index("[Preferences]\n"):]
    body = body.replace("{{ .username }}", username)
    body = body.replace("{{ .password_hash | toJson }}", json.dumps(password_hash))
    if "{{" in body or "}}" in body:
        raise ValueError("unsupported template expression")
    return body.encode("utf-8")


def main():
    os.umask(0o077)
    try:
        output = render(
            Path("/templates/managed.conf").read_bytes().decode("utf-8"),
            Path("/credentials/username").read_bytes().decode("utf-8"),
            Path("/credentials/password-hash").read_bytes().decode("utf-8"),
        )
        with Path("/desired/managed.conf").open("xb") as stream:
            stream.write(output)
    except (OSError, ValueError, TypeError):
        print("qBittorrent configuration rendering failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
