"""`--recover`: resuming a conversation whose host died without ending its run.

These drive `agent-model` as the Claude wrapper does, against a stand-in Loom
and a launcher that records the argv it was handed, so they test the selection
and the hand-off rather than Claude itself.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.fake_agent_loom import install as install_fake_agent_loom

REPO = Path(__file__).resolve().parent.parent
AGENT_MODEL = REPO / "launchers" / "agent-model"
DEAD = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
NEWER = "33333333-3333-4333-8333-333333333333"

requires_posix = unittest.skipIf(os.name == "nt", "the recorded launcher is a POSIX script")


@requires_posix
class RecoverTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.home = self.tmp / "home"
        self.project = self.tmp / "project"
        self.project.mkdir()
        self.transcripts = self.home / ".claude" / "projects" / "-project"
        self.transcripts.mkdir(parents=True)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        install_fake_agent_loom(bin_dir)
        self.launcher = self.tmp / "claude"
        self.launcher.write_text('#!/bin/sh\nprintf "launched:%s\\n" "$@"\n')
        self.launcher.chmod(0o755)
        self.table_path = self.tmp / "loom.json"
        self.log = self.tmp / "loom.log"
        self.table: dict[str, object] = {"unended": [], "log": str(self.log)}
        self.environment = dict(os.environ)
        self.environment.update({
            "HOME": str(self.home),
            # agent-model runs under the first python3 on PATH; keep it this one.
            "PATH": f"{bin_dir}:{Path(sys.executable).parent}:/usr/bin:/bin",
            "FAKE_AGENT_LOOM_TABLE": str(self.table_path),
        })

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def unended(self, native_id: str, name: str | None, *, transcript: bool = True,
                title: str = "Work in progress") -> None:
        run = {
            "loomId": native_id, "harness": "claude", "nativeId": native_id,
            "generation": 1, "runId": "run-" + native_id[:8], "project": str(self.project),
            "startedAt": "2026-10-07T01:00:00.000Z", "host": {"pid": 1, "procStart": "x"},
        }
        if name is not None:
            run["name"] = name
        self.table["unended"].append(run)  # type: ignore[union-attr]
        if transcript:
            self.transcript(native_id, title)

    def transcript(self, native_id: str, title: str = "Work in progress") -> Path:
        path = self.transcripts / f"{native_id}.jsonl"
        path.write_text(
            json.dumps({"type": "user", "timestamp": "2026-10-07T01:05:00.000Z"}) + "\n"
            + json.dumps({"type": "ai-title", "aiTitle": title, "sessionId": native_id}) + "\n"
        )
        return path

    def agent_model(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        self.table_path.write_text(json.dumps(self.table))
        return subprocess.run(
            [str(AGENT_MODEL), *arguments], capture_output=True, check=False,
            cwd=self.project, env=self.environment, stdin=subprocess.DEVNULL,
            text=True, timeout=30,
        )

    def recover(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return self.agent_model(
            "recover", "claude", "--launcher", str(self.launcher),
            "--native-binary", "/usr/bin/true", "--", *argv,
        )

    def launched(self, result: subprocess.CompletedProcess[str]) -> list[str]:
        return [line.removeprefix("launched:") for line in result.stdout.splitlines()
                if line.startswith("launched:")]

    def test_resumes_the_one_unended_session_by_its_native_id(self) -> None:
        self.unended(DEAD, "Swift Banjo")
        result = self.recover("--model", "--recover", "--recover", "--verbose")
        self.assertEqual(result.returncode, 0, result.stderr)
        # --recover is removed; an option's value that spells it is kept.
        self.assertEqual(self.launched(result), ["--resume", DEAD, "--model", "--recover", "--verbose"])
        self.assertIn("recovering Swift Banjo", result.stderr)
        self.assertIn(["continuation", "unended", "--project", str(self.project),
                       "--harness", "claude", "--json"],
                      [json.loads(line) for line in self.log.read_text().splitlines()])

    def test_a_forwarded_directory_selects_the_project(self) -> None:
        # Invoked elsewhere, --cd names the project whose sessions are asked for.
        self.unended(DEAD, "Swift Banjo")
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        self.table_path.write_text(json.dumps(self.table))
        result = subprocess.run(
            [str(AGENT_MODEL), "recover", "claude", "--launcher", str(self.launcher),
             "--native-binary", "/usr/bin/true", "--", "--recover", "--cd", str(self.project)],
            capture_output=True, check=False, cwd=elsewhere, env=self.environment,
            stdin=subprocess.DEVNULL, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launched(result), ["--resume", DEAD, "--cd", str(self.project)])

    def test_model_first_claude_routes_recovery_to_claude(self) -> None:
        # The shipped default sends `claude` to another harness; recovery cannot.
        self.environment["XDG_CONFIG_HOME"] = str(self.tmp / "config")
        result = self.agent_model("resolve", "claude", "--recover=Swift Banjo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split()[0], "claude")
        self.assertIn("--recover=", result.stdout)
        named = self.agent_model("resolve", "claude", "--harness", "omp", "--recover")
        self.assertEqual(named.returncode, 0, named.stderr)
        self.assertEqual(named.stdout.split()[0], "omp")
        # glm's own harness is opencode, which Loom does not track.
        refused = self.agent_model("resolve", "glm", "--recover")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("cannot recover", refused.stderr)

    def test_codex_and_omp_resume_in_their_own_spelling(self) -> None:
        sessions = {
            "omp": (self.home / ".omp" / "agent" / "sessions" / "-project", f"2026-10-07_{DEAD}.jsonl",
                    ["--resume", DEAD]),
            "codex": (self.home / ".codex" / "sessions" / "2026" / "10" / "07",
                      f"rollout-2026-10-07T01-00-00-{DEAD}.jsonl", ["resume", DEAD]),
        }
        for harness, (directory, filename, expected) in sessions.items():
            with self.subTest(harness=harness):
                directory.mkdir(parents=True, exist_ok=True)
                (directory / filename).write_text("{}\n")
                self.table["unended"] = []
                self.unended(DEAD, "Swift Banjo", transcript=False)
                self.table["unended"][0]["harness"] = harness  # type: ignore[index]
                self.table_path.write_text(json.dumps(self.table))
                result = self.agent_model(
                    "recover", harness, "--launcher", str(self.launcher),
                    "--native-binary", "/usr/bin/true", "--", "--recover",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.launched(result), expected)

    def test_none_unended_refuses_rather_than_continuing(self) -> None:
        result = self.recover("--recover")
        self.assertEqual(result.returncode, 2)
        self.assertIn("ended without exiting", result.stderr)
        self.assertEqual(self.launched(result), [])

    def test_a_run_without_a_transcript_is_named_and_skipped(self) -> None:
        self.unended(DEAD, "Swift Banjo", transcript=False)
        result = self.recover("--recover")
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"skipping unended {DEAD}: no transcript", result.stderr)
        self.assertEqual(self.launched(result), [])

    def test_several_unattended_require_a_selector(self) -> None:
        self.unended(DEAD, "Swift Banjo", title="Banjo work")
        self.unended(OTHER, "Quiet Heron", title="Heron work")
        result = self.recover("--recover")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--recover=<name-or-id>", result.stderr)
        self.assertIn("'Heron work'", result.stderr)
        self.assertEqual(self.launched(result), [])
        for selector in ("quiet heron", OTHER, OTHER[:8]):
            with self.subTest(selector=selector):
                result = self.recover(f"--recover={selector}")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.launched(result), ["--resume", OTHER])

    def test_selector_matching_nothing_refuses(self) -> None:
        self.unended(DEAD, "Swift Banjo")
        result = self.recover("--recover=Quiet Heron")
        self.assertEqual(result.returncode, 2)
        self.assertIn("matches 'Quiet Heron'", result.stderr)

    def test_dry_run_lists_without_launching(self) -> None:
        self.unended(DEAD, "Swift Banjo", title="Banjo work")
        result = self.recover("--recover", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Swift Banjo · 'Banjo work'", result.stdout)
        self.assertEqual(self.launched(result), [])

    def test_refuses_other_conversation_selectors(self) -> None:
        self.unended(DEAD, "Swift Banjo")
        for option in ("--continue", "-c", "--resume", "--from=x", "--fork-session", "--session-id"):
            with self.subTest(option=option):
                result = self.recover("--recover", option)
                self.assertEqual(result.returncode, 2)
                self.assertIn("cannot be combined", result.stderr)
                self.assertEqual(self.launched(result), [])

    def test_an_unreliable_process_scan_refuses(self) -> None:
        self.unended(DEAD, "Swift Banjo")
        self.table["unendedError"] = "scan_unavailable"
        result = self.recover("--recover")
        self.assertEqual(result.returncode, 2)
        self.assertIn("process scan unavailable", result.stderr)
        self.assertEqual(self.launched(result), [])

    def test_has_recover_ignores_a_native_option_value(self) -> None:
        self.assertEqual(self.agent_model("has-recover", "claude", "--", "--recover").returncode, 0)
        self.assertEqual(self.agent_model("has-recover", "claude", "--", "--recover=X").returncode, 0)
        self.assertEqual(
            self.agent_model("has-recover", "claude", "--", "--model", "--recover").returncode, 3
        )
        self.assertEqual(self.agent_model("has-recover", "claude", "--", "--", "--recover").returncode, 3)

    def test_continue_notice_names_unended_sessions_it_passes_over(self) -> None:
        self.unended(DEAD, "Swift Banjo")
        newer = self.transcript(NEWER)
        now = time.time()
        os.utime(self.transcripts / f"{DEAD}.jsonl", (now - 60, now - 60))
        os.utime(newer, (now, now))
        result = self.agent_model("notice-unended", "claude", "--", "--continue")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--continue reopens the newest conversation", result.stderr)
        self.assertIn("Swift Banjo", result.stderr)

    def test_continue_notice_is_silent_when_continue_reopens_the_unended_session(self) -> None:
        self.unended(DEAD, "Swift Banjo")
        result = self.agent_model("notice-unended", "claude", "--", "-c")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

    def test_notice_does_not_query_loom_without_continue(self) -> None:
        self.unended(DEAD, "Swift Banjo")
        result = self.agent_model("notice-unended", "claude", "--", "--model", "--continue")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertFalse(self.log.exists())

    def test_continue_notice_reports_a_failed_lookup(self) -> None:
        self.table["unendedError"] = "scan_unavailable"
        result = self.agent_model("notice-unended", "claude", "--", "--continue")
        self.assertEqual(result.returncode, 0)
        self.assertIn("could not check for unended sessions", result.stderr)


if __name__ == "__main__":
    unittest.main()
