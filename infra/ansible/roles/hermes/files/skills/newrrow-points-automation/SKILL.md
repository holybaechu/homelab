---
name: newrrow-points-automation
description: Use when the user asks Hermes to open Newrrow or complete, automate, verify, or recover Newrrow daily or weekly point tasks using Hermes browser tools, Browserbase routing, and 1Password-backed login.
version: 1.0.0
author: holybaechu + Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [newrrow, browser-automation, 1password, points, korean]
    related_skills: [1password, hermes-agent]
---

# Newrrow Points Automation

Automate the Newrrow point checklist through Hermes browser automation. Newrrow is a public URL, so the default homelab route is Hermes `browser_*` tools backed by Browserbase; local `agent-browser` is reserved only for Hermes' private/LAN auto-local sidecar. Use visible UI state only; do not inspect cookies, local storage, password stores, browser profile files, or hidden credential data.

## Required Setup

This skill is installed by homelab IaC at:

```bash
$HERMES_HOME/skills/productivity/newrrow-points-automation
```

The Hermes gateway environment provides:

- `NEWRROW_USERNAME_REF` (1Password secret reference for the Newrrow username)
- `NEWRROW_PASSWORD_REF` (1Password secret reference for the Newrrow password)
- `OP_SERVICE_ACCOUNT_TOKEN` for non-interactive `op` access
- the `newrrow-browser-login` Hermes plugin, which exposes `newrrow_browser_login` inside the `browser` toolset

The Newrrow URL is intentionally hardcoded in this skill/plugin as `https://gbsm.newrrow.com/csr-platform/home` instead of being rendered as a gateway environment variable.

Before acting, read `references/ui-flow.md`. It contains the observed routes, point checklist, selectors, and safety rules for submissions.

## Login with 1Password

Start with the Hermes browser tools, not the standalone `agent-browser` CLI:

1. Call `browser_navigate` on `https://gbsm.newrrow.com/csr-platform/home`.
2. If the snapshot shows the authenticated Newrrow home, continue the point workflow with `browser_click`, `browser_type`, `browser_scroll`, `browser_press`, and `browser_snapshot`.
3. If the snapshot shows the login form, call `newrrow_browser_login` once. The plugin reads `NEWRROW_USERNAME_REF` and `NEWRROW_PASSWORD_REF` with `op read` inside the tool handler, injects credentials into the active Hermes browser-tool session through CDP, and returns only non-secret status. This preserves the Hermes browser router: public Newrrow URLs use Browserbase in homelab, while private/LAN URLs still use Hermes' local sidecar.

Do **not** call `browser_type` with raw Newrrow passwords or `op read` output; `browser_type` arguments/results are model-visible. Do **not** use bare `agent-browser open/auth/login` for Newrrow runtime operation because that bypasses Hermes browser routing and can silently fall back to local Chromium. The `scripts/newrrow-login.sh` file is only a 1Password credential preflight helper for deployment/debugging; it does not log into the site.

If the plugin cannot read the configured 1Password refs, report the missing ref names and ask the user to update the 1Password item or IaC variables. If Newrrow requires 2FA, CAPTCHA, account chooser, or another visible security prompt, stop and ask the user to complete that step.

## Operating Rules

- Work in Korean when the user is Korean.
- Start from `https://gbsm.newrrow.com/csr-platform/home`; identify today's date and which checklist items already show `완료`.
- Before point actions, create or update a Hermes `todo` plan using the visible Newrrow point checklist as plan steps. Keep exactly one unresolved action `in_progress`, and update the plan as soon as each item is visibly confirmed, skipped, or blocked.
- Skip items that are already complete. Do not duplicate submissions just to increase confidence.
- Prefer short default content for generic point tasks, such as `클컴 공부`, `자바 공부`, or `뉴로우 포인트 점검`.
- Use visible confirmation after every action: toast/status text, enabled/disabled button changes, completed cards, route changes, or dashboard point movement.
- Use the preapproved defaults in `references/ui-flow.md` for reflection comments, reflection sharing, gratitude cards, short personal content, and scoring attempts.
- Never fabricate completion of a training or assignment. For score prompts, write the strongest honest answers first and aim for 5 when the completed answers support it.

## Completion Report

Report a compact checklist with `done`, `already done`, `skipped needs input`, or `blocked` for each point item. Include any exact recipients or public/shared actions that were performed.
