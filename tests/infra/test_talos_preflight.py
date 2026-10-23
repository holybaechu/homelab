import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader("talos_entrypoint", str(ROOT / "scripts/talos"))
spec = importlib.util.spec_from_loader(loader.name, loader)
talos = importlib.util.module_from_spec(spec)
loader.exec_module(talos)


class ProvisionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            "INSTALL_DISK_SERIAL": "INTERNAL123",
            "INSTALL_DISK_ID": "/dev/disk/by-id/ata-Test_INTERNAL123",
            "PROTECTED_USB_ID": "/dev/disk/by-id/usb-Backup_USB456-0:0",
        }
        self.internal = {"dev_path": "/dev/sdz", "transport": "sata", "readonly": False, "symlinks": [self.data["INSTALL_DISK_ID"]]}
        self.usb = {"dev_path": "/dev/sda", "transport": "usb", "readonly": False, "symlinks": [self.data["PROTECTED_USB_ID"]]}

    def inventory(self, *disks):
        return [{"spec": disk} for disk in disks]

    def test_device_enumeration_cannot_select_usb(self):
        result = talos.validate_disks(self.inventory(self.usb, self.internal), self.data)
        self.assertEqual(result, {"install": "/dev/sdz", "protected_usb": "/dev/sda"})

    def test_absent_or_ambiguous_internal_identity_blocks_install(self):
        for disks in [(self.usb,), (self.internal, self.internal, self.usb)]:
            with self.subTest(disks=disks), self.assertRaises(talos.SafetyError):
                talos.validate_disks(self.inventory(*disks), self.data)

    def test_usb_with_matching_install_identity_is_rejected(self):
        disguised = {**self.usb, "symlinks": [self.data["INSTALL_DISK_ID"], self.data["PROTECTED_USB_ID"]]}
        with self.assertRaises(talos.SafetyError):
            talos.validate_disks(self.inventory(disguised), self.data)

    def test_boot_usb_must_be_identified_before_initial_install(self):
        with self.assertRaises(talos.SafetyError):
            talos.validate_disks(self.inventory(self.internal), self.data)
        self.assertEqual(talos.validate_disks(self.inventory(self.internal), self.data, require_usb=False)["protected_usb"], "absent")

    def test_saved_plan_cannot_reset_replace_or_introduce_other_resources(self):
        for address, actions in [
            ("talos_machine_configuration_apply.node", ["delete", "create"]),
            ("talos_machine_secrets.cluster", ["delete"]),
            ("null_resource.erase_usb", ["create"]),
        ]:
            with self.subTest(address=address), self.assertRaises(talos.SafetyError):
                talos.inspect_plan({"resource_changes": [{"address": address, "mode": "managed", "change": {"actions": actions}}]}, False)

    def test_bootstrap_cannot_also_apply_machine_configuration(self):
        with self.assertRaises(talos.SafetyError):
            talos.inspect_plan({"resource_changes": [{"address": "talos_machine_configuration_apply.node", "mode": "managed", "change": {"actions": ["update"]}}]}, True)

    def test_apply_without_exact_serial_never_calls_live_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            data = {**talos.settings(), **self.data}
            calls = []
            with patch.dict(os.environ, {"HOMELAB_STATE_DIR": directory, "HOMELAB_STATE_PASSPHRASE": "synthetic-test-passphrase"}, clear=True), \
                 patch.object(talos, "settings", return_value=data), \
                 patch.object(talos, "executable", return_value="tofu"), \
                 patch.object(talos, "run", side_effect=lambda command, **kwargs: calls.append(command) or ""), \
                 patch.object(talos, "preflight") as preflight, \
                 patch("sys.argv", ["talos", "apply", "--confirm-disk", "WRONG"]):
                with self.assertRaises(talos.SafetyError):
                    talos.main()
            self.assertFalse(any("apply" in command for command in calls))
            preflight.assert_not_called()

    def test_secret_runtime_cannot_be_inside_git(self):
        with self.assertRaises(talos.SafetyError):
            talos.private_directory(ROOT / "private-state")

    def test_missing_key_and_debug_logging_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(talos.SafetyError):
            talos.tofu_environment(Path("/tmp/example"))
        with patch.dict(os.environ, {"HOMELAB_STATE_PASSPHRASE": "synthetic-test-passphrase", "TF_LOG": "TRACE"}, clear=True), self.assertRaises(talos.SafetyError):
            talos.tofu_environment(Path("/tmp/example"))

    def test_private_output_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.write_text("preserve")
            link = Path(directory) / "talosconfig"
            link.symlink_to(target)
            with self.assertRaises(talos.SafetyError):
                talos.write_private(link, "synthetic credential")
            self.assertEqual(target.read_text(), "preserve")

    def test_talos_json_stream_preserves_multiple_disk_records(self):
        records = self.inventory(self.internal, self.usb)
        self.assertEqual(talos.json_stream("\n".join(json.dumps(record) for record in records)), records)


if __name__ == "__main__":
    unittest.main()
