# Game Compose Project

Runs the Minecraft app plane with itzg images while keeping the Docker host
separate from Tailnet and Hermes. The Paper container is internal-only; the
Velocity/proxy container exposes Java and Bedrock/Geyser ports.

Use the gaming-mode runbook/script to stop this project before launching the
Windows gaming VM on the 24 GB host.
