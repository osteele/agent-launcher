#!/usr/bin/env python3
"""A stand-in for `agent-loom session-name`, for tests that need session names.

agent-loom owns its name store and the rules for reading it -- reused pids,
stale breadcrumbs -- and tests them itself. These suites test what this repo
does with the answer, so the stand-in answers from a table and speaks only the
documented schemas (agent-loom-session-name/v1, agent-loom-session-names/v1).

The table is JSON at $FAKE_AGENT_LOOM_TABLE:
  {"names": {"<session id>": {"slug", "displayName", "assignedAt"?, "project"?}},
   "hostPids": {"<pid>": "<session id>"},
   "mode": "answer" | "hang" | "old" | "garbage" | "array",
   "log": "<path>"?}
Each call's arguments are appended to "log" as one JSON line when it is set.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def install(bin_dir: Path) -> None:
    """Put this stand-in on PATH as `agent-loom`. Windows runs no shebang, so
    there it is a .cmd wrapper, which PATHEXT lets callers find by that name."""
    script = Path(__file__).resolve()
    if os.name == "nt":
        (bin_dir / "agent-loom.cmd").write_text(
            f'@"{sys.executable}" "{script}" %*\r\n'
        )
    else:
        (bin_dir / "agent-loom").symlink_to(script)


def name_record(entry: dict[str, str]) -> dict[str, str]:
    return {
        "slug": entry["slug"],
        "displayName": entry["displayName"],
        "scheme": "adjective-noun",
    }


def main(argv: list[str]) -> int:
    path = os.environ.get("FAKE_AGENT_LOOM_TABLE")
    table = json.loads(open(path, encoding="utf-8").read()) if path else {}
    if table.get("log"):
        with open(table["log"], "a", encoding="utf-8") as log:
            log.write(json.dumps(argv) + "\n")
    mode = table.get("mode", "answer")
    if mode == "hang":
        time.sleep(30)
        return 0
    if mode == "old" or not argv or argv[0] != "session-name":
        print(
            f"agent-loom: unknown command: {argv[0] if argv else ''}", file=sys.stderr
        )
        return 1
    if mode == "garbage":
        print("{not json")
        return 0
    if mode == "array":
        print("[]")
        return 0
    flags: dict[str, str] = {}
    arguments = argv[1:]
    while arguments:
        flag = arguments.pop(0)
        if flag == "--json":
            continue
        flags[flag] = arguments.pop(0)
    names: dict[str, dict[str, str]] = table.get("names", {})
    if "--name" in flags:
        query = flags["--name"].casefold()
        matches = [
            {
                "sessionId": session_id,
                **name_record(entry),
                **(
                    {"assignedAt": entry["assignedAt"]} if "assignedAt" in entry else {}
                ),
            }
            for session_id, entry in names.items()
            if query in (entry["slug"].casefold(), entry["displayName"].casefold())
            or (
                "project" in entry
                and query
                == f"{os.path.basename(entry['project'])}-{entry['slug']}".casefold()
            )
        ]
        matches.sort(key=lambda match: match.get("assignedAt", ""), reverse=True)
        print(
            json.dumps(
                {
                    "schema": "agent-loom-session-names/v1",
                    "query": flags["--name"],
                    "sessions": matches,
                }
            )
        )
        return 0
    session_id = flags.get("--session")
    if session_id not in names and "--host-pid" in flags:
        session_id = table.get("hostPids", {}).get(flags["--host-pid"], session_id)
    entry = names.get(session_id) if session_id else None
    print(
        json.dumps(
            {
                "schema": "agent-loom-session-name/v1",
                "sessionId": session_id,
                "name": name_record(entry) if entry else None,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
