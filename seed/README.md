# Seed data

Loads a reproducible customer base into Salesforce and Supabase.

This is not part of any running service. It is run by hand, once per
environment, to populate the two systems the agent reads from. Everything
derives from a fixed seed, so the same run produces identical data on every
machine, and both loaders match on an identifier this project owns rather than
on the ids those systems assign — so re-running updates records in place
instead of creating duplicates.

## Order

Customers first, then equipment, because devices reference households.

```sh
set -a; . ../.env; set +a

python generate.py           # print what would be produced, writes nothing
python load_salesforce.py    # accounts, contacts, historical cases
python load_supabase.py      # equipment and current state
```

## What lands where

| System | Holds |
|---|---|
| Salesforce | Households, the people who call, service plan, account status, verbal passcode, support history |
| Supabase | Installed equipment and its live state: reporting or not, battery, last contact |

## Notes

`load_supabase.py` talks to Postgres directly, which is the right tool for
schema changes and bulk loading. The agent reads the same data over the HTTP
API instead, which is the interface a service integrating with someone else's
telemetry platform would actually be given.

The direct connection resolves over IPv6 only. That works from a developer
machine but not from inside a container on a default Docker network, so
provisioning is run from the host.
