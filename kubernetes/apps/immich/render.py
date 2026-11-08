import json
import os
from pathlib import Path
import sys


def render(template, secret):
    config = json.loads(template)
    if not isinstance(secret, str) or not secret or not isinstance(config.get("oauth"), dict):
        raise ValueError("invalid input")
    if "clientSecret" in config["oauth"]:
        raise ValueError("template contains credential")
    config["oauth"]["clientSecret"] = secret
    return (json.dumps(config, ensure_ascii=True, indent=2) + "\n").encode("utf-8")


def main():
    os.umask(0o077)
    try:
        template = Path("/templates/immich.json").read_text(encoding="utf-8")
        # read_bytes avoids universal-newline conversion of a credential.
        secret = Path("/credentials/oidc-client-secret").read_bytes().decode("utf-8")
        output = render(template, secret)
        with Path("/config/immich.json").open("xb") as stream:
            stream.write(output)
    except (OSError, ValueError, TypeError, AttributeError):
        print("Immich configuration rendering failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
