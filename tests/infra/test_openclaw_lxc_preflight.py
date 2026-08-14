import importlib.util
import ipaddress
from pathlib import Path
import sys

import pytest

from tests.helpers import REPO_ROOT


SCRIPT = REPO_ROOT / "scripts" / "ci" / "preflight-openclaw-lxc.py"
SPEC = importlib.util.spec_from_file_location("openclaw_lxc_preflight", SCRIPT)
assert SPEC and SPEC.loader
PREFLIGHT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PREFLIGHT
SPEC.loader.exec_module(PREFLIGHT)


@pytest.fixture
def allocation():
    return PREFLIGHT.Allocation(
        vmid=118,
        hostname="openclaw",
        ip_address=ipaddress.ip_interface("192.168.0.5/24"),
        mac_address="02:00:00:BA:EC:05",
        datastore_id="local-lvm",
        required_storage_bytes=32 * 1024**3,
    )


@pytest.fixture
def config_root(tmp_path: Path) -> Path:
    for kind in ("lxc", "qemu-server"):
        (tmp_path / "nodes" / "pve" / kind).mkdir(parents=True)
    return tmp_path


def write_config(config_root: Path, kind: str, vmid: int, text: str) -> Path:
    path = config_root / "nodes" / "pve" / kind / f"{vmid}.conf"
    path.write_text(text, encoding="utf-8")
    return path


def exact_openclaw_config(extra: str = "") -> str:
    return (
        "hostname: openclaw\n"
        "unprivileged: 1\n"
        "tags: homelab;managed-by-opentofu;role-openclaw\n"
        "rootfs: local-lvm:subvol-118-disk-0,size=32G\n"
        "net0: name=veth0,bridge=vmbr0,gw=192.168.0.1,"
        "hwaddr=02:00:00:BA:EC:05,ip=192.168.0.5/24,type=veth\n"
        f"{extra}"
    )


def ctf_executor_allocation():
    return PREFLIGHT.Allocation(
        vmid=119,
        hostname="ctf-executor",
        ip_address=ipaddress.ip_interface("192.168.0.6/24"),
        mac_address="02:00:00:BA:EC:06",
        datastore_id="local-lvm",
        required_storage_bytes=64 * 1024**3,
        role_tag="role-ctf-executor",
        required_features=frozenset({"nesting", "keyctl"}),
        expected_bind_mounts=(
            "/var/lib/homelab/openclaw-ctf,mp=/srv/openclaw-ctf",
            "/var/lib/homelab/openclaw-ctf-sandbox-skills,mp=/var/lib/openclaw/sandbox/skills-workspaces",
        ),
        allow_missing_expected_bind_mounts=True,
    )


def exact_ctf_executor_config(extra: str = "") -> str:
    return (
        "hostname: ctf-executor\n"
        "unprivileged: 1\n"
        "tags: homelab;managed-by-opentofu;role-ctf-executor\n"
        "rootfs: local-lvm:subvol-119-disk-0,size=64G\n"
        "net0: name=veth0,bridge=vmbr0,gw=192.168.0.1,"
        "hwaddr=02:00:00:BA:EC:06,ip=192.168.0.6/24,type=veth\n"
        "features: nesting=1,keyctl=1\n"
        "mp0: /var/lib/homelab/openclaw-ctf,mp=/srv/openclaw-ctf\n"
        "mp1: /var/lib/homelab/openclaw-ctf-sandbox-skills,mp=/var/lib/openclaw/sandbox/skills-workspaces\n"
        f"{extra}"
    )


def storage_ok(datastore_id: str, required_bytes: int) -> dict[str, object]:
    return {
        "datastore_id": datastore_id,
        "available_bytes": 64 * 1024**3,
        "required_additional_bytes": required_bytes,
    }


def test_free_allocation_passes_only_after_an_arp_probe(config_root, allocation):
    observed = []

    def probe(address):
        observed.append(address)
        return "vmbr0", set()

    result = PREFLIGHT.preflight(
        allocation, config_root, probe=probe, storage_probe=storage_ok
    )

    assert result["status"] == "allocation-available"
    assert result["vmid"] == 118
    assert result["interface"] == "vmbr0"
    assert result["storage"]["required_additional_bytes"] == 32 * 1024**3
    assert observed == [ipaddress.ip_address("192.168.0.5")]


def test_exact_existing_unprivileged_lxc_is_idempotently_accepted(
    config_root, allocation
):
    write_config(config_root, "lxc", 118, exact_openclaw_config())

    result = PREFLIGHT.preflight(
        allocation,
        config_root,
        probe=lambda _: ("vmbr0", {"02:00:00:BA:EC:05"}),
        storage_probe=storage_ok,
    )

    assert result["status"] == "existing-managed-target"
    assert result["arp_responders"] == ["02:00:00:BA:EC:05"]
    assert result["storage"]["required_additional_bytes"] == 0


def test_existing_ctf_executor_requires_its_exact_docker_profile(
    config_root,
):
    allocation = ctf_executor_allocation()
    write_config(config_root, "lxc", 119, exact_ctf_executor_config())

    result = PREFLIGHT.preflight(
        allocation,
        config_root,
        probe=lambda _: ("vmbr0", {"02:00:00:BA:EC:06"}),
        storage_probe=storage_ok,
    )

    assert result["status"] == "existing-managed-target"

    write_config(
        config_root,
        "lxc",
        119,
        exact_ctf_executor_config().replace("keyctl=1", "keyctl=0"),
    )
    with pytest.raises(PREFLIGHT.PreflightError, match="unexpected feature set"):
        PREFLIGHT.preflight(
            allocation,
            config_root,
            probe=lambda _: ("vmbr0", {"02:00:00:BA:EC:06"}),
            storage_probe=storage_ok,
        )


def test_staged_ctf_executor_may_be_missing_only_a_declared_mount(config_root):
    allocation = ctf_executor_allocation()
    staged = exact_ctf_executor_config().replace(
        "mp1: /var/lib/homelab/openclaw-ctf-sandbox-skills,mp=/var/lib/openclaw/sandbox/skills-workspaces\n",
        "",
    )
    write_config(config_root, "lxc", 119, staged)

    result = PREFLIGHT.preflight(
        allocation,
        config_root,
        probe=lambda _: ("vmbr0", {"02:00:00:BA:EC:06"}),
        storage_probe=storage_ok,
    )

    assert result["status"] == "existing-managed-target"

    write_config(
        config_root,
        "lxc",
        119,
        staged + "mp2: /var/lib/homelab/unrelated,mp=/srv/unrelated\n",
    )
    with pytest.raises(PREFLIGHT.PreflightError, match="unexpected bind mount set"):
        PREFLIGHT.preflight(
            allocation,
            config_root,
            probe=lambda _: ("vmbr0", {"02:00:00:BA:EC:06"}),
            storage_probe=storage_ok,
        )

    write_config(
        config_root,
        "lxc",
        119,
        staged + "mp2: /var/lib/homelab/openclaw-ctf,mp=/srv/openclaw-ctf\n",
    )
    with pytest.raises(PREFLIGHT.PreflightError, match="unexpected bind mount set"):
        PREFLIGHT.preflight(
            allocation,
            config_root,
            probe=lambda _: ("vmbr0", {"02:00:00:BA:EC:06"}),
            storage_probe=storage_ok,
        )


@pytest.mark.parametrize(
    "kind,text,error",
    [
        ("qemu-server", "name: openclaw\n", "QEMU VM"),
        ("lxc", exact_openclaw_config().replace("openclaw", "other"), "hostname"),
        ("lxc", exact_openclaw_config().replace("unprivileged: 1", "unprivileged: 0"), "unprivileged"),
        ("lxc", exact_openclaw_config().replace("192.168.0.5/24", "192.168.0.6/24"), "network identity"),
        ("lxc", exact_openclaw_config().replace("managed-by-opentofu", "manual"), "managed OpenClaw tags"),
        ("lxc", exact_openclaw_config().replace("local-lvm:", "other-store:"), "datastore"),
        ("lxc", exact_openclaw_config("features: nesting=1,keyctl=1\n"), "forbidden features"),
        ("lxc", exact_openclaw_config("dev0: /dev/net/tun,mode=0666\n"), "TUN"),
        ("lxc", exact_openclaw_config("dev1: path=/dev/net/tun\n"), "TUN"),
        ("lxc", exact_openclaw_config("mp0: /var/lib/homelab,mp=/srv/homelab\n"), "bind mount"),
    ],
)
def test_target_vmid_must_match_the_exact_hardened_identity(
    config_root, allocation, kind, text, error
):
    write_config(config_root, kind, 118, text)

    with pytest.raises(PREFLIGHT.PreflightError, match=error):
        PREFLIGHT.preflight(
            allocation,
            config_root,
            probe=lambda _: ("vmbr0", set()),
            storage_probe=storage_ok,
        )


@pytest.mark.parametrize(
    "claim",
    [
        "net0: ip=192.168.0.5/24\n",
        "net0: hwaddr=02:00:00:ba:ec:05\n",
    ],
)
def test_other_guest_config_ip_or_mac_claim_fails_closed(
    config_root, allocation, claim
):
    write_config(config_root, "lxc", 119, claim)

    with pytest.raises(PREFLIGHT.PreflightError, match="already claimed"):
        PREFLIGHT.preflight(
            allocation,
            config_root,
            probe=lambda _: ("vmbr0", set()),
            storage_probe=storage_ok,
        )


def test_non_network_config_text_does_not_create_a_false_ip_claim(
    config_root, allocation
):
    write_config(
        config_root,
        "lxc",
        119,
        "description: formerly considered 192.168.0.5\nnet0: ip=192.168.0.9/24\n",
    )

    result = PREFLIGHT.preflight(
        allocation,
        config_root,
        probe=lambda _: ("vmbr0", set()),
        storage_probe=storage_ok,
    )

    assert result["status"] == "allocation-available"


def test_active_arp_responder_blocks_a_new_allocation(config_root, allocation):
    with pytest.raises(PREFLIGHT.PreflightError, match="answered ARP"):
        PREFLIGHT.preflight(
            allocation,
            config_root,
            probe=lambda _: ("vmbr0", {"AA:BB:CC:DD:EE:FF"}),
            storage_probe=storage_ok,
        )


def test_existing_target_rejects_a_second_arp_responder(config_root, allocation):
    write_config(config_root, "lxc", 118, exact_openclaw_config())

    with pytest.raises(PREFLIGHT.PreflightError, match="unexpected ARP responder"):
        PREFLIGHT.preflight(
            allocation,
            config_root,
            probe=lambda _: (
                "vmbr0",
                {"02:00:00:BA:EC:05", "AA:BB:CC:DD:EE:FF"},
            ),
            storage_probe=storage_ok,
        )


def test_pvesh_probe_requires_an_enabled_active_rootdir_store_with_enough_space(
    monkeypatch,
):
    observed = []

    def run(command, **kwargs):
        observed.append((command, kwargs))
        return PREFLIGHT.subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                '{"active":1,"avail":68719476736,'
                '"content":"images,rootdir","enabled":1,"shared":0,'
                '"total":137438953472,"type":"lvmthin","used":68719476736}'
            ),
            stderr="",
        )

    monkeypatch.setattr(PREFLIGHT.subprocess, "run", run)

    result = PREFLIGHT.probe_storage("local-lvm", 32 * 1024**3)

    command = observed[0][0]
    assert command == [
        "pvesh",
        "get",
        "/nodes/localhost/storage/local-lvm/status",
        "--output-format",
        "json",
    ]
    assert result["available_bytes"] == 64 * 1024**3


@pytest.mark.parametrize(
    "stdout,error",
    [
        ("[]", "one storage status object"),
        (
            '{"active":0,"avail":68719476736,'
            '"content":"images,rootdir","enabled":1}',
            "not active",
        ),
        (
            '{"active":1,"avail":68719476736,'
            '"content":"images,rootdir","enabled":0}',
            "not enabled",
        ),
        (
            '{"active":1,"avail":68719476736,'
            '"content":"images","enabled":1}',
            "does not allow rootdir",
        ),
        (
            '{"active":1,"avail":1073741824,'
            '"content":"rootdir","enabled":1}',
            "is required",
        ),
        (
            '{"storage":"other","active":1,"avail":68719476736,'
            '"content":"rootdir","enabled":1}',
            "unexpected datastore",
        ),
    ],
)
def test_pvesh_probe_fails_closed_on_unusable_status(monkeypatch, stdout, error):
    monkeypatch.setattr(
        PREFLIGHT.subprocess,
        "run",
        lambda command, **kwargs: PREFLIGHT.subprocess.CompletedProcess(
            command, 0, stdout=stdout, stderr=""
        ),
    )

    with pytest.raises(PREFLIGHT.PreflightError, match=error):
        PREFLIGHT.probe_storage("local-lvm", 32 * 1024**3)


def test_pvesh_probe_accepts_an_api_wrapped_status_object(monkeypatch):
    monkeypatch.setattr(
        PREFLIGHT.subprocess,
        "run",
        lambda command, **kwargs: PREFLIGHT.subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                '{"data":{"active":true,"avail":68719476736,'
                '"content":"rootdir,images","enabled":true}}'
            ),
            stderr="",
        ),
    )

    result = PREFLIGHT.probe_storage("local-lvm", 32 * 1024**3)

    assert result["datastore_id"] == "local-lvm"
    assert result["available_bytes"] == 64 * 1024**3


def test_preflight_playbook_uses_the_committed_allocation_and_is_read_only():
    playbook = (
        REPO_ROOT / "infra" / "ansible" / "playbooks" / "preflight-openclaw-lxc.yml"
    ).read_text(encoding="utf-8")
    variables = (
        REPO_ROOT / "infra" / "ansible" / "inventory" / "prod" / "group_vars" / "all.yml"
    ).read_text(encoding="utf-8")

    assert "hosts: pve_hosts" in playbook
    assert "preflight-openclaw-lxc.py" in playbook
    assert "openclaw_lxc_allocation.vmid" in playbook
    assert "openclaw_lxc_allocation.ip_address" in playbook
    assert "openclaw_lxc_allocation.datastore_id" in playbook
    assert "openclaw_lxc_allocation.required_storage_gb" in playbook
    assert "--expected-bind-mount" in playbook
    assert "openclaw_ctf_sandbox_skills_host_path" in playbook
    assert "openclaw_ctf_sandbox_skills_root" in playbook
    assert "--allow-missing-expected-bind-mounts" in playbook
    assert "changed_when: false" in playbook
    assert "vmid: 118" in variables
    assert 'ip_address: "{{ openclaw_ip }}/24"' in variables
    assert "mac_address: 02:00:00:BA:EC:05" in variables
    assert 'datastore_id: "{{ openclaw_root_datastore_id }}"' in variables
    assert "required_storage_gb: 32" in variables
    for mutating_command in ("pct set", "pct create", "qm set", "qm create"):
        assert mutating_command not in SCRIPT.read_text(encoding="utf-8")
