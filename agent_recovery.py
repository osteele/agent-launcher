"""Resume a conversation whose host process died without ending its run.

Loom records a run as ended when its harness exits normally. A run that is
still its identity's current one, never ended, and whose host process is gone
was crashed or killed -- Claude Code's idle compaction takes the process down
this way. `--continue` cannot find such a conversation reliably: it reopens the
newest transcript in the directory, which is often a later session.

Recovery asks Loom for those runs and resumes one by its native ID through the
launcher, so the ordinary resume fence binds the conversation back to its Loom
identity. Loom lists unended runs for Claude, Codex, and OMP.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agent_continuation import (
    ContinuationError,
    object_value,
    query_json,
    required_binary,
    string_value,
)

# Each harness Loom tracks runs for, with the store that proves a native ID
# still opens and the arguments that resume it.
RECOVER_HARNESSES = {"claude", "codex", "omp"}
TRANSCRIPT_STORES: dict[str, tuple[str, str]] = {
    "claude": (".claude/projects", "*/{native_id}.jsonl"),
    "omp": (".omp/agent/sessions", "*/*_{native_id}.jsonl"),
    "codex": (".codex/sessions", "*/*/*/rollout-*-{native_id}.jsonl"),
}
RESUME_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "claude": ("--resume",),
    "omp": ("--resume",),
    "codex": ("resume",),
}
# Harnesses whose --continue reopens the newest conversation in the directory.
CONTINUE_HARNESSES = {"claude", "omp"}

# Each of these chooses the conversation itself; recovery owns that choice.
RECOVER_CONFLICTS = {
    "--resume", "-r", "--continue", "-c", "--from", "--fork-session", "--session-id",
}

# A notice runs in front of an ordinary launch, so its lookup is bounded tighter
# than recovery's own.
NOTICE_TIMEOUT = 5.0


@dataclass(frozen=True)
class UnendedRun:
    loom_id: str
    name: str | None
    native_id: str
    started_at: str
    transcript: Path
    title: str | None
    last_turn: str | None

    @property
    def label(self) -> str:
        parts = [self.name or self.loom_id[:8]]
        if self.title:
            parts.append(repr(self.title))
        parts.append(f"last turn {self.last_turn or 'unknown'}")
        parts.append(self.native_id)
        return " · ".join(parts)

    def selected_by(self, query: str) -> bool:
        if self.name is not None and self.name.casefold() == query.casefold():
            return True
        if query in {self.loom_id, self.native_id}:
            return True
        # An ID prefix long enough to read off a receipt or a listing.
        return len(query) >= 8 and (
            self.loom_id.startswith(query) or self.native_id.startswith(query)
        )


@dataclass(frozen=True)
class RecoverRequest:
    selector: str | None
    dry_run: bool
    arguments: tuple[str, ...]


def parse_recover(
    harness: str, arguments: list[str], takes_value: Callable[[str, str], bool]
) -> RecoverRequest:
    """Separate recovery's own options from the arguments the resume keeps."""
    selector: str | None = None
    requested = dry_run = False
    kept: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            kept.extend(arguments[index:])
            break
        if argument == "--recover":
            requested = True
        elif argument.startswith("--recover="):
            requested = True
            selector = argument.split("=", 1)[1]
            if not selector:
                raise ContinuationError("--recover= needs a Loom name or session ID")
        elif argument == "--dry-run":
            dry_run = True
        elif argument in RECOVER_CONFLICTS or argument.split("=", 1)[0] in RECOVER_CONFLICTS:
            raise ContinuationError(f"--recover cannot be combined with {argument.split('=', 1)[0]}")
        else:
            kept.append(argument)
            if "=" not in argument and takes_value(harness, argument) and index + 1 < len(arguments):
                index += 1
                kept.append(arguments[index])
        index += 1
    if not requested:
        raise ContinuationError("--recover was not requested")
    return RecoverRequest(selector, dry_run, tuple(kept))


def effective_project(
    harness: str, arguments: list[str] | tuple[str, ...], takes_value: Callable[[str, str], bool]
) -> Path:
    """The directory the resumed conversation runs in: a forwarded --cd, else the cwd."""
    project = Path.cwd()
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--":
            break
        flag, separator, value = argument.partition("=")
        if flag in {"--cwd", "--cd", "-C"}:
            if not separator:
                if index + 1 >= len(arguments):
                    raise ContinuationError(f"{flag} requires a value")
                index += 1
                value = arguments[index]
            project = Path(value).expanduser()
        elif not separator and takes_value(harness, argument):
            index += 1
        index += 1
    try:
        return project.resolve(strict=True)
    except OSError as error:
        raise ContinuationError(f"project directory does not exist: {project}") from error


def transcript_path(harness: str, native_id: str) -> Path | None:
    directory, pattern = TRANSCRIPT_STORES[harness]
    root = Path.home() / directory
    try:
        found = [path for path in root.glob(pattern.format(native_id=native_id)) if path.is_file()]
    except OSError:
        return None
    return max(found, key=lambda path: path.stat().st_mtime) if found else None


def transcript_summary(harness: str, path: Path) -> tuple[str | None, str | None]:
    """The transcript's latest generated title and its last turn's timestamp.

    Only Claude's transcript format is read; the others report the file's
    modification time as the last turn and carry no title.
    """
    title: str | None = None
    last_turn: str | None = None
    if harness != "claude":
        moment = datetime.fromtimestamp(path.stat().st_mtime)
        return None, moment.strftime("%Y-%m-%d %H:%M")
    with path.open(encoding="utf-8", errors="replace") as transcript:
        for line in transcript:
            if '"ai-title"' not in line and '"timestamp"' not in line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "ai-title" and isinstance(entry.get("aiTitle"), str):
                title = entry["aiTitle"]
            elif entry.get("type") in {"user", "assistant"} and isinstance(entry.get("timestamp"), str):
                last_turn = entry["timestamp"]
    if last_turn is not None:
        try:
            moment = datetime.fromisoformat(last_turn.replace("Z", "+00:00"))
            last_turn = moment.astimezone().strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return title, last_turn


def unended_runs(
    binary: str, project: Path, harness: str, *, timeout: float = 30
) -> tuple[list[UnendedRun], list[str]]:
    """Loom's unended runs that still have a transcript, and the IDs that lack one."""
    response = object_value(
        query_json(
            [binary, "continuation", "unended", "--project", str(project),
             "--harness", harness, "--json"],
            timeout=timeout,
        ),
        "Loom unended runs",
    )
    if response.get("schema") != "agent-loom-continuation/v1":
        raise ContinuationError("agent-loom does not provide the continuation/v1 unended listing")
    if response.get("ok") is not True:
        error = response.get("error")
        detail = error.get("message") or error.get("code") if isinstance(error, dict) else None
        raise ContinuationError(f"Loom could not list unended runs: {detail or 'no diagnostic returned'}")
    raw_runs = response.get("runs")
    if not isinstance(raw_runs, list):
        raise ContinuationError("Loom unended runs: expected a runs list")
    runs: list[UnendedRun] = []
    missing: list[str] = []
    for raw in raw_runs:
        run = object_value(raw, "unended run")
        native_id = string_value(run.get("nativeId"), "unended run native ID")
        transcript = transcript_path(harness, native_id)
        if transcript is None:
            missing.append(native_id)
            continue
        name = run.get("name")
        title, last_turn = transcript_summary(harness, transcript)
        runs.append(UnendedRun(
            string_value(run.get("loomId"), "unended run Loom ID"),
            name if isinstance(name, str) and name else None,
            native_id,
            string_value(run.get("startedAt"), "unended run start"),
            transcript,
            title,
            last_turn,
        ))
    return runs, missing


def selected(
    harness: str, runs: list[UnendedRun], selector: str | None, project: Path
) -> list[UnendedRun]:
    candidates = runs if selector is None else [run for run in runs if run.selected_by(selector)]
    if not candidates:
        if selector is None:
            raise ContinuationError(f"no {harness} session in {project} ended without exiting")
        raise ContinuationError(f"no unended {harness} session in {project} matches {selector!r}")
    return candidates


def choose(
    harness: str, runs: list[UnendedRun], selector: str | None, project: Path
) -> UnendedRun:
    candidates = selected(harness, runs, selector, project)
    if len(candidates) == 1:
        return candidates[0]
    labels = [run.label for run in candidates]
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        listing = "".join(f"\n  {label}" for label in labels)
        raise ContinuationError(
            f"{len(candidates)} unended sessions in {project}; "
            f"name one with --recover=<name-or-id>:{listing}"
        )
    print(f"agent-model: {len(candidates)} sessions in {project} ended without exiting:", file=sys.stderr)
    for index, label in enumerate(labels, 1):
        print(f"  {index}) {label}", file=sys.stderr)
    print("Choose a number, or q to cancel: ", end="", file=sys.stderr, flush=True)
    answer = sys.stdin.readline().strip()
    if not answer.isdecimal() or not 1 <= int(answer) <= len(candidates):
        raise ContinuationError("recovery cancelled")
    return candidates[int(answer) - 1]


def recover(arguments: list[str], takes_value: Callable[[str, str], bool]) -> int:
    """agent-model recover HARNESS --launcher PATH --native-binary PATH -- ARGV"""
    if (
        len(arguments) < 6
        or arguments[1] != "--launcher"
        or arguments[3] != "--native-binary"
        or arguments[5] != "--"
    ):
        raise ContinuationError(
            "recover requires HARNESS --launcher PATH --native-binary PATH -- ARGV"
        )
    harness = arguments[0]
    if harness not in RECOVER_HARNESSES:
        raise ContinuationError(f"--recover is not supported for {harness}")
    # Keep the agent-named path: resolving it would lose the harness dispatch.
    launcher = Path(arguments[2]).absolute()
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise ContinuationError(f"launcher is not executable: {launcher}")
    request = parse_recover(harness, arguments[6:], takes_value)
    project = effective_project(harness, request.arguments, takes_value)
    runs, missing = unended_runs(required_binary("agent-loom"), project, harness)
    for native_id in missing:
        print(f"agent-model: skipping unended {native_id}: no transcript to resume", file=sys.stderr)
    if request.dry_run:
        print(f"Unended {harness} sessions in {project}:")
        for run in selected(harness, runs, request.selector, project):
            print(f"  {run.label}")
        return 0
    run = choose(harness, runs, request.selector, project)
    print(f"agent-model: recovering {run.label}", file=sys.stderr)
    invocation = [str(launcher), *RESUME_ARGUMENTS[harness], run.native_id, *request.arguments]
    os.execv(invocation[0], invocation)
    raise AssertionError("execv returned")


def continue_requested(
    harness: str, arguments: list[str], takes_value: Callable[[str, str], bool]
) -> bool:
    skip_value = False
    for argument in arguments:
        if skip_value:
            skip_value = False
            continue
        if argument == "--":
            return False
        if argument in {"--continue", "-c"}:
            return True
        if "=" not in argument and takes_value(harness, argument):
            skip_value = True
    return False


def notice_unended(
    arguments: list[str], takes_value: Callable[[str, str], bool]
) -> None:
    """agent-model notice-unended HARNESS -- ARGV

    Before `--continue`, name the unended sessions it will not reopen. Advisory:
    it never changes the launch, and a failed lookup says so in one line.
    """
    if len(arguments) < 2 or arguments[1] != "--":
        raise ContinuationError("notice-unended requires HARNESS -- ARGV")
    harness, argv = arguments[0], arguments[2:]
    if harness not in CONTINUE_HARNESSES or not continue_requested(harness, argv, takes_value):
        return
    try:
        runs, _ = unended_runs(
            required_binary("agent-loom"), effective_project(harness, argv, takes_value),
            harness, timeout=NOTICE_TIMEOUT,
        )
        if not runs:
            return
        newest = max(runs[0].transcript.parent.glob("*.jsonl"), key=lambda path: path.stat().st_mtime)
    except (ContinuationError, OSError) as error:
        print(f"agent-model: could not check for unended sessions: {error}", file=sys.stderr)
        return
    # Compare transcripts, not names: OMP's carry a date ahead of the session ID.
    passed_over = [run for run in runs if run.transcript.resolve() != newest.resolve()]
    if passed_over:
        names = "; ".join(run.label for run in passed_over)
        print(
            f"agent-model: --continue reopens the newest conversation here, not "
            f"{len(passed_over)} that ended without exiting ({names}); "
            "use --recover to reopen one",
            file=sys.stderr,
        )
