import os
from pathlib import Path
import re


def render(template, destination, username, password):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", username):
        raise ValueError("invalid Copyparty account name")
    if not password or any(c in password for c in "\x00\r\n") or password != password.strip():
        raise ValueError("Copyparty password must be one nonempty line without outer whitespace")
    # Copyparty treats a space followed by # as a trailing config comment.
    if " #" in password:
        raise ValueError("Copyparty password cannot contain a space followed by #")
    content = template.read_text().replace("@@OWNER@@", username)
    content = content.replace("@@ACCOUNTS@@", f"  {username}: {password}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(content)
    temporary.chmod(0o600)
    temporary.replace(destination)


if __name__ == "__main__":
    os.umask(0o077)
    render(Path("/templates/copyparty.conf"), Path("/generated/copyparty.conf"),
           os.environ["COPYPARTY_USERNAME"], os.environ["COPYPARTY_PASSWORD"])
    Path("/downloads/complete").mkdir(exist_ok=True)
    Path("/downloads/incomplete").mkdir(exist_ok=True)
