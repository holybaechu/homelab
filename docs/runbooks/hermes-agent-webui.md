# Hermes Agent WebUI

Hermes Agent WebUI runs in the `hermes` Debian LXC and is exposed only through the private Caddy route at `https://hermes.home.hchu.me`.

## Secrets

Set `HERMES_WEBUI_PASSWORD` in GitHub Actions secrets. The CD workflow writes it to Ansible as `hermes_webui_password`.

Provider/model API keys are not deployed by this repo. Complete provider/model setup from the WebUI onboarding flow or from the Hermes CLI after the service is running.

## Deploy

1. Apply the OpenTofu LXC changes:

   The Hermes LXC topology is tracked in
   `infra/opentofu/envs/prod/containers.auto.tfvars`. Keep private provider
   values in the ignored `infra/opentofu/envs/prod/terraform.tfvars` file, or
   provide them through the CI-generated `ci.auto.tfvars.json` path.

   ```sh
   ./scripts/ci/tofu-plan.sh
   ./scripts/ci/tofu-apply.sh
   ```

2. Bootstrap Proxmox storage, root-only bind mounts, SSH, and base packages:

   ```sh
   ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/bootstrap.yml
   ```

3. Deploy services:

   ```sh
   ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/site.yml --extra-vars @/tmp/ansible-extra-vars.json
   ```

4. Validate services:

   ```sh
   ansible-playbook -i infra/ansible/inventory/prod/hosts.yml infra/ansible/playbooks/validate.yml
   ```

## First Login

1. Open `https://hermes.home.hchu.me` from LAN or Tailscale.
2. Log in with `HERMES_WEBUI_PASSWORD`.
3. Complete provider/model setup in the WebUI onboarding flow or with Hermes CLI.
4. Confirm `/workspace` is selected as the default writable workspace.

## Storage

Persistent host paths:

- `/var/lib/homelab/hermes/home` is mounted inside the LXC as `/var/lib/hermes`.
- `/var/lib/homelab/hermes/workspace` is mounted inside the LXC as `/workspace`.

The Hermes service does not mount unrelated homelab datasets.
