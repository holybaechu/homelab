import json
import os
from pathlib import Path
import re


def render(template, state, username, password_hash):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", username):
        raise ValueError("invalid AdGuard account name")
    if not re.fullmatch(r"\$2[aby]\$(?:0[4-9]|[12][0-9]|3[01])\$[./A-Za-z0-9]{53}", password_hash):
        raise ValueError("invalid AdGuard bcrypt hash")
    content = template.read_text().replace("@@USERNAME@@", json.dumps(username))
    content = content.replace("@@PASSWORD_HASH@@", json.dumps(password_hash))
    conf = state / "conf"
    conf.mkdir(parents=True, exist_ok=True)
    (state / "work").mkdir(exist_ok=True)
    destination = conf / "AdGuardHome.yaml"
    declared = conf / ".declared.yaml"
    # Preserve AdGuard's normalized config and console edits while inputs are unchanged.
    if destination.exists() and declared.exists() and declared.read_text() == content:
        return
    for path in (destination, declared):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(content)
        temporary.chmod(0o600)
        temporary.replace(path)


if __name__ == "__main__":
    os.umask(0o077)
    render(Path("/templates/AdGuardHome.yaml"), Path("/state"),
           os.environ["ADGUARD_USERNAME"], os.environ["ADGUARD_PASSWORD_HASH"])
