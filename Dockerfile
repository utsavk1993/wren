# One container, for a host that runs one.
#
# Locally there are four services and compose starts them all. A host on a free
# plan gives a single container, so the page is built here and served by the
# same process that answers the call.

# ---- the page ----
FROM node:22-slim AS web

WORKDIR /web
RUN corepack enable
COPY web/package.json web/yarn.lock web/.yarnrc.yml ./
RUN yarn install --immutable
COPY web/ ./
RUN yarn build

# ---- the agent, serving it ----
FROM python:3.12-slim

WORKDIR /app

COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ ./
# Where the running process looks for the page.
COPY --from=web /web/dist ./web

# The schema is applied on startup and lives alongside the database definition
# rather than being duplicated here.
COPY db/ ./db/

# The host decides the port and tells the process through the environment.
ENV PORT=7860
EXPOSE 7860

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
