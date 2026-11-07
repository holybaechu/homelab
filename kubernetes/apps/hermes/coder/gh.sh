#!/bin/sh
set -eu

# Read the mounted credential at invocation, keeping it out of profile/PVC files.
GH_TOKEN=$(cat /opt/coder-credentials/github-token)
export GH_TOKEN
exec /opt/coder/bin/gh-real "$@"
