# Agent Launcher

[![CI](https://github.com/osteele/agent-launcher/actions/workflows/ci.yml/badge.svg)](https://github.com/osteele/agent-launcher/actions/workflows/ci.yml)

Launchers for the kimi, opencode, codex, OMP, and agy coding-agent CLIs. Each
launcher finds the real binary, puts the
[agent-command-guards](https://github.com/osteele/agent-command-guards) shadows at
the front of `PATH` for the session, gives the session a stable identity, and
then `exec`s the agent. Around that core it adds model-first shell commands,
`--resume` by session name or transcript text, continuation across harnesses,
and an exit receipt that names the session that just ended.

This is personal tooling, written against one machine's set of agents and
services:

| Dependency | Used for | Without it |
| --- | --- | --- |
| [agent-command-guards](https://github.com/osteele/agent-command-guards), checked out beside this repository | The command guards each session runs under | Every launch warns that the session runs unguarded. If it lives elsewhere, run setup with `AGENT_COMMAND_GUARDS_DIR` set to its `shadows/` directory; setup records it in the shell environment it writes. A launch from a process that never read that environment still looks beside this checkout. |
| Python 3.11+ | `launchers/agent-model` | Model-first commands and resume resolution fail |
| [agent-loom](https://github.com/osteele/agent-loom) | Session names, for receipts and resume by name | Receipts and resume fall back to native IDs |
| AgentsView | Resume by transcript text; cross-harness continuation | Only exact IDs and names resolve |
| claude-wrapper | Claude Code's equivalent launcher | Claude Code launches are outside this repository |

## Installation

`agent-launcher` is a generic launcher invoked through a symlink named for the
agent it starts. The symlink name selects the real binary to resolve and whether
that agent needs the Zsh bridge; the rest is shared.

```bash
./launchers/setup            # install
./launchers/setup --dry-run  # preview
./launchers/setup --uninstall
```

Setup links `~/bin/kimi`, `~/bin/opencode`, `~/bin/codex`, and `~/bin/omp` to the launchers,
and adds a managed block to `~/.zshenv`, `~/.zshrc`, and `~/.bashrc` that prepends
`launchers/` to `PATH` and, inside an agent session, restores the guards to the
front of it. That subdirectory holds only the launchers, so making it globally
visible makes nothing else a command. Setup warns when it cannot find the guards
beside this checkout or at `AGENT_COMMAND_GUARDS_DIR`. Verify with:

```bash
kimi wrapper doctor
opencode wrapper doctor
codex wrapper doctor
omp wrapper doctor
```

Adding another agent takes a symlink in `launchers/` plus, if its installer puts
the binary somewhere a login shell would not find, an entry in the launcher's
`fallback_candidates`.

## Model-first interactive commands

`launchers/agent-model` lets an interactive shell select a model first and a
harness second. Source `shell/agent-models.sh` from the interactive shell setup
to define `claude`, `fable`, `codex`, `kimi`, and `glm` as shell functions. The
functions are not inherited by subprocesses, so a program that runs `codex
exec` still reaches the native Codex launcher.

Choose a harness for one invocation with `--harness` or `-h`:

```bash
codex                         # use the configured default harness
codex --harness codex         # use Codex itself
codex -h own                  # same: use this model's native harness
codex -h self                 # `self` is a synonym for `own`
codex -h .                    # `.` is a synonym for `self`
claude -h omp                 # use OMP with the current Opus selector
codex -h                      # pass -h through to the selected harness
```

The short form is consumed only when the next word is a known harness or the
`own`/`self`/`.` native-harness alias. A bare `-h`, or `-h` followed by any other
word, remains the selected harness's help option. `--harness` reports an unknown
value as an error.

Use `--resume SESSION` with every model-first command. The selected conversation
determines the harness, and the launcher translates the request to that harness's
syntax (`resume`, `--resume`, or `--session`). Exact IDs are checked against local
session stores and AgentsView; an identifier's shape alone does not establish its owner.

`SESSION` does not have to be an identifier. The agent launchers accept the same
three forms, so `omp --resume "Efficient Deer"` works as well as
`claude --resume "Efficient Deer"`:

```bash
codex --resume 01a02c18-042f-7950-8d9a-7d88b50c8cab   # a session id
codex --resume "Efficient Deer"                       # an agent-mail session name
codex --resume "the already-verified result"          # text from the transcript
```

A name is what agent-mail, the dashboards, and a peer's message call a session:
its display name ("Efficient Deer"), its slug (`efficient-deer`), or the
project-qualified address (`augur-efficient-deer`). Names come from agent-mail's
own store, so a session that has exited still resolves.

Transcript text is matched against AgentsView's index of message text, not tool
arguments or tool output. The session you are typing in is never a candidate: the
phrase you type to find an old session lands in the current session's transcript.

The newest matching conversation is the default, with alternatives listed on
stderr by ID. An explicit harness sets a preference:

- If a newer match belongs to another harness, an interactive launch prompts
  for a choice. An unattended launch selects the newest match in the requested harness.
- If the requested harness has no match, the launcher switches to the newest
  matching conversation's owner.

A switch that leaves the requested model without a configured route prompts an
interactive caller to select a compatible model. Unattended launches fail with
an error instead of substituting a saved or default model.

A conclusive miss produces a launcher error. When lookup is unavailable, an
exact native ID can be attempted using the invocation's selected harness.
Names and transcript queries require a resolved conversation; unresolved text
is never sent to a provider as a resume ID. Resolution requires
`launchers/agent-model` and Python 3.11 or later.

Defaults come from `agent-models.toml` at the repository root. Each command
names its native harness as `home` — what `--harness own`, `--harness self`, and
`--harness .` select — the harness an unqualified invocation uses as `default`, and one entry
per supported `(command, harness)` pair:

```toml
version = 1

[commands.glm]
home = "opencode"
default = "omp"
routes.opencode = { model = "zai-coding-plan/glm-5.3-flash" }
routes.omp = { model = "zai/glm-5.3-flash" }
```

A route carries a `model`; how each harness spells it — `--model=X`, `--model X`,
`-m X` — is the harness's API and stays in the code.
An empty table is a harness that needs no extra arguments. `home` and `default`
must name routes that exist, which is checked at load, so a bad edit fails on the
next command rather than resolving to something unintended.

Machine-local overrides live in
`${XDG_CONFIG_HOME:-$HOME/.config}/agent-models/config.toml`, which selects a
harness per command and nothing else:

```toml
version = 1

[defaults]
claude = "claude"
fable = "claude"
```

The CLI reads and writes that file only; `agent-models.toml` is hand-edited:

```bash
agent-model default list
agent-model default get codex
agent-model default set glm opencode
agent-model default reset glm
agent-model resolve codex -h own
agent-model config path
agent-model config check
agent-model doctor
```

Every command ships defaulting to OMP. The native Claude routes resolve `claude`
through `PATH`, so an installed `claude-wrapper` remains responsible for profiles
and command guards. The OMP routes use provider-qualified model selectors for
Anthropic, Fable, Codex, Kimi Code, and the Z.AI coding plan.

The shipped OMP routes select Claude Opus 5.5 for `claude` and GPT-6 Astra for
interactive `codex` launches. Native Claude model defaults come from the
selected `claude-wrapper` profile.

## Continuing in a fresh conversation

`--from` starts a fresh native conversation in the selected destination harness
while keeping the source session's Loom identity, name, mail state, and
obligations. The source must be offline.

```bash
codex --from "Fortunate Blanket"
codex --from e7be4b46-44c7-4e38-aa7d-476fd5e11e5a
codex --from "Fortunate Blanket" --dry-run
codex --from "Fortunate Blanket" "Continue with the failing integration test."
```

With the model-first shell functions, the configured destination still applies.
Use `codex --harness own --from "Fortunate Blanket"` to select the native Codex
harness explicitly. `--from` never changes the destination to the source's
harness. Use `--resume` to reopen an existing native conversation.

The source and destination can use the same harness. For OMP-to-Claude or
Claude-to-Claude continuation, stop the source agent, then run:

```sh
command claude --from SOURCE --dry-run
command claude --from SOURCE
```

Replace `SOURCE` with the source session UUID or a quoted Loom name.
`command claude` bypasses model-first shell functions and selects the Claude wrapper.
For a Claude compaction loop, use this fresh-conversation path instead of
`--resume`. It reads bounded historical context and keeps the full transcript
export available in the bundle. It does not repair Claude's compaction behavior,
and it requires a readable source transcript.

Sources can be Loom names, Loom IDs, or exact native session IDs. Ambiguous names
require interactive selection; unattended launches require an exact match.
An existing Loom ID follows that identity's latest confirmed native conversation.
Use `loom:ID` or `claude:ID`, `codex:ID`, and `omp:ID` to disambiguate.
An older native conversation cannot take the identity back from its successor.
Claude, Codex, and OMP have continuation adapters. Other source or destination
harnesses are refused rather than launched without a verified identity binding.
Native resume/fork options cannot be combined with `--from`.

The launcher requires Python 3.11+, AgentsView, and an agent-loom installation
with the continuation API. It checks the transcript and project before launch
and prints the source, destination, project, and private transcript-bundle path.
The source project's directory is the default; `--cd DIR` selects an explicit
override. Project mail stays in its original project spool; an override does not
move or replay it. `--dry-run` inspects transcript coverage, eligibility, and
startup readiness without reserving or changing ownership.

Install the native lifecycle callbacks for Claude or Codex before using that
destination:

```sh
agent-loom continuation hooks install --harness codex --project /path/to/project --native-binary /path/to/native/codex --json
agent-loom continuation hooks install --harness claude --project /path/to/project --native-binary /path/to/native/claude --json
```

Installation preserves other hooks and trusts only Loom's exact hook definitions.
Launches check readiness without granting trust. OMP loads Loom's packaged
startup extension explicitly, including when extension discovery is disabled.

When `agent-loom` is on `PATH`, Codex and OMP startup preparation requires Python
3.11+. Ordinary OMP conversations and native Claude/Codex resumes also require
Loom's continuation API and ready startup callbacks. OMP can resume automatically,
so its check applies even without a resume flag. These launches stop if readiness
cannot be verified; they do not bypass the identity fence. Update agent-loom
together with the launchers. Ordinary launches without agent-loom remain available;
`--from` always requires it.

Bundles live under
`${XDG_STATE_HOME:-~/.local/state}/agent-command-guards/continuations/`.
They contain the raw AgentsView export, attributed message history, a bounded
context preview, and a manifest with coverage and content hashes. Historical
tools and permission settings are evidence, not commands or permission grants.
The preview reports truncation and retains branch metadata in the full history.
Earlier bundles remain available through continuation lineage.

The destination uses a new native conversation ID. Session obligations retain
their existing IDs and both ownership roles; they are not copied or recreated.
Process-owned claims and work leases require their own recovery or transfer.
Live or unverifiable predecessors, failed exports, incompatible Loom versions,
and competing takeovers stop the launch.

## CPU priority

The launcher starts the agent under `nice -n 5`, which the agent's whole process
tree inherits. Niceness only arbitrates contention — an otherwise-idle machine
runs the agent at full speed — so delegated work yields to interactive use and
costs nothing the rest of the time. Set `AGENT_LAUNCHER_NICE` to change the
value (`0` disables).

## Codex open-file limit

Codex loads skills concurrently. Its current loader can exceed macOS's default
soft limit of 256 file descriptors and then skip valid skills with `Too many
open files (os error 24)`. The Codex launcher raises a lower inherited soft
limit to 65,536 before starting the binary. It leaves the hard limit unchanged.

## Session identity

The launcher exports `AGENT_SESSION_ID`, a fresh id per launch, and unsets
`CLAUDE_CODE_SESSION_ID` and `CODEX_THREAD_ID` first.

Claude Code and Codex export a per-session id into their shell subprocesses. Kimi
and opencode export none. OMP exposes its native conversation id to extensions,
but not to MCP or tool subprocesses, so its push bridge, tools, and Weft jobs
otherwise acquire different identities. Addressing agent-mail to one session
rather than broadcasting to the whole project is the case that motivated this.
`AGENT_SESSION_ID` fills the gap.

The unset handles nesting. An agent started from inside another agent's shell
inherits that parent's session id, and answering to it would attribute this
session's work to the parent. Minting unconditionally does the same for an
inherited `AGENT_SESSION_ID`.

An explicit resume id skips the fresh mint and becomes `AGENT_SESSION_ID`, so
a resumed session keeps the address its mail and tools already know. A name or a
line of transcript text resolves to that id before anything reads it, so
resuming by name lands on the same address as resuming by id. AgentsView
lists sessions under canonical ids (`omp:<uuid>`, `codex:<uuid>`,
`opencode:ses_...`, `kimi:<machine>:<channel>:session_<uuid>`,
`antigravity-cli:<uuid>`) that the agents themselves do not resolve, so the
launcher strips its own agent's prefix from a pasted id before exec.

## Provider credentials

The launcher unsets `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` before starting
`omp`. Set `AGENT_LAUNCHER_KEEP_API_KEYS=1` to keep them.

OMP treats a key it finds in the environment as being logged in: the provider
list shows the provider as `logged in (env)` rather than `(login)`, and requests
bill at API rates instead of against the subscription. Authorizing the
subscription afterwards does not displace it, because an environment key leaves
the stored OAuth credential unused unless one is explicitly selected. Claude Code
asks before using a key it did not obtain itself; OMP does not ask. On a machine
that loads keys from a keychain into every interactive shell, that turns a
subscription session into a metered one with nothing on screen to say so.

`OPENAI_API_KEY` was set in every agent session when this was measured on
2026-09-05, so OMP routed to `openai-codex` was billing per token for the same
reason Anthropic would have been. Z.AI is deliberately absent: its only
available key bills the GLM coding plan, so a key there *is* the subscription.

The unset covers `omp` alone. The other launched agents authenticate elsewhere
and have no reason to lose the keys.

## The Zsh bridge

`launchers/shell-init` is a private `ZDOTDIR` whose startup files source the
user's own configuration and then restore the shadow directory to the front of
`PATH`. Agents need it only if they re-source shell configuration for their shell
tool, because doing so puts mise's `uv` shim back ahead of the shadows.

- `opencode` takes a shell snapshot that sources `${ZDOTDIR:-$HOME}/.zshrc`, and
  `codex` re-sources configuration the same way, so both get the bridge.
- `kimi` runs tool commands through `sh -c`, which reads no startup files, so it
  inherits `PATH` directly and does not.

The bridge covers Zsh only. An agent that snapshots Bash would need its own.

## Exit receipts

These agents render on the alternate screen, so exiting restores a scrollback
with no sign of which session just ended. After a launched agent exits, the next
shell prompt prints a receipt naming it by its agent-mail name:

```
┌ llm-performance-models · Flying Cake
│ 42m · kimi · exited normally
└ resume  kimi --resume Flying\ Cake
```

The resume command prefers the session's readable name, but only a name that
leads back to this conversation: every session recorded under it must verify as
one native conversation owned by the harness that ran, and that conversation must
be this run's own when its ID is known. Otherwise the receipt offers the native ID,
again only after the owning harness recognizes that exact identity. A launcher
bookkeeping ID establishes neither, so an agent whose name is keyed on one shows
its name without offering it for resume. If nothing verifies within two seconds,
the receipt has no resume command.

Receipts describe the project, actual harness, elapsed time, and process exit
status. A successful process exit does not claim that its task was completed.
The shell consumes each card once and preserves the command and pipeline statuses.

Both input and output must be terminals. Headless, diagnostic, nested, and
control-subcommand launches (`omp auth-broker`, `codex login`, `agy update`)
write no card, so they cannot overwrite an outer interactive session's
receipt. Claude Code's separate wrapper owns its receipt, including when a
resume request switches to Claude.

Set `AGENT_EPILOGUE_DIR` to move the card directory
(default `~/.cache/agent-command-guards/epilogue`).

The reader is a Zsh `precmd` hook installed by `launchers/setup` into
`~/.config/agent-launchers/env`. Bash gets no receipt: there is no equivalent
hook that can run after the prompt captures its status without displacing it.

## Tests

```bash
python3 -m unittest          # from the repository root
```

The suite uses `unittest`. It drives the real launchers as subprocesses against
temporary homes and fake agent binaries, and stands in for the command guards
with stubs under `tests/fixtures/guards`, so it runs without
agent-command-guards installed.

The launch and resume policies are specified in
[`agent-launch.allium`](specs/agent-launch.allium). Regression test docstrings
identify the rules and invariants they exercise.

CI runs it on Linux, macOS, and Windows across Python 3.11–3.14. Suites that
need POSIX shells or terminal emulation skip on Windows with their reason.
