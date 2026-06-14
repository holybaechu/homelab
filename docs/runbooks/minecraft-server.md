# Minecraft Server Runbook

## Topology

- LXC: `minecraft`
- LAN IP: `192.168.0.8`
- Java entrypoint: Velocity on TCP `25565`
- Bedrock entrypoint: Geyser on UDP `19132`
- Backend: Paper on `127.0.0.1:25566`
- Java allowlist: `holybaechu`
- Bedrock allowlist: `holybaechuwu`, represented on the backend as `.holybaechuwu`

## Deploy

Run these commands from the repository root.

1. Apply the OpenTofu LXC change.

   Before running `tofu plan` or `tofu apply`, update the ignored
   `infra/opentofu/envs/prod/terraform.tfvars` file with the minecraft block
   from `infra/opentofu/envs/prod/terraform.tfvars.example`. For first-time
   setup, copy `terraform.tfvars.example` to `terraform.tfvars`, then replace
   real secrets and environment-specific values before planning or applying.
   Changing only terraform.tfvars.example does not deploy the LXC.

   ```sh
   tofu -chdir=infra/opentofu/envs/prod plan
   tofu -chdir=infra/opentofu/envs/prod apply
   ```

2. Bootstrap access for the new LXC.

   ```sh
   export ANSIBLE_CONFIG=infra/ansible/ansible.cfg
   ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/bootstrap.yml --limit pve,minecraft
   ```

3. Add the Minecraft LXC host key to known hosts.

   ```sh
   mkdir -p "${HOME}/.ssh"
   touch "${HOME}/.ssh/known_hosts"
   chmod 700 "${HOME}/.ssh"
   chmod 600 "${HOME}/.ssh/known_hosts"
   ssh-keygen -R 192.168.0.8 >/dev/null 2>&1 || true
   ssh-keyscan -H -T 10 192.168.0.8 >> "${HOME}/.ssh/known_hosts"
   ```

4. Configure the Minecraft LXC.

   ```sh
   export ANSIBLE_CONFIG=infra/ansible/ansible.cfg
   ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/site.yml --limit minecraft
   ```

5. Validate the Minecraft runtime.

   ```sh
   export ANSIBLE_CONFIG=infra/ansible/ansible.cfg
   ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/validate.yml --limit minecraft
   ```

## DNS

Keep the existing DDNS path for `home.hchu.me`.

Create or verify this Java SRV record:

| Type | Name | Priority | Weight | Port | Target |
| --- | --- | ---: | ---: | ---: | --- |
| SRV | `_minecraft._tcp.hchu.me` | `0` | `0` | `25565` | `home.hchu.me` |

Bedrock clients connect to `home.hchu.me` on UDP port `19132`. Java SRV records do not replace the Bedrock port, and Bedrock clients should not use `hchu.me` unless a separate DNS-only Bedrock hostname is created.

## Router Forwards

Forward these ports to `192.168.0.8`:

- TCP 25565 -> 192.168.0.8:25565
- UDP 19132 -> 192.168.0.8:19132

Do not forward TCP `25566`; Paper must stay reachable only through Velocity.

## Join Checks

1. Join from Java as `holybaechu` using `hchu.me`.
2. Join from Bedrock as `holybaechuwu` using `home.hchu.me`, port `19132`.
3. Try an unlisted Java account and confirm it is rejected.
4. Try an unlisted Bedrock account and confirm it is rejected.

## Bedrock UUID Cache

The role resolves Bedrock allowlist identities through GeyserMC's UUID utility API. If the Geyser cache misses, the role fails before writing the allowlist and reports `failed to resolve Minecraft allowlist. For Bedrock players, make sure the gamertag is known to the GeyserMC UUID API cache or provide the correct Floodgate UUID before enabling the whitelist.`, followed by `failed to resolve Bedrock player holybaechuwu: ...`. Prime the cache by signing into any Geyser-backed server once as `holybaechuwu`, then rerun the Minecraft Ansible role. If cache priming is inconvenient, add `uuid` under the Bedrock entry in `apps/minecraft/allowed-players.yml` with the correct Floodgate UUID. The resolver accepts dashed UUIDs or 32-hex UUIDs and writes the backend name as `.holybaechuwu`. The role must not disable the whitelist to let unknown players into the world.
