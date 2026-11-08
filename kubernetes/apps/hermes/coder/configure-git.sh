#!/bin/sh
set -eu

exec /opt/hermes/.venv/bin/python /bootstrap/coder-configure-git.py
