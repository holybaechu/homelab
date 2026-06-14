# Minecraft Server Runbook

## Topology

- LXC: `minecraft`
- LAN IP: `192.168.0.8`
- Java entrypoint: Velocity on TCP `25565`
- Bedrock entrypoint: Geyser on UDP `19132`
- Backend: Paper on `127.0.0.1:25566`
- Whitelist: disabled; any authenticated Java or Floodgate Bedrock player can join.
- This server does not source-control a Minecraft player allowlist.

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

1. Join from Java using `hchu.me`.
2. Join from Bedrock using `home.hchu.me`, port `19132`.
3. Confirm both clients reach the same Paper backend world through Velocity.

## Bedrock And Java Account Linking

Floodgate global linking is enabled in the Velocity Floodgate config. Local linking on this server is disabled, so use GeyserMC global linking.

Before linking, move any Bedrock-only inventory, ender chest contents, armor, or
location-dependent progress to the Java account. After linking, Floodgate uses
the Java account's player data when the Bedrock account joins.

1. Join `link.geysermc.org` with both accounts.
   - Java: `link.geysermc.org`, port `25565`.
   - Bedrock: `link.geysermc.org`, port `19132`.
2. On either account, run `/linkaccount`.
3. Copy the random code to the other account with `/linkaccount <code>`.
4. Wait for the success kick message on both accounts.
5. Join this server again. Java still uses `hchu.me`; Bedrock uses
  `home.hchu.me`, port `19132`.

To unlink later, join `link.geysermc.org` from either account and run `/unlinkaccount`.
