# Deploying

Three pieces go to three places: the browser client to a static host, the two
services to a container host, and the agent's own database to managed Postgres.
Salesforce and Supabase are already hosted and need nothing.

## What has to be true first

The agent's database needs the pgvector extension, and the embedding model runs
inside the agent process rather than as a separate service, so that container
needs memory more than it needs cores.

## 1. Database

Any managed Postgres with pgvector. Create it, then apply the schema and load
the knowledge base:

```sh
export DATABASE_URL='postgres://...'
psql "$DATABASE_URL" -f db/init.sql
cd agent && python -m rag.ingest
```

## 2. Services

```sh
fly launch --config deploy/fly.api.toml --no-deploy
fly launch --config deploy/fly.agent.toml --no-deploy

# Secrets live with the host, never in the repo.
fly secrets set --app wren-agent \
  ANTHROPIC_API_KEY=... \
  SALESFORCE_INSTANCE_URL=... SALESFORCE_CLIENT_ID=... SALESFORCE_CLIENT_SECRET=... \
  SUPABASE_URL=... SUPABASE_SECRET_KEY=... \
  DEEPGRAM_API_KEY=... \
  DATABASE_URL=...

fly deploy --config deploy/fly.api.toml
fly deploy --config deploy/fly.agent.toml
```

The agent is set to stay running. A caller who has to wait for a container to
start has already hung up, and the embedding model loads on first use.

## 3. Client

```sh
cd web
VITE_AGENT_BASE_URL=https://wren-agent.fly.dev yarn build
```

Deploy `web/dist` to any static host.

Then close the agent to everything but that origin:

```sh
fly secrets set --app wren-agent WREN_ALLOWED_ORIGINS=https://your-client-host
```

## 4. Keep Supabase awake

A free Supabase project pauses after a week of inactivity, and a paused project
takes the device data with it. Anything that touches it on a schedule prevents
that:

```sh
curl -s "$SUPABASE_URL/rest/v1/devices?limit=1" \
  -H "apikey: $SUPABASE_SECRET_KEY" -H "Authorization: Bearer $SUPABASE_SECRET_KEY"
```

## Things that will bite

**The direct Postgres connection to Supabase resolves over IPv6 only.** That
works from a developer machine and generally not from inside a container. Only
the seed scripts use it; the agent reads device state over HTTPS, so this
affects loading data rather than running the service. Loading from a container
needs the session pooler connection string instead.

**Sockets and idle timeouts.** A call holds its socket open for minutes. Hosts
that cap idle connections will cut calls off mid-sentence.

**Region matters more than usual.** Every turn makes several round trips to
Salesforce, Supabase and the model. Put the agent near whichever is slowest, and
re-measure after moving: the numbers taken on a laptop do not survive the move.
