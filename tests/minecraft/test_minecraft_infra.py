import re




from tests.helpers import REPO_ROOT
def read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def tfvars_container_body(tfvars_text: str, name: str) -> str:
    match = re.search(
        rf"^  {name} = \{{(?P<body>.*?)^  \}}",
        tfvars_text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"{name} container block not found"
    return match.group("body")


def numeric_value(container_body: str, key: str) -> int:
    match = re.search(rf"^\s+{key}\s+=\s+(\d+)$", container_body, re.MULTILINE)
    assert match is not None, f"{key} not found"
    return int(match.group(1))


def test_minecraft_lxc_is_declared_in_tracked_topology_tfvars():
    tfvars = read("infra/opentofu/envs/prod/containers.auto.tfvars")
    body = tfvars_container_body(tfvars, "minecraft")

    assert 'hostname         = "minecraft"' in body
    assert 'description      = "Velocity and Paper Minecraft server managed by OpenTofu and Ansible"' in body
    assert 'tags             = ["homelab", "managed-by-opentofu", "role-minecraft"]' in body
    assert 'template_file_id = "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst"' in body
    assert 'os_type          = "debian"' in body
    assert 'ip_address       = "192.168.0.8/24"' in body
    assert 'mac_address      = "02:00:00:BA:EC:08"' in body
    assert numeric_value(body, "vmid") == 115
    assert numeric_value(body, "root_disk_gb") == 32
    assert numeric_value(body, "cores") == 4
    assert numeric_value(body, "memory_mb") == 4096
    assert numeric_value(body, "swap_mb") == 1024
    assert numeric_value(body, "startup_order") == 6


def test_minecraft_inventory_is_debian_host_and_role_group():
    hosts = read("infra/ansible/inventory/prod/hosts.yml")

    assert re.search(
        r"debian:\s*\n\s+hosts:.*minecraft:\s*\n\s+ansible_host: 192\.168\.0\.8",
        hosts,
        re.DOTALL,
    )
    assert re.search(r"minecraft:\s*\n\s+hosts:\s*\n\s+minecraft:", hosts)


def test_minecraft_ip_and_bootstrap_are_in_all_group_vars():
    all_vars = read("infra/ansible/inventory/prod/group_vars/all.yml")

    assert re.search(r"^minecraft_ip: 192\.168\.0\.8$", all_vars, re.MULTILINE)
    assert "  - vmid: 115\n    name: minecraft\n    os_family: debian" in all_vars
