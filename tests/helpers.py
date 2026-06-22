from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parents[1]


def read_repo_text(*parts: str) -> str:
    return (REPO_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def render_template(template_dir: Path, template_name: str, **values: Any) -> str:
    env = Environment(
        loader=FileSystemLoader(template_dir),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template(template_name).render(**values)


def render_role_template(role: str, template_name: str, **values: Any) -> str:
    return render_template(
        REPO_ROOT / "infra" / "ansible" / "roles" / role / "templates",
        template_name,
        **values,
    )
