# Homelab Storage Resource Resize Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resize declared homelab storage and LXC resources while removing retired Copyparty shares.

**Architecture:** OpenTofu owns LXC CPU, memory, and root disk declarations. Ansible owns Proxmox host bind mount declarations, shared data LV creation defaults, and Copyparty service configuration. Tests should assert the intended declarations so future resource drift is caught before deployment.

**Tech Stack:** OpenTofu HCL, Ansible YAML, Copyparty config, pytest, Python standard library.

---

### Task 1: Add Resource Sizing Tests

**Files:**
- Create: `tests/infra/test_lxc_resource_sizing.py`
- Read if present: `infra/opentofu/envs/prod/terraform.tfvars`
- Read: `infra/opentofu/envs/prod/terraform.tfvars.example`
- Read: `infra/ansible/inventory/prod/group_vars/all.yml`

- [ ] **Step 1: Write failing sizing tests**

Create `tests/infra/test_lxc_resource_sizing.py`:

```python
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[2]


def tfvars_container_body(tfvars_text: str, name: str) -> str:
    match = re.search(rf"^  {name} = \{{(?P<body>.*?)^  \}}", tfvars_text, re.MULTILINE | re.DOTALL)
    assert match is not None, f"{name} container block not found"
    return match.group("body")


def numeric_value(container_body: str, key: str) -> int:
    match = re.search(rf"^\s+{key}\s+=\s+(\d+)$", container_body, re.MULTILINE)
    assert match is not None, f"{key} not found"
    return int(match.group(1))


def assert_container_sizing(tfvars_path: Path):
    tfvars_text = tfvars_path.read_text(encoding="utf-8")

    expected = {
        "edge": {"root_disk_gb": 6, "cores": 1, "memory_mb": 512},
        "downloads": {"root_disk_gb": 8, "cores": 2, "memory_mb": 1024},
        "files": {"root_disk_gb": 4, "cores": 1, "memory_mb": 512},
        "dns": {"root_disk_gb": 4, "cores": 1, "memory_mb": 512},
        "tailnet": {"root_disk_gb": 4, "cores": 1, "memory_mb": 512},
    }

    for name, values in expected.items():
        body = tfvars_container_body(tfvars_text, name)
        for key, expected_value in values.items():
            assert numeric_value(body, key) == expected_value


def test_prod_lxc_resource_sizing_matches_capacity_plan():
    prod_tfvars_path = (
        REPO_ROOT / "infra" / "opentofu" / "envs" / "prod" / "terraform.tfvars"
    )
    if prod_tfvars_path.exists():
        assert_container_sizing(prod_tfvars_path)


def test_example_lxc_resource_sizing_matches_capacity_plan():
    assert_container_sizing(
        REPO_ROOT
        / "infra"
        / "opentofu"
        / "envs"
        / "prod"
        / "terraform.tfvars.example"
    )


def test_homelab_data_lv_size_matches_capacity_plan():
    all_vars_path = (
        REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "group_vars" / "all.yml"
    )
    all_vars_text = all_vars_path.read_text(encoding="utf-8")

    assert re.search(r"^homelab_data_lv_size: 896G$", all_vars_text, re.MULTILINE)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/infra/test_lxc_resource_sizing.py -q`

Expected: FAIL because `edge.root_disk_gb`, `downloads.memory_mb`, `files.root_disk_gb`, `files.memory_mb`, and `homelab_data_lv_size` still use the old values.

### Task 2: Update LXC Resource Declarations

**Files:**
- Modify: `infra/opentofu/envs/prod/terraform.tfvars`
- Modify: `infra/opentofu/envs/prod/terraform.tfvars.example`
- Modify: `infra/ansible/inventory/prod/group_vars/all.yml`

- [ ] **Step 1: Update OpenTofu prod and example sizing**

Set these values in both tfvars files:

```hcl
edge.root_disk_gb = 6
downloads.memory_mb = 1024
files.root_disk_gb = 4
files.memory_mb = 512
```

Leave all CPU allocations unchanged.

- [ ] **Step 2: Update homelab data LV declaration**

Set this value in `infra/ansible/inventory/prod/group_vars/all.yml`:

```yaml
homelab_data_lv_size: 896G
```

- [ ] **Step 3: Run sizing test to verify it passes**

Run: `python -m pytest tests/infra/test_lxc_resource_sizing.py -q`

Expected: PASS.

### Task 3: Remove Retired Copyparty Shares

**Files:**
- Modify: `tests/files/test_copyparty_config_template.py`
- Modify: `apps/files/copyparty.conf`
- Modify: `infra/ansible/roles/copyparty/templates/copyparty.conf.j2`
- Modify: `infra/ansible/roles/copyparty/tasks/main.yml`
- Modify: `infra/ansible/inventory/prod/group_vars/all.yml`
- Modify: `infra/ansible/roles/pve_homelab_storage/tasks/main.yml`

- [ ] **Step 1: Expand Copyparty config test**

Add assertions to `test_copyparty_config_renders_only_supplied_accounts`:

```python
    assert "[/music]" not in rendered
    assert "/srv/music" not in rendered
    assert "[/bjh_deepfake_contest]" not in rendered
    assert "/srv/bjh_deepfake_contest" not in rendered
```

- [ ] **Step 2: Run Copyparty config test to verify it fails**

Run: `python -m pytest tests/files/test_copyparty_config_template.py -q`

Expected: FAIL because the template still renders the retired shares.

- [ ] **Step 3: Remove retired Copyparty shares from config files**

Delete the `[/music]` and `[/bjh_deepfake_contest]` sections from:

```text
apps/files/copyparty.conf
infra/ansible/roles/copyparty/templates/copyparty.conf.j2
```

- [ ] **Step 4: Remove retired mount checks from Copyparty role**

Remove these entries from the `Check Copyparty data mounts` loop in `infra/ansible/roles/copyparty/tasks/main.yml`:

```yaml
    - /srv/music
    - /srv/bjh_deepfake_contest
```

- [ ] **Step 5: Remove retired Files LXC bind mounts**

Remove these bind mount sources and settings from the `files` entry in `infra/ansible/inventory/prod/group_vars/all.yml`:

```yaml
      - /var/lib/homelab/copyparty/music
      - /var/lib/homelab/copyparty/bjh_deepfake_contest
```

```yaml
      - description: mount music Copyparty share
        pattern: '^mp3: /var/lib/homelab/copyparty/music,mp=/srv/music(,.*)?$'
        pct_args: '-mp3 /var/lib/homelab/copyparty/music,mp=/srv/music'
      - description: mount contest Copyparty share
        pattern: '^mp4: /var/lib/homelab/copyparty/bjh_deepfake_contest,mp=/srv/bjh_deepfake_contest(,.*)?$'
        pct_args: '-mp4 /var/lib/homelab/copyparty/bjh_deepfake_contest,mp=/srv/bjh_deepfake_contest'
```

- [ ] **Step 6: Remove retired host directory creation and chmod**

Remove the `music` and `bjh_deepfake_contest` paths from `infra/ansible/roles/pve_homelab_storage/tasks/main.yml`.

- [ ] **Step 7: Run Copyparty config test to verify it passes**

Run: `python -m pytest tests/files/test_copyparty_config_template.py -q`

Expected: PASS.

### Task 4: Add Runtime Resize Runbook

**Files:**
- Create: `docs/runbooks/homelab-storage-resize.md`

- [ ] **Step 1: Write runbook**

Create `docs/runbooks/homelab-storage-resize.md` with these sections:

```markdown
# Homelab Storage Resize Runbook

## Intent

This runbook applies the manual runtime side of the repo-declared storage resize.

## Safety

- Back up important data from `/var/lib/homelab` before shrinking.
- Stop affected LXCs before unmounting or resizing shared storage.
- Do not shrink ext4 or LVM below observed used space.

## Data LV Shrink Outline

1. Stop affected LXCs.
2. Confirm `/var/lib/homelab` has a current backup.
3. Unmount `/var/lib/homelab`.
4. Run `e2fsck -f /dev/pve/homelab-data`.
5. Run `resize2fs /dev/pve/homelab-data 880G`.
6. Run `lvreduce -L 896G /dev/pve/homelab-data`.
7. Run `e2fsck -f /dev/pve/homelab-data`.
8. Mount `/var/lib/homelab`.
9. Start affected LXCs.

## Verification

- Copyparty reports reduced total capacity.
- `/srv/downloads` is mounted read-only in the `files` LXC.
- `/downloads` is writable in the `downloads` LXC.
- `/srv/music` and `/srv/bjh_deepfake_contest` are no longer active Copyparty shares.
```

### Task 5: Final Validation

**Files:**
- Read: changed files from Tasks 1-4

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m pytest tests/infra/test_lxc_resource_sizing.py tests/files/test_copyparty_config_template.py -q
```

Expected: PASS.

- [ ] **Step 2: Run broad relevant test subset**

Run:

```powershell
python -m pytest tests/files tests/repo -q
```

Expected: PASS.

- [ ] **Step 3: Search for retired share references**

Run:

```powershell
rg -n "bjh_deepfake_contest|/srv/music|copyparty/music|/music" apps infra tests docs/runbooks
```

Expected: only historical design/plan references remain under `docs/superpowers`.

- [ ] **Step 4: Review git diff**

Run: `git diff --check`

Expected: no whitespace errors.
