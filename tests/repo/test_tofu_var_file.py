import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "write_tofu_vars.py"


def load_module():
    spec = importlib.util.spec_from_file_location("write_tofu_vars", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_write_tofu_vars_normalizes_github_secret_values(tmp_path):
    module = load_module()
    output = tmp_path / "ci.auto.tfvars.json"

    module.write_tofu_vars(
        output,
        {
            "PROXMOX_ENDPOINT": '"https://192.168.0.2:8006/"',
            "PROXMOX_API_TOKEN": '"PVEAPIToken=automation@pve!homelab=token-value"',
            "PROXMOX_INSECURE_TLS": "true",
            "PVE_NODE_NAME": "pve",
            "PVE_BRIDGE": "vmbr0",
            "PVE_ROOT_DATASTORE_ID": "local-lvm",
            "DEPLOY_SSH_PUBLIC_KEYS": (
                '\ufeff["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHomelabDeployKeyMaterial '
                'homelab-deploy"]'
            ),
        },
    )

    data = json.loads(output.read_text(encoding="utf-8"))

    assert data["proxmox_endpoint"] == "https://192.168.0.2:8006/"
    assert data["proxmox_api_token"] == "PVEAPIToken=automation@pve!homelab=token-value"
    assert data["proxmox_insecure_tls"] is True
    assert data["node_name"] == "pve"
    assert data["bridge"] == "vmbr0"
    assert data["root_datastore_id"] == "local-lvm"
    assert data["ssh_public_keys"] == [
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHomelabDeployKeyMaterial homelab-deploy"
    ]


def test_write_tofu_vars_accepts_newline_public_keys(tmp_path):
    module = load_module()
    output = tmp_path / "ci.auto.tfvars.json"

    module.write_tofu_vars(
        output,
        {
            "PROXMOX_ENDPOINT": "https://192.168.0.2:8006/",
            "PROXMOX_API_TOKEN": "PVEAPIToken=automation@pve!homelab=token-value",
            "PROXMOX_INSECURE_TLS": "false",
            "PVE_NODE_NAME": "pve",
            "PVE_BRIDGE": "vmbr0",
            "PVE_ROOT_DATASTORE_ID": "local-lvm",
            "DEPLOY_SSH_PUBLIC_KEYS": "ssh-ed25519 key-one\nssh-ed25519 key-two",
        },
    )

    data = json.loads(output.read_text(encoding="utf-8"))

    assert data["proxmox_insecure_tls"] is False
    assert data["ssh_public_keys"] == ["ssh-ed25519 key-one", "ssh-ed25519 key-two"]
