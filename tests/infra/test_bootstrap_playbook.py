from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_bootstrap_retries_ssh_keyscan_for_lxc_hosts():
    bootstrap = (
        REPO_ROOT / "infra" / "ansible" / "playbooks" / "bootstrap.yml"
    ).read_text(encoding="utf-8")

    assert 'host="{{ item }}"' in bootstrap
    assert "for attempt in 1 2 3 4 5 6; do" in bootstrap
    assert 'ssh-keyscan -H -T 10 "${host}"' in bootstrap
    assert "ssh-keyscan failed for ${host} after 6 attempts" in bootstrap
