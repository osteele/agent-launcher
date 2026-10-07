# Decision log

Y-statement entries for decisions that foreclosed something without earning a
full record. Append-only, newest last. An entry is never edited to reflect a
later change; a later change is a later entry.

*In the context of X, facing Y, we decided for Z, and neglected W, to achieve Q,
accepting D.*

- **2026-09-16** — In the context of resolving a `--resume` argument that names
  an agent-mail session rather than an id, facing a CLI whose name lookup covers
  only *live* sessions while the sessions worth resuming have exited, we decided
  to read agent-mail's session-name store directly, and neglected `agent-mail
  listeners` and a `--session` resolution flag we would have had to add there,
  to keep the feature inside one repository and answer for sessions that ended
  months ago, accepting a dependency on a file layout that is agent-mail's
  private state and can change without notice — so every read degrades to "no
  match" rather than to an error, and the coupling is named in `CLAUDE.md`.

- **2026-09-16** — In the context of matching `--resume` text against
  transcripts, facing an AgentsView default search that reads tool arguments and
  results and takes ~40s against this machine's archive, we decided to query the
  `--fts` message index at ~0.5s, and neglected the fuller search and a fallback
  to it when the index misses, to keep the lookup in front of a launch rather
  than in front of a coffee break, accepting that text appearing only in tool
  output or tool arguments never matches.

- **2026-09-16** — In the context of a `--resume` name or phrase matching
  several sessions, facing generated names that collide freely across a store of
  thousands, we decided to resume the newest match and name the others with
  their ids on stderr, and neglected refusing until the user supplies an id —
  which is what agent-mail does for the live sessions it addresses — to keep the
  common case one command, accepting that a resume can land on a plausible wrong
  session when the user does not read the line that says so.

- **2026-10-07** — In the context of a repository named for its command
  guards that had grown a multi-harness launcher twice the guards' size,
  facing two halves with different audiences — the guards general and public,
  the launcher bound to agent-loom, AgentsView, and herdr — we
  decided to move the launcher into this repository with its history and find
  the guards as a sibling checkout (`AGENT_COMMAND_GUARDS_DIR` overrides), and
  neglected keeping both in one repository behind a reorganized README, so that
  the guards can be read, installed, and published without the launcher's
  machine-specific dependencies; accepting a cross-repository path the
  launcher, its setup, and the zsh path-order hook must agree on, a loud
  "runs unguarded" warning on any machine without the sibling checkout, and
  state paths (`~/.cache/agent-command-guards/epilogue`,
  `…/agent-command-guards/continuations`) that keep the old repository's name
  so cards and continuations written before the split stay readable. The
  2026-09-16 entries above moved here with the code they describe.
