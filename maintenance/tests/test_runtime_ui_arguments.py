import subprocess
import unittest

from x86qw_runtime.ui import arguments


class PublicCommandRenderingTests(unittest.TestCase):
    def test_posix_public_launcher_name_uses_the_shell_launcher(self):
        self.assertEqual(
            "./x86qw.sh", arguments.public_launcher_name(os_name="posix"),
        )

    def test_windows_public_launcher_name_uses_the_batch_launcher(self):
        self.assertEqual(
            "x86qw.cmd", arguments.public_launcher_name(os_name="nt"),
        )

    def test_posix_public_bootstrap_selects_the_unix_command(self):
        self.assertEqual(
            "unix-bootstrap",
            arguments.public_bootstrap_command(
                "unix-bootstrap", "powershell-bootstrap", os_name="posix",
            ),
        )

    def test_windows_public_bootstrap_selects_the_powershell_command(self):
        self.assertEqual(
            "powershell-bootstrap",
            arguments.public_bootstrap_command(
                "unix-bootstrap", "powershell-bootstrap", os_name="nt",
            ),
        )

    def test_posix_command_uses_the_shell_launcher_and_safe_quoting(self):
        rendered = arguments.render_public_command(
            ["play", "ktx", "--map", "map with spaces"],
            os_name="posix",
        )

        self.assertEqual(
            "./x86qw.sh play ktx --map 'map with spaces'",
            rendered,
        )

    def test_windows_command_uses_the_batch_launcher_and_native_quoting(self):
        command = ["x86qw.cmd", "play", "ktx", "--map", "map with spaces"]

        self.assertEqual(
            subprocess.list2cmdline(command),
            arguments.render_public_command(command[1:], os_name="nt"),
        )


if __name__ == "__main__":
    unittest.main()
