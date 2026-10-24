import configparser
import os
from pathlib import Path


def merge(defaults, managed, destination):
    config = configparser.RawConfigParser(strict=False)
    config.optionxform = str
    # Defaults fill gaps; existing UI settings win; managed credentials win last.
    for source in (defaults, destination, managed):
        if source == destination and not source.exists():
            continue
        with source.open() as stream:
            config.read_file(stream)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    with temporary.open("w") as output:
        config.write(output, space_around_delimiters=False)
    temporary.chmod(0o600)
    temporary.replace(destination)


if __name__ == "__main__":
    os.umask(0o077)
    merge(Path("/templates/defaults.conf"), Path("/credentials/managed.conf"),
          Path("/config/qBittorrent/qBittorrent.conf"))
    for directory in ("complete", "incomplete"):
        Path("/downloads", directory).mkdir(exist_ok=True)
