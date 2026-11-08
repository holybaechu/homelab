import json
import os
from pathlib import Path
import re
import sys


def render(template, username, password_hash):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", username):
        raise ValueError("invalid username")
    if not re.fullmatch(r"\$2[aby]\$(0[4-9]|[12][0-9]|3[01])\$[./A-Za-z0-9]{53}", password_hash):
        raise ValueError("invalid bcrypt hash")
    body = template[template.index("schema_version: 34\n"):]
    body = body.replace("{{ .username | toJson }}", json.dumps(username))
    body = body.replace("{{ .password_hash | toJson }}", json.dumps(password_hash))
    if "{{" in body or "}}" in body:
        raise ValueError("unsupported template expression")
    return body.encode("utf-8")


def main():
    os.umask(0o077)
    try:
        output = render(
            Path("/templates/AdGuardHome.yaml").read_bytes().decode("utf-8"),
            Path("/credentials/username").read_bytes().decode("utf-8"),
            Path("/credentials/password-hash").read_bytes().decode("utf-8"),
        )
        with Path("/desired/AdGuardHome.yaml").open("xb") as stream:
            stream.write(output)
    except (OSError, ValueError, TypeError):
        print("AdGuard configuration rendering failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
