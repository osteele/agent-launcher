"""Continuation identity, transcript fidelity, and argument boundary checks."""

from __future__ import annotations

import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path

from agent_continuation import (
    ContinuationError,
    Source,
    binding_environment,
    content_text,
    digest,
    normalize_export,
    parse_request,
    validate_bundle,
    validate_lineage,
    validate_claude_arguments,
)

REPO = Path(__file__).resolve().parent.parent
MODEL = runpy.run_path(str(REPO / "launchers" / "agent-model"))
SESSION = "9d1df57a-17b7-402a-a994-393869908ddd"


class ContinuationArgumentsTest(unittest.TestCase):
    def parse(self, *args: str, harness: str = "codex"):
        return parse_request(harness, list(args), MODEL["argument_takes_value"])

    def test_option_values_and_literal_separator_do_not_select_a_source(self):
        request = self.parse(
            "--profile",
            "--from",
            "--from",
            "Fortunate Blanket",
            "--",
            "--resume",
            "literal",
        )
        self.assertEqual(request.source, "Fortunate Blanket")
        self.assertEqual(request.arguments, ("--profile", "--from"))
        self.assertEqual(request.prompt, "--resume\nliteral")

    def test_optional_resume_flags_do_not_hide_a_continuation(self):
        for harness in ("claude", "omp"):
            for flag in ("-c", "-r", "--resume"):
                with self.subTest(harness=harness, flag=flag):
                    args = [flag, "--from", SESSION]
                    self.assertTrue(MODEL["has_continuation_option"](harness, args))
                    with self.assertRaises(ContinuationError):
                        self.parse(*args, harness=harness)

    def test_configured_arguments_cannot_replace_a_bound_claude_conversation(self):
        prepared = ["--session-id", SESSION, "--", "historical context"]
        validate_claude_arguments(
            SESSION,
            ["--system-prompt", "--resume", *prepared],
            MODEL["argument_takes_value"],
        )
        for configured in (
            ["--resume=another-session"],
            ["--continue"],
            ["--session-id", "another-session"],
            ["--", "hidden native options"],
            ["--bare"],
            ["--safe-mode"],
        ):
            with (
                self.subTest(configured=configured),
                self.assertRaises(ContinuationError),
            ):
                validate_claude_arguments(
                    SESSION,
                    [*configured, *prepared],
                    MODEL["argument_takes_value"],
                )

    def test_identity_changing_modes_are_refused(self):
        for option in (
            "--resume",
            "--session-id",
            "--remote",
            "--worktree",
            "--no-session",
        ):
            with self.subTest(option=option), self.assertRaises(ContinuationError):
                self.parse("--from", SESSION, option)
        for command in ("resume", "fork", "exec", "mcp"):
            with self.subTest(command=command), self.assertRaises(ContinuationError):
                self.parse("--from", SESSION, command)

    def test_duplicate_or_empty_sources_are_refused(self):
        for args in (("--from",), ("--from=",), ("--from", "a", "--from", "b")):
            with self.subTest(args=args), self.assertRaises(ContinuationError):
                self.parse(*args)

    def test_project_override_resolves_before_switching_working_directory(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            request = self.parse("--from", SESSION, "--cd", str(path), "continue")
            self.assertEqual(request.project, path.resolve())
            self.assertEqual(request.arguments, ())
            self.assertEqual(request.prompt, "continue")


class BindingValidationTest(unittest.TestCase):
    def test_partial_foreign_or_stale_binding_never_authorizes_launch(self):
        environment = {
            "AGENT_LOOM_ID": SESSION,
            "AGENT_LOOM_RUN_ID": "new-run",
            "AGENT_LOOM_GENERATION": "3",
            "AGENT_LOOM_BINDING": "single-use-proof",
            "AGENT_LOOM_HOST_PID": str(os.getpid()),
            "AGENT_LOOM_HOST_START": "process-start",
        }
        binding_environment({"environment": environment}, SESSION, 2)
        invalid = [
            {
                key: value
                for key, value in environment.items()
                if key != "AGENT_LOOM_BINDING"
            },
            {**environment, "AGENT_LOOM_ID": "unrelated-session"},
            {**environment, "AGENT_LOOM_HOST_PID": str(os.getpid() + 1)},
            {**environment, "AGENT_LOOM_GENERATION": "2"},
            {**environment, "AGENT_LOOM_GENERATION": "not-a-generation"},
            {**environment, "PATH": "/untrusted"},
        ]
        for binding in invalid:
            with self.subTest(binding=binding), self.assertRaises(ContinuationError):
                binding_environment({"environment": binding}, SESSION, 2)


class TranscriptNormalizationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.bundle = Path(self.temporary.name)
        self.source = Source(SESSION, SESSION, "claude", "Fortunate Blanket")

    def normalize(self, rows, source=None):
        (self.bundle / "source.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        return normalize_export(source or self.source, self.bundle)

    def claude(self, text, **extra):
        return {
            "type": "user",
            "sessionId": SESSION,
            "cwd": str(self.bundle),
            "message": {"role": "user", "content": text},
            **extra,
        }

    def test_wrong_native_identity_never_becomes_context(self):
        with self.assertRaisesRegex(ContinuationError, "expected"):
            self.normalize([self.claude("another task", sessionId="wrong-session")])

    def test_unverified_export_and_empty_conversation_are_refused(self):
        with self.assertRaisesRegex(ContinuationError, "positively identify"):
            self.normalize(
                [{"type": "user", "message": {"role": "user", "content": "task"}}]
            )
        (self.bundle / "history.jsonl").unlink()
        with self.assertRaisesRegex(ContinuationError, "no recognized"):
            self.normalize([{"type": "mode", "sessionId": SESSION}])

    def test_bounded_preview_keeps_full_history_and_source_provenance(self):
        rows = [
            self.claude(f"task {i}", uuid=f"m{i}", parentUuid=f"m{i - 1}")
            for i in range(20)
        ]
        rows.append(self.claude("subagent-only", isSidechain=True))
        project, coverage, recent = self.normalize(rows)
        history = [
            json.loads(line)
            for line in (self.bundle / "history.jsonl").read_text().splitlines()
        ]
        self.assertEqual(project, self.bundle)
        self.assertEqual(coverage["message_count"], 21)
        self.assertEqual(
            [row["text"] for row in recent], [f"task {i}" for i in range(4, 20)]
        )
        self.assertEqual(history[0]["text"], "task 0")
        self.assertEqual(history[-1]["source_line"], 21)
        self.assertTrue(history[-1]["sidechain"])
        self.assertEqual(history[10]["parent_id"], "m9")
        if os.name != "nt":
            self.assertEqual(
                (self.bundle / "history.jsonl").stat().st_mode & 0o777, 0o600
            )

    def test_tool_calls_are_historical_data_and_reasoning_is_not_promoted(self):
        content = [
            {"type": "thinking", "thinking": "private reasoning"},
            {"type": "text", "text": "verified result"},
            {
                "type": "tool_use",
                "id": "call1",
                "name": "Bash",
                "input": {"command": "exit 42"},
            },
        ]
        self.normalize([self.claude(content)])
        record = json.loads((self.bundle / "history.jsonl").read_text())
        self.assertNotIn("private reasoning", record["text"])
        self.assertIn("verified result", record["text"])
        self.assertIn('"command": "exit 42"', record["text"])
        self.assertIn("Historical content block", record["text"])
        self.assertEqual(
            json.loads((self.bundle / "source.jsonl").read_text())["message"][
                "content"
            ],
            content,
        )

    def test_codex_native_metadata_and_tool_output_are_preserved(self):
        source = Source("durable-loom", SESSION, "codex", "Fortunate Blanket")
        _, _, recent = self.normalize(
            [
                {
                    "type": "session_meta",
                    "payload": {"id": SESSION, "cwd": str(self.bundle)},
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "fix crash"}],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "c1",
                        "output": "exit 137",
                    },
                },
            ],
            source,
        )
        self.assertEqual(recent[0]["text"], "fix crash")
        self.assertEqual(recent[1]["role"], "tool")
        self.assertIn("exit 137", recent[1]["text"])

    def test_malformed_content_is_not_silently_skipped(self):
        for content in (None, [{"type": "text"}], ["invalid block"]):
            with self.subTest(content=content), self.assertRaises(ContinuationError):
                content_text(content)

    def write_manifest(self, *, destination="codex", parent=None):
        names = ["source.jsonl", "history.jsonl", "context.md"]
        if destination == "omp":
            names.append("fresh-session.json")
        for name in names:
            (self.bundle / name).write_text("original evidence", encoding="utf-8")
        manifest = {
            "schema": "agent-continuation-bundle/v1",
            "loom_id": SESSION,
            "destination_harness": destination,
            "parent_bundle": parent,
            "files": {name: digest(self.bundle / name) for name in names},
        }
        path = self.bundle / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_omp_fresh_session_configuration_is_covered_by_integrity_checks(self):
        self.write_manifest(destination="omp")
        validate_bundle(self.bundle, SESSION, "omp")
        (self.bundle / "fresh-session.json").write_text(
            '{"autoResume": true}', encoding="utf-8"
        )
        with self.assertRaisesRegex(
            ContinuationError, "integrity check failed: fresh-session.json"
        ):
            validate_bundle(self.bundle, SESSION, "omp")

    def test_missing_and_cyclic_lineage_cannot_silently_drop_predecessor_context(self):
        path = self.write_manifest()
        validate_lineage(str(path), SESSION)
        self.write_manifest(parent=str(path))
        with self.assertRaisesRegex(ContinuationError, "lineage contains a cycle"):
            validate_lineage(str(path), SESSION)
        self.write_manifest(parent=str(self.bundle / "missing" / "manifest.json"))
        with self.assertRaises(FileNotFoundError):
            validate_lineage(str(path), SESSION)

    def test_mutated_context_and_wrong_owner_cannot_pass_bundle_validation(self):
        self.write_manifest()
        self.assertEqual(
            validate_bundle(self.bundle, SESSION, "codex")["loom_id"], SESSION
        )
        with self.assertRaisesRegex(ContinuationError, "identity or destination"):
            validate_bundle(self.bundle, "another-session", "codex")
        (self.bundle / "context.md").write_text(
            "substituted instructions", encoding="utf-8"
        )
        with self.assertRaisesRegex(ContinuationError, "integrity check failed"):
            validate_bundle(self.bundle, SESSION, "codex")


if __name__ == "__main__":
    unittest.main()
