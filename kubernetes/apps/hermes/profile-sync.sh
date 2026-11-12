#!/bin/bash
set -euo pipefail

export HOME="$(printf '%s' "$HERMES_HOME" | sed 's@/profiles/.*@@')"
unset XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME
source="$HOME/.local/share/chezmoi"
test -d "$source/.git"
exec 9>"$HOME/.config/chezmoi/profile-sync.lock"
flock 9
test ! -d "$source/.git/rebase-merge"
test ! -d "$source/.git/rebase-apply"
test ! -f "$source/.git/MERGE_HEAD"
test -z "$(git -C "$source" ls-files --unmerged)"

chezmoi managed --include files,symlinks --path-style absolute -0 |
  while IFS= read -r -d '' target; do
    [[ -e "$target" || -L "$target" ]] || printf '%s\0' "$target"
  done | xargs -0 -r chezmoi forget --force
shopt -s nullglob
chezmoi add --exact --quiet "$HOME"/* "$HOME"/.[!.]*
if [[ "$*" == start ]]; then
  chezmoi --no-tty --error-on-conflict update
fi
chezmoi git -- push --quiet
