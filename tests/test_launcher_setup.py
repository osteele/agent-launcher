"""Tests for launchers/setup: install, uninstall, and the generated env file."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHER_DIR = REPO / "launchers"
SETUP = LAUNCHER_DIR / "setup"
# Stand-ins for the agent-command-guards shadows, which live in their own
# repository: the launcher only checks that git is executable and puts the
# directory on PATH.
SHADOWS = REPO / "tests" / "fixtures" / "guards"
AGENTS = ("kimi", "opencode", "codex", "omp", "agy")
RC_FILES = (".zshenv", ".zshrc", ".bashrc")
BLOCK_START = "# >>> agent-launchers initialize >>>"
BLOCK_END = "# <<< agent-launchers initialize <<<"
BLOCK_SOLUTION = f"Agent launchers are not configured; run {LAUNCHER_DIR}/setup"

requires_posix = unittest.skipIf(
    os.name == "nt", "setup and the generated env file are POSIX shell"
)


@requires_posix
class LauncherSetupTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin_dir = self.tmp / "bin"
        self.environment = dict(os.environ)
        self.environment["HOME"] = str(self.home)
        self.environment["AGENT_LAUNCHER_BIN_DIR"] = str(self.bin_dir)
        self.environment["PATH"] = "/usr/bin:/bin"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_setup(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SETUP), *args],
            capture_output=True,
            check=False,
            env=self.environment,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )

    def seed_rc_files(self) -> None:
        # Sentinels prove uninstall and dry-run preserve unrelated content.
        for name in RC_FILES:
            (self.home / name).write_text(f"export SENTINEL_{name[1:-1].upper()}=1\n")

    def sentinel(self, name: str) -> str:
        return f"export SENTINEL_{name[1:-1].upper()}=1"

    def test_dry_run_touches_nothing(self) -> None:
        self.seed_rc_files()
        result = self.run_setup("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in RC_FILES:
            self.assertEqual((self.home / name).read_text(), f"{self.sentinel(name)}\n")
        self.assertFalse(self.bin_dir.exists())
        self.assertFalse((self.home / ".config").exists())

    def test_install_links_binaries_and_configures_rc_files(self) -> None:
        self.seed_rc_files()
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        for agent in AGENTS:
            link = self.bin_dir / agent
            self.assertTrue(link.is_symlink(), link)
            self.assertEqual(os.path.realpath(link), str(REPO / "agent-launcher"))
        env_file = self.home / ".config" / "agent-launchers" / "env"
        self.assertTrue(env_file.is_file())
        self.assertIn(f'export PATH="{LAUNCHER_DIR}:$PATH"', env_file.read_text())
        command = subprocess.run(
            [
                "/bin/sh",
                "-c",
                f'. "{env_file}"; command -v agent-model',
            ],
            capture_output=True,
            check=False,
            env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"},
            text=True,
        )
        self.assertEqual(command.returncode, 0, command.stderr)
        self.assertEqual(command.stdout.strip(), str(LAUNCHER_DIR / "agent-model"))
        for name in RC_FILES:
            content = (self.home / name).read_text()
            self.assertEqual(content.count(BLOCK_START), 1, name)
            self.assertEqual(content.count(BLOCK_END), 1, name)
            self.assertIn(self.sentinel(name), content)
            self.assertIn('. "$HOME/.config/agent-launchers/env"', content)
            # .zshenv is sourced on every shell invocation, including
            # non-interactive ones, and must stay silent; the warning belongs
            # only in the files a person is present for.
            if name == ".zshenv":
                self.assertNotIn(BLOCK_SOLUTION, content)
            else:
                self.assertIn(BLOCK_SOLUTION, content)

    def test_install_leaves_missing_rc_files_uncreated(self) -> None:
        (self.home / ".zshenv").write_text(f"{self.sentinel('.zshenv')}\n")

        result = self.run_setup()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.home / ".zshenv").read_text().count(BLOCK_START), 1)
        self.assertFalse((self.home / ".zshrc").exists())
        self.assertFalse((self.home / ".bashrc").exists())

    def test_install_is_idempotent(self) -> None:
        self.seed_rc_files()
        self.assertEqual(self.run_setup().returncode, 0)
        second = self.run_setup()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout.count("Already configured"), 3)
        self.assertEqual(second.stdout.count("Already linked"), len(AGENTS))

    def test_agy_is_reached_by_a_shell_function(self) -> None:
        # agy is the one managed agent PATH cannot intercept: its real binary
        # sits in ~/.local/bin, ahead of the launcher directory in this
        # machine's login PATH, so the symlink every other agent relies on
        # never wins the lookup. The generated env file carries the function
        # that closes that gap; without it agy runs unguarded.
        self.seed_rc_files()
        self.assertEqual(self.run_setup().returncode, 0)
        env = (self.home / ".config" / "agent-launchers" / "env").read_text()
        self.assertIn("agy()", env)
        self.assertIn("launchers/agy", env)
        for name in RC_FILES:
            self.assertEqual((self.home / name).read_text().count(BLOCK_START), 1)
            self.assertEqual((self.home / name).read_text().count(BLOCK_END), 1)

    def test_env_file_installs_the_exit_receipt_reader(self) -> None:
        # The launcher's card is written just before it execs the agent, and
        # nothing else reads it. Installing the producer without this reader
        # leaves every card unread until it expires a day later.
        self.seed_rc_files()
        self.assertEqual(self.run_setup().returncode, 0)
        env = (self.home / ".config" / "agent-launchers" / "env").read_text()
        self.assertIn("shell/epilogue.zsh", env)
        self.assertIn("ZSH_VERSION", env)

    def test_install_upgrades_an_existing_source_only_block(self) -> None:
        self.seed_rc_files()
        for name in RC_FILES:
            with (self.home / name).open("a") as file:
                file.write(
                    f"\n{BLOCK_START}\n"
                    "# !! Contents within this block are managed by "
                    "agent-launcher/launchers/setup !!\n"
                    '. "$HOME/.config/agent-launchers/env"\n'
                    f"{BLOCK_END}\n"
                )

        result = self.run_setup()

        self.assertEqual(result.returncode, 0, result.stderr)
        for name in RC_FILES:
            content = (self.home / name).read_text()
            self.assertEqual(content.count(BLOCK_START), 1, name)
            self.assertEqual(content.count(BLOCK_END), 1, name)
            if name == ".zshenv":
                self.assertNotIn(BLOCK_SOLUTION, content)
            else:
                self.assertIn(BLOCK_SOLUTION, content)

    def test_install_replaces_links_into_the_previous_repository(self) -> None:
        # The launchers lived in agent-command-guards before they moved here.
        # Its links may still resolve, or may already dangle once that
        # checkout loses its launchers/ directory.
        self.bin_dir.mkdir()
        old_root = self.tmp / "agent-command-guards"
        (old_root / "launchers").mkdir(parents=True)
        (old_root / "agent-launcher").write_text("#!/bin/sh\n")
        (old_root / "launchers" / "kimi").symlink_to(old_root / "agent-launcher")
        (self.bin_dir / "kimi").symlink_to(old_root / "launchers" / "kimi")
        (self.bin_dir / "codex").symlink_to(old_root / "launchers" / "codex")
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        for agent in ("kimi", "codex"):
            link = self.bin_dir / agent
            self.assertEqual(os.path.realpath(link), str(REPO / "agent-launcher"))

    def test_env_file_keeps_a_guards_override_given_to_setup(self) -> None:
        # A guards checkout outside the sibling default is named once, at
        # setup; later shells and the launchers they start must still find it.
        elsewhere = self.tmp / "elsewhere" / "shadows"
        self.environment["AGENT_COMMAND_GUARDS_DIR"] = str(elsewhere)
        self.assertEqual(self.run_setup().returncode, 0)
        env_file = self.home / ".config" / "agent-launchers" / "env"
        for inherited, expected in ((None, elsewhere), ("/inherited", "/inherited")):
            with self.subTest(inherited=inherited):
                environment = {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}
                if inherited is not None:
                    environment["AGENT_COMMAND_GUARDS_DIR"] = inherited
                result = subprocess.run(
                    ["/bin/sh", "-c", f'. "{env_file}"; sh -c \'printf "%s" "$AGENT_COMMAND_GUARDS_DIR"\''],
                    capture_output=True, check=False, env=environment,
                    stdin=subprocess.DEVNULL, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, str(expected))

    def test_env_file_keeps_a_hostile_guards_path_as_data(self) -> None:
        # The path comes from the caller's environment; sourcing the generated
        # file must reproduce it byte for byte and execute none of it.
        marker = self.tmp / "executed"
        hostile = f"{self.tmp}/it's $(touch {marker}) `touch {marker}` $HOME"
        self.environment["AGENT_COMMAND_GUARDS_DIR"] = hostile
        self.assertEqual(self.run_setup().returncode, 0)
        env_file = self.home / ".config" / "agent-launchers" / "env"
        for shell in ("/bin/sh", "/bin/bash", "/bin/zsh"):
            with self.subTest(shell=shell):
                result = subprocess.run(
                    [shell, *(["-f"] if shell.endswith("zsh") else []), "-c",
                     f'. "{env_file}"; printf "%s" "$AGENT_COMMAND_GUARDS_DIR"'],
                    capture_output=True, check=False,
                    env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"},
                    stdin=subprocess.DEVNULL, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, hostile)
                self.assertFalse(marker.exists())

    def test_install_keeps_other_links_into_the_previous_repository(self) -> None:
        # Only the old launcher links migrate; any other link into that
        # repository is someone's own binary and is refused as before.
        self.bin_dir.mkdir()
        own = self.tmp / "agent-command-guards" / "venv" / "bin" / "codex"
        own.parent.mkdir(parents=True)
        own.write_text("#!/bin/sh\n")
        (self.bin_dir / "codex").symlink_to(own)
        result = self.run_setup()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Refusing to replace", result.stderr)
        self.assertEqual(os.readlink(self.bin_dir / "codex"), str(own))

    def test_checkout_path_with_a_quote_keeps_startup_files_parseable(self) -> None:
        # The not-configured hint names the checkout, which may contain a quote.
        checkout = self.tmp / "it's work" / "agent-launcher"
        checkout.mkdir(parents=True)
        for name in ("agent-launcher", "launchers", "shell"):
            source = REPO / name
            if source.is_dir():
                shutil.copytree(source, checkout / name, symlinks=True)
            else:
                shutil.copy2(source, checkout / name)
        self.seed_rc_files()
        setup = checkout / "launchers" / "setup"
        first = subprocess.run([str(setup)], capture_output=True, check=False, env=self.environment,
                               stdin=subprocess.DEVNULL, text=True, timeout=30)
        self.assertEqual(first.returncode, 0, first.stderr)
        for shell, rc in (("/bin/bash", ".bashrc"), ("/bin/zsh", ".zshrc")):
            with self.subTest(rc=rc):
                parsed = subprocess.run([shell, "-n", str(self.home / rc)],
                                        capture_output=True, check=False, text=True, timeout=30)
                self.assertEqual(parsed.returncode, 0, parsed.stderr)
        second = subprocess.run([str(setup)], capture_output=True, check=False, env=self.environment,
                                stdin=subprocess.DEVNULL, text=True, timeout=30)
        self.assertIn(f"Already configured: {self.home / '.zshrc'}", second.stdout)

    def test_install_refuses_to_replace_a_foreign_binary(self) -> None:
        self.bin_dir.mkdir(parents=True)
        foreign = self.bin_dir / "opencode"
        foreign.write_text("#!/bin/sh\nexit 42\n")

        result = self.run_setup()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to replace", result.stderr)
        self.assertEqual(foreign.read_text(), "#!/bin/sh\nexit 42\n")

    def test_uninstall_removes_links_blocks_and_env(self) -> None:
        self.seed_rc_files()
        self.assertEqual(self.run_setup().returncode, 0)

        result = self.run_setup("--uninstall")

        self.assertEqual(result.returncode, 0, result.stderr)
        for agent in AGENTS:
            self.assertFalse((self.bin_dir / agent).exists())
        self.assertFalse((self.home / ".config" / "agent-launchers" / "env").exists())
        for name in RC_FILES:
            content = (self.home / name).read_text()
            self.assertNotIn(BLOCK_START, content)
            self.assertIn(self.sentinel(name), content)

    def test_uninstall_preserves_foreign_binaries(self) -> None:
        self.bin_dir.mkdir(parents=True)
        foreign = self.bin_dir / "kimi"
        foreign.write_text("mine\n")

        result = self.run_setup("--uninstall")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(foreign.read_text(), "mine\n")

    def test_uninstall_without_install_is_a_noop(self) -> None:
        result = self.run_setup("--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.bin_dir.exists())
        self.assertFalse((self.home / ".config").exists())


@requires_posix
class GeneratedEnvTest(unittest.TestCase):
    """The generated env file must restore the guards in sh, Bash, and Zsh."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        home = Path(cls._tmp.name) / "home"
        home.mkdir()
        bin_dir = Path(cls._tmp.name) / "bin"
        environment = dict(os.environ)
        environment["HOME"] = str(home)
        environment["AGENT_LAUNCHER_BIN_DIR"] = str(bin_dir)
        environment["PATH"] = "/usr/bin:/bin"
        subprocess.run(
            [str(SETUP)],
            capture_output=True,
            check=True,
            env=environment,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )
        cls.home = home
        cls.env_file = home / ".config" / "agent-launchers" / "env"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def source_env(
        self, shell: str, path_value: str, guards: str | None
    ) -> subprocess.CompletedProcess[str]:
        environment = {"HOME": str(self.home), "PATH": path_value}
        if guards is not None:
            environment["AGENT_COMMAND_GUARDS_DIR"] = guards
        return subprocess.run(
            [
                shell,
                *(["-f"] if shell.endswith("zsh") else []),
                "-c",
                f'. "{self.env_file}"; printf "%s\\n" "$PATH" '
                "; command -v agent_guards_restore || echo function-gone",
            ],
            capture_output=True,
            check=False,
            env=environment,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )

    def test_moves_the_guards_to_the_front_in_every_shell(self) -> None:
        # An agent re-sourced its shell configuration: version managers sit in
        # front of the guards, which must return to the front, not multiply.
        for shell in ("/bin/sh", "/bin/bash", "/bin/zsh"):
            with self.subTest(shell=shell):
                result = self.source_env(
                    shell, f"/usr/bin:/bin:{SHADOWS}", str(SHADOWS)
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                first_line = result.stdout.splitlines()[0]
                self.assertTrue(
                    first_line.startswith(f"{SHADOWS}:{LAUNCHER_DIR}"),
                    f"{shell}: guards not restored to front: {first_line}",
                )
                self.assertEqual(first_line.count(str(SHADOWS)), 1)
                self.assertIn("function-gone", result.stdout)
                self.assertNotIn("agent_guards_restore\n", result.stdout)

    def test_is_a_noop_without_the_guards_on_path(self) -> None:
        # An ordinary shell has no guards directory on PATH; sourcing the env
        # must not put one there.
        for shell in ("/bin/sh", "/bin/bash", "/bin/zsh"):
            with self.subTest(shell=shell):
                result = self.source_env(shell, "/usr/bin:/bin", None)
                self.assertEqual(result.returncode, 0, result.stderr)
                first_line = result.stdout.splitlines()[0]
                self.assertTrue(first_line.startswith(f"{LAUNCHER_DIR}:"))
                self.assertNotIn(str(SHADOWS), first_line)

    def run_zsh_startup(
        self, guards: str | None, *, trigger: str
    ) -> subprocess.CompletedProcess[str]:
        # Reproduce the rc order: .zshenv sources the env file, .zshrc then
        # activates a version manager whose hook prepends its own directory,
        # and .zshrc sources the env file again last. `trigger` is what the
        # interactive shell does next: show a prompt, or change directory.
        environment = {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}
        if guards is not None:
            environment["AGENT_COMMAND_GUARDS_DIR"] = guards
            environment["PATH"] = f"{guards}:/usr/bin:/bin"
        manager = self.home / "manager-bin"
        manager.mkdir(exist_ok=True)
        fake_omp = manager / "omp"
        fake_omp.write_text("#!/bin/sh\n")
        fake_omp.chmod(0o755)
        script = f"""
. "{self.env_file}"
autoload -Uz add-zsh-hook
_manager_hook() {{ path=("{manager}" ${{path:#{manager}}}); }}
add-zsh-hook precmd _manager_hook
add-zsh-hook chpwd _manager_hook
. "{self.env_file}"
{trigger}
print -r -- "$PATH"
print -r -- "$(whence -p omp)"
print -r -- "${{(j: :)precmd_functions}}"
"""
        return subprocess.run(
            ["/bin/zsh", "-f", "-c", script],
            capture_output=True,
            check=False,
            env=environment,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )

    def test_zsh_keeps_the_launcher_ahead_of_a_prompt_hook_manager(self) -> None:
        # mise activate prepends its tool directories at every prompt and cd;
        # without the hook a mise-installed omp resolves ahead of the launcher.
        triggers = {
            "prompt": "for f in $precmd_functions; do $f; done",
            "cd": "cd / && for f in $chpwd_functions; do $f; done",
        }
        for name, trigger in triggers.items():
            with self.subTest(trigger=name):
                result = self.run_zsh_startup(None, trigger=trigger)
                self.assertEqual(result.returncode, 0, result.stderr)
                path_line, omp, hooks = result.stdout.splitlines()
                self.assertTrue(path_line.startswith(f"{LAUNCHER_DIR}:"), path_line)
                self.assertEqual(path_line.count(str(LAUNCHER_DIR)), 1)
                self.assertEqual(omp, str(LAUNCHER_DIR / "omp"))
                self.assertNotIn(str(SHADOWS), path_line)
                self.assertEqual(hooks.split().count("_agent_path_order"), 1)
                order = hooks.split()
                self.assertGreater(
                    order.index("_agent_path_order"), order.index("_manager_hook")
                )

    def test_zsh_keeps_the_guards_first_in_an_agent_session(self) -> None:
        result = self.run_zsh_startup(
            str(SHADOWS), trigger="for f in $precmd_functions; do $f; done"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        path_line = result.stdout.splitlines()[0]
        self.assertTrue(path_line.startswith(f"{SHADOWS}:{LAUNCHER_DIR}:"), path_line)
        self.assertEqual(path_line.count(str(SHADOWS)), 1)


if __name__ == "__main__":
    unittest.main()
