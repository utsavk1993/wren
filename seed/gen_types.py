"""Generate Python types for the telemetry tables from the live schema.

The types are not written by hand. They are produced from what the platform
publishes about its own tables, so the allowed values for a column come from the
database rather than from someone remembering to keep two lists in step.

That only works because those columns have real types rather than text with a
constraint attached. A constraint is invisible to anything reading the published
schema, so a column carrying one is described as an ordinary string and every
generated client loses the restriction.

The platform's own command line tool does the same job. It reaches the database
over a Postgres connection, which is not always available; this reads the same
description over HTTPS, which is.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

OUTPUT = Path(__file__).resolve().parents[1] / "agent" / "systems" / "generated.py"

# What the published description calls a type, and what that is in Python.
SCALARS = {
    ("string", None): "str",
    ("string", "text"): "str",
    ("string", "uuid"): "str",
    ("string", "date"): "date",
    ("string", "timestamp with time zone"): "datetime",
    ("string", "timestamp without time zone"): "datetime",
    ("integer", None): "int",
    ("number", None): "float",
    ("boolean", None): "bool",
}


def _fetch_schema(base_url: str, key: str) -> dict[str, Any]:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/rest/v1/")
    request.add_header("apikey", key)
    request.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _camel(name: str) -> str:
    return "".join(part.title() for part in re.split(r"[^A-Za-z0-9]+", name) if part)


def _python_type(spec: dict[str, Any]) -> str:
    kind, fmt = spec.get("type"), spec.get("format")
    if kind in ("string", "integer", "number", "boolean"):
        return SCALARS.get((kind, fmt)) or SCALARS.get((kind, None), "str")
    return "Any"


def generate(schema: dict[str, Any], tables: list[str]) -> str:
    lines: list[str] = [
        '"""Types describing the telemetry tables.',
        "",
        "Generated from the schema the platform publishes about itself. Do not edit;",
        "run `python seed/gen_types.py` after changing the tables.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from datetime import date, datetime  # noqa: F401",
        "from typing import Literal",
        "",
        "from pydantic import BaseModel",
        "",
    ]

    enums: list[str] = []
    models: list[str] = []

    for table in tables:
        definition = schema["definitions"][table]
        properties: dict[str, Any] = definition["properties"]
        required = set(definition.get("required", []))
        model_name = _camel(table).rstrip("s") or _camel(table)

        fields: list[str] = []
        for column, spec in properties.items():
            if "enum" in spec:
                # A real type in the database, so its values are part of the
                # schema and come across intact.
                enum_name = _camel(spec.get("format", f"{table}_{column}").split(".")[-1])
                values = ", ".join(f'"{v}"' for v in spec["enum"])
                declaration = f"{enum_name} = Literal[{values}]"
                if declaration not in enums:
                    enums.append(declaration)
                annotation = enum_name
            else:
                annotation = _python_type(spec)

            if column not in required:
                annotation = f"{annotation} | None"
                fields.append(f"    {column}: {annotation} = None")
            else:
                fields.append(f"    {column}: {annotation}")

        models.append(
            f"class {model_name}(BaseModel):\n"
            f'    """One row of the {table} table."""\n\n'
            + "\n".join(fields)
            + "\n"
        )

    lines.extend(enums)
    lines.append("")
    lines.append("")
    lines.append("\n\n".join(models))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tables", nargs="+", default=["devices"])
    parser.add_argument("--out", default=str(OUTPUT))
    args = parser.parse_args()

    schema = _fetch_schema(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    text = generate(schema, args.tables)
    Path(args.out).write_text(text)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
