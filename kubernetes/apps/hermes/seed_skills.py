"""Seed pinned native skills while preserving existing and relocated user copies."""

from tools import skills_sync as ss
from tools.skills_sync_optional import restore_official_optional_skill

ss.sync_skills(quiet=True)
present = any(
    ss.read_skill_name(path, path.parent.name) == "llm-wiki"
    for path in ss._iter_active_skill_mds()
)
if not present:
    result = restore_official_optional_skill("llm-wiki", restore=True)
    if not result["ok"]:
        raise RuntimeError(result["message"])
