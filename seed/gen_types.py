"""Generate Python types for the connected systems from what they publish.

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

The customer system publishes no types at all, but what it does publish is
richer than a table schema: every field arrives with its kind, its length,
whether it may be empty, and for a restricted list, every value it will accept.
That is enough to produce the same thing, so it is produced the same way.

Which fields are read is a decision made here, since the customer system carries
seventy five on an account alone and this reads eight. What those fields are and
what they may contain is not decided here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TELEMETRY_OUTPUT = ROOT / "agent" / "systems" / "generated.py"
CRM_OUTPUT = ROOT / "agent" / "systems" / "generated_crm.py"

API_VERSION = "v62.0"

# What the customer system calls a field kind, and what that is in Python.
CRM_SCALARS = {
    "string": "str", "textarea": "str", "phone": "str", "email": "str",
    "url": "str", "picklist": "str", "multipicklist": "str",
    "reference": "str", "id": "str", "encryptedstring": "str",
    "date": "date", "datetime": "datetime", "time": "str",
    "boolean": "bool", "int": "int",
    "double": "float", "currency": "float", "percent": "float",
}

# Above this many values a list has stopped being a set of states worth
# modelling and is a reference table. Every country subdivision on earth is a
# restricted list too, and writing three hundred of them into a type says
# nothing while burying the ones that matter.
MAX_MEANINGFUL_PICKLIST = 20

# The fields this project reads. Everything else about them comes from the
# system itself.
CRM_FIELDS = {
    "Account": [
        "Id", "Name", "Phone", "Plan__c", "Account_Status__c", "Status_Since__c",
        "Wren_External_Id__c", "BillingStreet", "BillingCity",
        "BillingStateCode", "BillingPostalCode",
    ],
    "Contact": [
        "Id", "Name", "FirstName", "LastName", "Phone", "Email",
        "Wren_External_Id__c", "AccountId",
    ],
    "Case": [
        "Id", "CaseNumber", "Subject", "Description", "Status", "Origin",
        "Wren_External_Id__c", "Wren_Device_External_Id__c", "Wren_Occurred_On__c",
    ],
}

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


def _describe(instance_url: str, token: str, sobject: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{instance_url.rstrip('/')}/services/data/{API_VERSION}/sobjects/{sobject}/describe"
    )
    request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _crm_token(instance_url: str) -> str:
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": os.environ["SALESFORCE_CLIENT_ID"],
        "client_secret": os.environ["SALESFORCE_CLIENT_SECRET"],
    }).encode()
    request = urllib.request.Request(
        f"{instance_url.rstrip('/')}/services/oauth2/token", data=body
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["access_token"]


def generate_crm(described: dict[str, dict[str, Any]]) -> str:
    lines: list[str] = [
        '''"""Types describing the customer system\'s objects.

Generated from what that system publishes about its own fields. Do not edit;
run `python seed/gen_types.py` after changing them.
"""''',
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

    for sobject, description in described.items():
        by_name = {f["name"]: f for f in description["fields"]}
        fields: list[str] = []
        for name in CRM_FIELDS[sobject]:
            field = by_name.get(name)
            if field is None:
                raise SystemExit(f"{sobject}.{name} is not in the customer system")

            values_available = [v["value"] for v in field.get("picklistValues") or []]
            if (
                field["type"] == "picklist"
                and field.get("restrictedPicklist")
                and 0 < len(set(values_available)) <= MAX_MEANINGFUL_PICKLIST
            ):
                # Only a restricted list is worth turning into a set of values.
                # An open one accepts anything, so saying otherwise here would
                # be a claim this side invented.
                enum_name = _camel(name.replace("__c", ""))
                values = ", ".join(f'"{v}"' for v in dict.fromkeys(values_available))
                declaration = f"{enum_name} = Literal[{values}]"
                if declaration not in enums:
                    enums.append(declaration)
                annotation = enum_name
            else:
                annotation = CRM_SCALARS.get(field["type"], "str")

            if field["nillable"] or not field.get("createable", True):
                fields.append(f"    {name}: {annotation} | None = None")
            else:
                fields.append(f"    {name}: {annotation}")

        models.append(
            f"class {sobject}Row(BaseModel):\n"
            f'    """The fields this project reads from a {sobject.lower()}."""\n\n'
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
    parser.add_argument(
        "--only", choices=["telemetry", "crm"], help="generate just one of them"
    )
    args = parser.parse_args()

    if args.only != "crm":
        schema = _fetch_schema(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
        TELEMETRY_OUTPUT.write_text(generate(schema, args.tables))
        print(f"wrote {TELEMETRY_OUTPUT}")

    if args.only != "telemetry":
        instance = os.environ["SALESFORCE_INSTANCE_URL"]
        token = _crm_token(instance)
        described = {
            sobject: _describe(instance, token, sobject) for sobject in CRM_FIELDS
        }
        CRM_OUTPUT.write_text(generate_crm(described))
        print(f"wrote {CRM_OUTPUT}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
