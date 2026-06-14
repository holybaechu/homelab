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

Bedrock clients connect to `hchu.me` or `home.hchu.me` on UDP port `19132`. Java SRV records do not replace the Bedrock port.

## Router Forwards

Forward these ports to `192.168.0.8`:

- TCP 25565 -> 192.168.0.8:25565
- UDP 19132 -> 192.168.0.8:19132

Do not forward TCP `25566`; Paper must stay reachable only through Velocity.

## Join Checks

1. Join from Java as `holybaechu` using `hchu.me`.
2. Join from Bedrock as `holybaechuwu` using `hchu.me` or `home.hchu.me`, port `19132`.
3. Try an unlisted Java account and confirm it is rejected.
4. Try an unlisted Bedrock account and confirm it is rejected.

## Bedrock UUID Cache

The role resolves Bedrock allowlist identities through GeyserMC's UUID utility API. If the Geyser cache misses, the role fails before writing the allowlist and reports `failed to resolve Minecraft allowlist. For Bedrock players, make sure the gamertag is known to the GeyserMC UUID API cache or provide the correct Floodgate UUID before enabling the whitelist.`, followed by `failed to resolve Bedrock player holybaechuwu: ...`. Prime the cache by signing into any Geyser-backed server once as `holybaechuwu`, then rerun the Minecraft Ansible role. The role must not disable the whitelist to let unknown players into the world.
