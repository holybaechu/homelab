"""Stage the optional official plugin without modifying Hermes or its venv."""

import contextlib
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
import zipfile


REVISION = "83f0a896b3518c7131181838e340c9a80c79524d"
SOURCE = f"https://raw.githubusercontent.com/NousResearch/hermes-official-plugins/{REVISION}/langfuse"
PLUGIN_FILES = {
    "__init__.py": "32354bf471bf0f569b26e300cc407961679af6f446e8ab17850bdf2e5f8b165e",
    "plugin.yaml": "f3484e8baad8e637056f11560a5d99332b144e286199d4edc4b65584810a0068",
    "LICENSE": "5f79d321c17de8ef6e5f34967dc5b484afebeeefd87180890d9741941f6b815f",
}
# The pinned Hermes image supplies the other SDK dependencies. Pure-Python
# wheels keep these three additions independent of the image's Python ABI.
WHEELS = (
    (
        "https://files.pythonhosted.org/packages/02/de/e59da18cb5ca9cb8515a254199bd96ace8cf918788d13ded66aa2693e007/langfuse-4.15.2-py3-none-any.whl",
        "98c27a3c06e18c4497045f2d4decce2c716ef11cb215cf5b27bbea6ee0877115",
    ),
    (
        "https://files.pythonhosted.org/packages/df/73/b6e24bd22e6720ca8ee9a85a0c4a2971af8497d8f3193fa05390cbd46e09/backoff-2.2.1-py3-none-any.whl",
        "63579f9a0628e06278f7e47b7d7d5b6ce20dc65c5e96a6f3ca99a6adca0396e8",
    ),
    (
        "https://files.pythonhosted.org/packages/77/8b/1ea57b936b70f98e8e7620a352578096d778dbee95b262f73c066d210ffb/wrapt-2.5.2-py3-none-any.whl",
        "61eb54e9540ea522c2c4752a57549a83d3c67e6704ed708403d28e067aaf90f0",
    ),
)


def download(url, expected_hash):
    with urllib.request.urlopen(url, timeout=10) as response:
        content = response.read(16 * 1024 * 1024 + 1)
    if len(content) > 16 * 1024 * 1024:
        raise ValueError("download too large")
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise ValueError("checksum mismatch")
    return content


def prepare(runtime):
    """Expose a complete plugin, or leave its manifest absent and return False."""
    runtime = Path(runtime)
    plugin = runtime / "plugin"
    python = runtime / "python"
    manifest = plugin / "plugin.yaml"
    try:
        plugin.mkdir(parents=True, exist_ok=True)
        python.mkdir(parents=True, exist_ok=True)
        manifest.unlink(missing_ok=True)
        for url, checksum in WHEELS:
            with zipfile.ZipFile(io.BytesIO(download(url, checksum))) as wheel:
                for name in wheel.namelist():
                    destination = (python / name).resolve()
                    if not destination.is_relative_to(python.resolve()):
                        raise ValueError("wheel path escapes runtime")
                wheel.extractall(python)
        for name, checksum in PLUGIN_FILES.items():
            if name != "plugin.yaml":
                (plugin / name).write_bytes(download(f"{SOURCE}/{name}", checksum))
        # Validate against the image's installed dependency versions before the
        # plugin becomes discoverable. Importing the SDK does not create a client.
        # Match native startup: the mounted site path file must expose the SDK.
        # Injecting the target here would hide an incorrect Python site mount.
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        subprocess.run(
            [sys.executable, "-c", "from importlib.metadata import distribution, version; "
             "from packaging.requirements import Requirement; "
             "requirements = [Requirement(s) for s in distribution('langfuse').requires]; "
             "assert all(r.specifier.contains(version(r.name)) for r in requirements "
             "if r.marker is None or r.marker.evaluate()); "
             "from langfuse import Langfuse, propagate_attributes"],
            env=environment, check=True, timeout=20,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        manifest.write_bytes(download(f"{SOURCE}/plugin.yaml", PLUGIN_FILES["plugin.yaml"]))
        print("Langfuse plugin prepared.")
        return True
    except Exception as error:
        with contextlib.suppress(OSError):
            manifest.unlink(missing_ok=True)
        # Never print credentials, HTTP bodies or request headers on failure.
        print(f"Langfuse tracing unavailable ({type(error).__name__}); Hermes can continue.", file=sys.stderr)
        return False


def merge_configuration(config):
    """Own only this plugin's selection and sender-attribution preference."""
    plugins = config.setdefault("plugins", {})
    enabled = plugins.setdefault("enabled", [])
    if "langfuse" not in enabled:
        enabled.append("langfuse")
    if "disabled" in plugins:
        plugins["disabled"] = [name for name in plugins["disabled"] if name != "langfuse"]
    settings = plugins.setdefault("entries", {}).setdefault("langfuse", {}).setdefault("settings", {})
    settings["end_user_attribution"] = False


def configure():
    # Dependencies were verified above; a generic `plugins enable` invokes PM's
    # environment transaction, which is unnecessary for this mounted runtime.
    from hermes_cli.config import load_config, save_config

    config = load_config()
    merge_configuration(config)
    save_config(config)


def main(runtime):
    if not prepare(runtime):
        return
    try:
        configure()
    except Exception as error:
        with contextlib.suppress(OSError):
            (Path(runtime) / "plugin/plugin.yaml").unlink(missing_ok=True)
        print(f"Langfuse configuration unavailable ({type(error).__name__}); Hermes can continue.", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1])
