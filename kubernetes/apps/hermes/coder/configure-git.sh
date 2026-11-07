#!/bin/sh
set -eu

# Raw Secret files are root-only; runtime copies live in a memory-backed volume.
install -m 400 -o 10000 -g 10000 /var/run/coder-source/github-token /opt/coder-credentials/github-token
install -m 400 -o 10000 -g 10000 /var/run/coder-source/signing-key /opt/coder-credentials/signing-key
ssh-keygen -y -P '' -f /opt/coder-credentials/signing-key > /opt/coder-credentials/signing-key.pub
printf 'holybaechu@gmail.com namespaces="git" %s\n' "$(cat /opt/coder-credentials/signing-key.pub)" > /opt/coder-credentials/allowed-signers
chown 10000:10000 /opt/coder-credentials/signing-key.pub /opt/coder-credentials/allowed-signers
chmod 400 /opt/coder-credentials/signing-key.pub /opt/coder-credentials/allowed-signers
