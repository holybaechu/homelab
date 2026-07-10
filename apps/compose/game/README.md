# Game Compose Project

Runs Minecraft with the itzg Paper and Velocity images. The Paper container is
internal-only; Velocity exposes Java and Bedrock/Geyser ports. Both use bind
mounts under `/srv/homelab/minecraft` because world and plugin data must be
backed up and migrated independently of the container lifecycle.

Use the gaming-mode runbook/script to stop this project before launching the
Windows gaming VM on the 24 GB host.
