"""Exercise refresh recovery and child termination through the native CLI seam."""

import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest


@unittest.skipUnless(os.name == "posix", "the cluster runs a POSIX shell")
class RefreshTests(unittest.TestCase):
    def test_failed_update_retries_then_refreshes_and_stops_sleep(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = root / "bin"
            commands.mkdir()
            node = commands / "node"
            node.write_text(
                "#!/bin/sh\n"
                'case "$*" in\n'
                '  *update) if [ ! -e "$FIXTURE/attempted" ]; then\n'
                '    touch "$FIXTURE/attempted"; echo failed-update >> "$FIXTURE/events"; exit 1\n'
                '    fi; echo updated >> "$FIXTURE/events" ;;\n'
                '  *embed) echo embedded >> "$FIXTURE/events" ;;\n'
                "esac\n"
            )
            sleep = commands / "sleep"
            sleep.write_text(
                "#!/bin/sh\n"
                'if [ ! -e "$FIXTURE/paused" ]; then touch "$FIXTURE/paused"; exit 0; fi\n'
                'echo $$ > "$FIXTURE/sleeper"\n'
                "exec /bin/sleep 60\n"
            )
            node.chmod(0o755)
            sleep.chmod(0o755)
            process = subprocess.Popen(
                ["/bin/sh", str(Path(__file__).with_name("refresh.sh"))],
                env={
                    **os.environ,
                    "PATH": f"{commands}:{os.environ['PATH']}",
                    "FIXTURE": str(root),
                    "XDG_CACHE_HOME": str(root / "cache"),
                },
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            sleeper = None
            try:
                deadline = time.monotonic() + 5
                while not (root / "sleeper").exists():
                    if process.poll() is not None or time.monotonic() > deadline:
                        self.fail("refresh did not recover and reach the next interval")
                    time.sleep(0.02)
                sleeper = int((root / "sleeper").read_text())
                self.assertEqual(
                    (root / "events").read_text().splitlines(),
                    ["failed-update", "updated", "embedded"],
                )
                self.assertTrue((root / "cache/qmd/last-refresh").read_text().strip())
                process.send_signal(signal.SIGTERM)
                _, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertIn("QMD refresh failed", stderr)
                with self.assertRaises(ProcessLookupError):
                    os.kill(sleeper, 0)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=5)
                if sleeper is not None:
                    try:
                        os.kill(sleeper, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


if __name__ == "__main__":
    unittest.main()
