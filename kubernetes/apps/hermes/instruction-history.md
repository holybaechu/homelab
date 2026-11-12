---
name: instruction-history
description: "Inspect or restore Hermes settings, memories, schedules, SOUL, and skills."
---

# Profile state history

Assistant and coder share a private profile-state repository. Native
`on_session_end` hooks record changes after each agent turn, including interrupted
turns. It covers SOUL, personal skills, configuration, memories, user instructions,
cron job definitions, hooks, scripts, personal plugins and preferences. Installed
skill files and their install/curator metadata are included; mounted operational
skills and managed plugins remain in the homelab repository. Credentials,
conversations, execution databases, logs and caches use their existing storage
and backup paths. Keep credential values in 1Password and use references.

For history and comparisons:

```sh
hermes-instructions log --oneline -- SOUL.md
hermes-instructions log --oneline -- memories/MEMORY.md config.yaml cron/jobs.json
hermes-instructions log --oneline -- skills/research/llm-wiki/SKILL.md
hermes-instructions diff <older-commit> HEAD -- <path>
```

Paths are relative to `/opt/data`. Coder's SOUL is
`profiles/coder/SOUL.md`; its skills are under `profiles/coder/skills`.

For a requested restore, inspect the current file and relevant diff. Restore only
the requested files and verify their contents:

```sh
hermes-instructions restore <commit> -- <path>
```

The command checkpoints pending state before changing files, then records the
restoration as a new commit and pushes both. It re-registers the history hook so
restoring an older config does not silently disable future recording.
Before restoring configuration or schedules, pause dispatch with `hermes pause`
and wait for active work to finish. Review restored schedules before `hermes resume`.
Cluster-managed networking, credential references and integration settings are
reconciled from homelab; restore those through their Git source. Personal model
settings and additional MCP servers remain editable. Use a new conversation to
load restored instructions. A history or push
failure leaves live files and local commits intact; report it and retry
`hermes-instructions save` after resolving the cause. The automatic scan blocks
credential-shaped additions without printing their values. Use the save command
to record changes; it selects and scans the supported instruction paths.
