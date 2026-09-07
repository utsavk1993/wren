"""Build the environment file to paste into the host, from the local one.

Two things differ between running this on a laptop and running it behind a
link, and both are easy to get wrong by hand.

The database is not the same one. Locally it is a container reachable at a name
that only exists inside compose; deployed it is the hosted database, reached
through its pooled address. Uploading the local file as it stands gives the host
a hostname that does not resolve, and the service starts and then fails on every
call that touches the database.

And the limits that keep a public link from being an open tab on someone else's
budget are off locally, because there is nobody to protect against.

Run this, change the passphrase, upload the result.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".env"
OUTPUT = ROOT / ".env.render"

# Only what the deployed service reads. Anything meaningful solely to compose,
# such as the host ports, is left out rather than uploaded and ignored.
CARRIED_OVER = [
    ("The model", ["ANTHROPIC_API_KEY", "WREN_LLM_MODEL"]),
    ("Speech, both directions", ["DEEPGRAM_API_KEY", "DEEPGRAM_VOICE"]),
    (
        "Customer records",
        ["SALESFORCE_INSTANCE_URL", "SALESFORCE_CLIENT_ID", "SALESFORCE_CLIENT_SECRET"],
    ),
    ("Device state", ["SUPABASE_URL", "SUPABASE_SECRET_KEY"]),
    ("Embeddings", ["EMBEDDINGS_PROVIDER", "EMBEDDINGS_MODEL"]),
]


def read_env(path: Path) -> dict[str, str]:
    return dict(re.findall(r"^([A-Z_]+)=(.*)$", path.read_text(), flags=re.M))


def build(env: dict[str, str], passphrase: str = "CHANGE-ME") -> str:
    pooled = env.get("SUPABASE_DB_URL", "")
    if not pooled:
        raise SystemExit(
            "SUPABASE_DB_URL is not set. That is the address the deployed service "
            "uses for its own records; without it there is nothing to upload."
        )

    lines = [
        "# For pasting into the host's environment settings. Not for git.",
        "#",
        "# Built by scripts/render_env.py. Change the passphrase before uploading.",
        "",
        "# The agent's own records. The pooled address, not the direct one, which",
        "# resolves over IPv6 only and is unreachable from most hosts.",
        f"DATABASE_URL={pooled}",
        "",
        "# Those tables share a database with the device data, so they sit in",
        "# their own schema. This says where to look, and where the vector type",
        "# is. Naming it in the connection string does not work: a pooled",
        "# connection drops startup options and the setting never arrives.",
        "WREN_SEARCH_PATH=wren, public, extensions",
        "",
        "# Required before a call can start. Hand this out with the link.",
        f"WREN_PASSPHRASE={passphrase}",
        "",
        "# What a public link may spend. The daily figure stays well under what",
        "# the customer system allows in a day, so running out turns one caller",
        "# away rather than locking the account for everyone.",
        "WREN_MAX_CONCURRENT=3",
        "WREN_MAX_CALLS_PER_DAY=200",
        "",
    ]

    for label, keys in CARRIED_OVER:
        lines.append(f"# {label}")
        for key in keys:
            lines.append(f"{key}={env.get(key, '')}")
        lines.append("")

    lines += [
        "# JSON once deployed, because a machine is reading it.",
        "LOG_FORMAT=json",
        "LOG_LEVEL=INFO",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    env = read_env(SOURCE)
    missing = [
        key
        for _, keys in CARRIED_OVER
        for key in keys
        if not env.get(key) and "MODEL" not in key and "VOICE" not in key
    ]
    OUTPUT.write_text(build(env))
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    if missing:
        print("  empty, and the service will not work without them:")
        for key in missing:
            print(f"    {key}")
    print("  change WREN_PASSPHRASE before uploading")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
