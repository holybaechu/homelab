from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tests.helpers import REPO_ROOT


SCRIPT = REPO_ROOT / "scripts/ci/verify_pve_access_bundle.py"


def load_module():
    spec = importlib.util.spec_from_file_location("verify_pve_access_bundle", SCRIPT)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


verify = load_module()


def write_bundle(path: Path, keys: list[str], *, version: object = 1) -> None:
    values = {"deploy_ssh_public_keys": keys}
    if version == 2:
        values["proxmox_oidc_client_secret"] = "a" * 64
    path.write_text(
        json.dumps(
            {
                "component": "pve",
                "version": version,
                "values": values,
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("version", [1, 2])
def test_configured_private_key_must_match_one_exact_bundle_identity(tmp_path, capsys, version):
    keygen = shutil.which("ssh-keygen")
    if keygen is None:
        pytest.skip("ssh-keygen is unavailable")
    private_key = tmp_path / "deploy-key"
    subprocess.run(
        [keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(private_key)],
        check=True,
    )
    public_key = verify.derive_public_key(private_key)
    assert verify.PUBLIC_KEY_RE.fullmatch(public_key)
    assert len(public_key.split()) == 2

    bundle = tmp_path / "pve.json"
    write_bundle(bundle, [public_key], version=version)
    assert verify.main(["--private-key", str(private_key), "--bundle", str(bundle)]) == 0
    assert "matches one authorized bundle key" in capsys.readouterr().out

    write_bundle(bundle, ["ssh-ed25519 QUJD"], version=version)
    assert verify.main(["--private-key", str(private_key), "--bundle", str(bundle)]) == 2
    assert "absent from PVE deploy_ssh_public_keys" in capsys.readouterr().err


@pytest.mark.parametrize(
    "keys,version,error",
    [
        (["ssh-ed25519 AAAA operator@example"], 1, "not normalized"),
        (["from=192.0.2.1 ssh-ed25519 AAAA"], 1, "not normalized"),
        (["ssh-ed25519 AAAA\nssh-ed25519 AAAA"], 1, "not normalized"),
        (["ssh-ed25519 AAAA"], True, "identity is invalid"),
    ],
)
def test_bundle_identity_contract_rejects_ambiguous_keys_and_boolean_versions(
    tmp_path, keys, version, error
):
    bundle = tmp_path / "pve.json"
    write_bundle(bundle, keys, version=version)
    with pytest.raises(verify.AccessContractError, match=error):
        verify.require_identity_membership(bundle, "ssh-ed25519 AAAA")


def test_v2_invalid_private_credential_is_rejected_without_disclosure(tmp_path):
    bundle = tmp_path / 'pve.json'
    write_bundle(bundle, ['ssh-ed25519 AAAA'], version=2)
    payload = json.loads(bundle.read_text())
    private_value = 'invalid-private-value-must-not-be-logged'
    payload['values']['proxmox_oidc_client_secret'] = private_value
    bundle.write_text(json.dumps(payload))
    with pytest.raises(verify.AccessContractError, match='identity credential') as error:
        verify.require_identity_membership(bundle, 'ssh-ed25519 AAAA')
    assert private_value not in str(error.value)
