# SparkVault

A small, self-hosted model registry for GPU nodes that serve models with vLLM (built on NVIDIA
GB10 "Spark"-class boxes, not tied to them). It gives you:

- a **catalogue** of the models you run, try and reject (`library.yaml`), with serve recipes kept as data;
- **benchmark and eval results** per model and node (`results/*.json`), with a headline number per model;
- a **cache drift view** that compares the catalogue with the Hugging Face cache on your shared storage
  (`ok`, `drift`, `unpinned`, `broken` links or `.incomplete` files, `missing`, `absent`, `stale`, uncatalogued);
- an optional **result upload** endpoint so benchmark jobs can file their own records.

It is read-only: one admin login, and the only thing it ever writes is an inbox folder of uploaded
records. It holds no credential for git, your nodes or Hugging Face.

> Status: the app and its 45 tests run. The Docker image and `docker-compose.yml` have **not been built or
> run yet** (the author's workstation has no Docker), so treat the container route as unverified and open an
> issue with what you find.

## Quick start

You need Docker with Compose, and a folder holding your catalogue. Start from the examples:

```bash
git clone https://github.com/jmoe24/sparkvault.git && cd sparkvault
cp -r examples ~/my-catalogue                     # edit library.yaml, add your own results/
scripts/init-env.sh                               # asks for your folders, node names, password; writes .env
docker compose up -d --build                      # builds from GitHub, listens on :8080
```

`init-env.sh` generates the session secret and stores only a scrypt hash of your admin password.
Prefer to do it by hand? Copy `.env.example` to `.env`, fill it in, and generate the two secrets with
`openssl rand -hex 32` and `python3 -m app.hashpw`.

The compose file needs only itself and `.env`: it builds the image from this GitHub repo (pin a tag or
commit with `SPARKVAULT_BUILD_CONTEXT`). You do not need a clone on the server.

### What you configure (all in `.env`)

| Variable | Meaning |
|---|---|
| `SPARKVAULT_CATALOGUE_HOST_DIR` | **Required.** Folder with your `library.yaml` and `results/`, mounted read-only. |
| `SPARKVAULT_HF_HOST_DIR` | **Required.** Hugging Face cache root (the folder containing `hub/`), mounted read-only. |
| `SPARKVAULT_NODES` | Node names for the filter and for result records, e.g. `spark-1,spark-2`. Default `node-a,node-b`. |
| `SPARKVAULT_INBOX_HOST_DIR` | Writable folder for uploads (default `./inbox`). |
| `SPARKVAULT_INGEST_TOKEN` | Turns result upload on (32+ chars). Empty = the endpoint returns 404. |
| `SPARKVAULT_UID` / `SPARKVAULT_GID` | User the container runs as (default 10001). Set to the owner of your folders if they are not world-readable. |
| `SPARKVAULT_PORT`, `SPARKVAULT_COOKIE_SECURE` | Host port; set the cookie flag to `1` behind HTTPS. |

### Getting your catalogue onto the server

If the server is not the machine you edit on, `scripts/sync-catalogue.sh` copies `library.yaml` and
`results/` to a destination **you** give it (a mounted share or `user@host:/path`). There is no default
destination, it dry-runs unless you add `--apply`, and it validates your records first:

```bash
scripts/sync-catalogue.sh --src ~/my-catalogue --dest /mnt/share/sparkvault/catalogue          # dry run
scripts/sync-catalogue.sh --src ~/my-catalogue --dest /mnt/share/sparkvault/catalogue --apply
```

The container mounts the catalogue read-only and re-reads it when files change, so no restart is needed.

## Catalogue format

See `examples/library.yaml` (models, status `active|testing|rejected`, pinned revision, nodes with a copy,
optional `recipe`) and `app/results.schema.json` (one record per run; `node` is any name from
`SPARKVAULT_NODES`, or two joined with `+`). A malformed file is skipped and listed at the top of every
page; it never takes the app down.

A **recipe** is a vLLM serve command stored as data. `tools/check-recipes.py` renders each recipe with the
app's own renderer and compares it token for token with the command in your launch script
(`golden.script`), so the catalogue cannot drift from what you actually run:

```bash
python3 tools/check-recipes.py                                   # the shipped examples
python3 tools/check-recipes.py --catalogue ~/my-catalogue --root ~/path/to/your/launch-scripts
```

## Result upload

Benchmark jobs can file records without anyone copying files. `tools/bench-vllm.sh` measures a running
vLLM container (`BENCH_JSON=out.json` writes the `bench` block); wrap that in a full record (see
`examples/results/`) and send it:

```bash
SPARKVAULT_URL=http://host:8080 SPARKVAULT_INGEST_TOKEN=... tools/submit-result.sh record.json
```

- **Auth:** `Authorization: Bearer <SPARKVAULT_INGEST_TOKEN>`. No session or cookie is accepted on this route,
  and it is off (404) unless a token of at least 32 characters is set. Five wrong tokens from one address lock it
  for a minute.
- **Accepts:** one JSON object, at most 64 KB, valid against the schema, whose `model` is in `library.yaml`, whose
  `id` is only letters, digits and `. _ + -` (it becomes a file name), and which does not already exist. Replies:
  `201` stored, `401` bad token, `409` id exists, `413` too big, `422` invalid, `503` inbox not writable. The
  reply never echoes the body.
- **Writes:** only `<inbox>/<id>.json`, created exclusively (never overwrites). The container stays read-only;
  `/inbox` is its one writable mount (create it and give it to `SPARKVAULT_UID:SPARKVAULT_GID` first).
- **Pending until you commit it:** uploads show in the UI as *pending*. To make one permanent:

  ```bash
  python3 tools/import-results.py --catalogue ~/my-catalogue /path/to/inbox          # dry run
  python3 tools/import-results.py --catalogue ~/my-catalogue /path/to/inbox --apply  # copy the valid ones
  ```

  then sync the catalogue again; the committed record replaces the pending one (same id).
- **Transport:** plain HTTP, like the login. Anything that can sniff that network segment can read the token, so it
  only buys "accepts schema-valid records into a quarantine folder". Terminate TLS in front of it before you
  widen who can reach it.

## Security model

SparkVault is built for a trusted, self-hosted network, not the open internet.

- One admin login: scrypt hash from the environment, signed session cookie, CSRF token on every POST, five failures
  lock an address out for a minute. It **refuses to start** without a session secret and an admin hash.
- Read-only mounts, read-only root filesystem, all capabilities dropped, `no-new-privileges`.
- Content-Security-Policy with no inline script or style; catalogue text is escaped (tested).
- If you find a vulnerability, please open a private security advisory on GitHub rather than a public issue.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python tools/check-recipes.py
```

Try the app against your own cache without Docker:

```bash
export SPARKVAULT_ADMIN_HASH="$(.venv/bin/python -m app.hashpw)"   # prompts for a password
export SPARKVAULT_SECRET_KEY="$(openssl rand -hex 32)"
export SPARKVAULT_CATALOGUE_DIR=$PWD/examples SPARKVAULT_HF_DIR=/path/to/huggingface SPARKVAULT_DATA_DIR=/tmp
.venv/bin/uvicorn app.main:app_factory --factory --host 127.0.0.1 --port 8081
```

| Path | What |
|---|---|
| `app/` | The web app (FastAPI + Jinja, server-rendered, no JavaScript) and the result schema. |
| `tests/` | Auth/CSRF/lockout, filters and sorting, drift states against mock caches, bad data never crashes the app, output escaping, the golden recipe check, upload. |
| `examples/` | A sample catalogue, results and launch scripts to copy and edit. |
| `tools/` | `check-recipes.py`, `import-results.py`, `results-report.py` (Markdown report), `bench-vllm.sh`, `submit-result.sh`. |
| `scripts/` | `init-env.sh` (creates `.env`), `sync-catalogue.sh` (copies the catalogue to your server). |
| `Dockerfile`, `docker-compose.yml`, `.env.example` | Container files. |
