#!/bin/sh
set -eu

cd "$(git -C "$HERMES_HOME" rev-parse --show-toplevel)"
test -f .gitignore
exec 9>.git/profile-sync.lock
flock 9
git add -A
if ! git diff --cached --quiet; then
  git commit --quiet -m 'chore(hermes): update profile state'
fi
git push --quiet origin main
