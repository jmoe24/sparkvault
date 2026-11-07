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

> Status: the app and its 45 tests run, and the container runs on a UGOS NAS using the layout described under
> "Deploy"; it came back correctly after a NAS reboot. Plain-Linux Docker hosts are untested.

## Deploy

You need Docker with Compose on the server, a folder with your catalogue, and the Hugging Face cache the
nodes use. Everything host-specific is either in three marked lines of `docker-compose.yml` (uid/gid, the Hugging
Face folder, the port: filled in by `scripts/make-compose.py` from a small values file) or in one secrets file,
`sparkvault.env`.

**1. Get the code onto the server.** Either way works.

- *Docker host with git:* `git clone https://github.com/jmoe24/sparkvault.git /srv/sparkvault/app` on the server.
- *Anything else (NAS Docker UIs, no git on the server):* clone it on your workstation and copy it over.
  `scripts/sync-app.sh` does the copy: it ships only committed files, dry-runs unless you add `--apply`, and has no
  default destination.

  ```bash
  git clone https://github.com/jmoe24/sparkvault.git && cd sparkvault
  scripts/sync-app.sh --dest /mnt/share/sparkvault/app --init            # dry run, first copy
  scripts/sync-app.sh --dest /mnt/share/sparkvault/app --init --apply
  ```

**2. Lay out your own files next to the code, not inside it.** The code folder is replaced on every update, so
anything of yours inside it would be lost. A layout that works:

```
/srv/sparkvault/
  app/                    <- the code (git clone or sync-app.sh). Never edit inside it.
  docker-compose.yaml     <- generated from app/docker-compose.yml by scripts/make-compose.py
  sparkvault.env          <- secrets, mode 600
  catalogue/              <- your library.yaml and results/
  inbox/                  <- writable, uploads land here
```

**3. Create the secrets file.** From a clone, `scripts/init-env.sh /srv/sparkvault/sparkvault.env` asks for your
node names and an admin password, generates the session secret, and stores only a scrypt hash of the password
(mode 600). Or copy `sparkvault.env.example` and fill it in by hand.

**4. Create your compose file.** `docker-compose.yml` in this repo is the one source; you don't hand-edit a copy. Only
three values belong to your host, and the lines carrying them are marked `# @sv <name>`: `user` (the owner of your
folders; check with `ls -ldn` on the server, because an NFS-exported share can look open while the underlying
filesystem is not), `hf_dir` (the Hugging Face cache root, outside your folder) and `port` (only if 8080 is taken).
Everything else is a relative path inside your folder. Put the three values in a small file (see
`deploy.example.conf`, keep your real one outside this repo) and generate the compose file:

```bash
scripts/make-compose.py --values deploy.conf --out /srv/sparkvault/docker-compose.yaml          # shows a diff
scripts/make-compose.py --values deploy.conf --out /srv/sparkvault/docker-compose.yaml --apply  # writes it
scripts/make-compose.py --values deploy.conf --out /srv/sparkvault/docker-compose.yaml --check  # exit 1 on drift
```
It rewrites only the marked lines, validates the values, keeps the old file as `.bak`, and `--check` is how you notice
that the deployed file no longer matches the repo's. Relative paths need your Docker or NAS UI to run the file from its
own folder; if the catalogue page lists no models, the UI is running a copy from somewhere else.

**5. Start it:** `docker compose up -d --build`, or select the compose file in your NAS's Docker/Project UI. If the
page is empty or errors, check what the container sees:
`docker exec sparkvault sh -c 'id; ls -ldn /catalogue /hf /inbox'`.

**Updating:** `git pull` (or on your workstation `git pull`, then `scripts/sync-app.sh --dest ... --apply`), then restart
the project: the code is mounted into the container, so a restart picks it up. Rebuild only when `requirements.txt` or
the `Dockerfile` change. If `docker-compose.yml` itself changed, run `make-compose.py` again and recreate the container.

On a UGOS NAS this runs by building from the copied folder; building straight from a GitHub URL
(`context: https://github.com/...`) fails there because the NAS's Docker has no `git`.

### Settings in `sparkvault.env`

| Variable | Meaning |
|---|---|
| `SPARKVAULT_SECRET_KEY` | **Required.** Signs the session cookie (32+ characters). |
| `SPARKVAULT_ADMIN_HASH` | **Required.** scrypt hash of the admin password (`python3 -m app.hashpw`). |
| `SPARKVAULT_NODES` | Node names for the filter and for result records, e.g. `spark-1,spark-2`. Default `node-a,node-b`. |
| `SPARKVAULT_INGEST_TOKEN` | Turns result upload on (32+ chars). Empty = the endpoint returns 404. |
| `SPARKVAULT_COOKIE_SECURE` | Set to `1` once served over HTTPS. |

### Getting your catalogue onto the server

If the server is not the machine you edit on, `scripts/sync-catalogue.sh` copies `library.yaml` and
`results/` to a destination **you** give it (a mounted share or `user@host:/path`). There is no default
destination, it dry-runs unless you add `--apply`, and it validates your records first. Start from `examples/`:

```bash
cp -r examples ~/my-catalogue                       # edit library.yaml, add your own results/
scripts/sync-catalogue.sh --src ~/my-catalogue --dest /mnt/share/sparkvault/catalogue --init          # dry run
scripts/sync-catalogue.sh --src ~/my-catalogue --dest /mnt/share/sparkvault/catalogue --init --apply
```

(`--init` is only for the first copy into an empty folder.) The container mounts the catalogue read-only and
re-reads it when files change, so no restart is needed.

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
  `/inbox` is its one writable mount (create it and give it to the `user:` set in the compose file first).
- **Pending until you commit it:** uploads show in the UI as *pending*. To make one permanent:

  ```bash
  python3 tools/import-results.py --catalogue ~/my-catalogue /path/to/inbox          # dry run
  python3 tools/import-results.py --catalogue ~/my-catalogue /path/to/inbox --apply  # copy the valid ones
  ```

  then sync the catalogue again; the committed record replaces the pending one (same id).
- **Transport:** plain HTTP, like the login. Anything that can sniff that network segment can read the token, so it
  only buys "accepts schema-valid records into a quarantine folder". Terminate TLS in front of it before you
  widen who can reach it.

**Benchmark notes.** `tools/bench-vllm.sh` takes its output tokens/s from the server's own
`vllm:generation_tokens_total` counter, not from `vllm bench serve`, which counts only visible text and so
undercounts reasoning models (gpt-oss reported about a third of what it generated). A run that generated fewer than
95% of the requested tokens is written with `complete: false`. `BENCH_DATASET=prompts` sends the long-form questions
in `tools/bench-prompts.jsonl` instead of random tokens, which some models answer briefly (about 170 of 256 tokens for
gpt-oss-120b). Numbers are comparable only within one dataset, so say which you used in the record's `source`.

## Security model

SparkVault is built for a trusted, self-hosted network, not the open internet.

- One admin login: scrypt hash from the environment, signed session cookie, CSRF token on every POST, five failures
  lock an address out for a minute. It **refuses to start** without a session secret and an admin hash.
- Read-only mounts, read-only root filesystem, all capabilities dropped, `no-new-privileges`.
- Content-Security-Policy with no inline script or style; catalogue text is escaped (tested).
- If you find a vulnerability, please open a private security advisory on GitHub rather than a public issue.

## Open work

- **Ansible playbooks: published in [`ansible/`](ansible/README.md).** Sanitized and variable-driven (site values in
  `group_vars/vllm_nodes.yml`, secrets from the environment). Run on real nodes 2026-11-07: status, rollback, a `vllm-main`
  redeploy, cluster `status`, and a deploy + benchmark + rollback of a candidate (the record matched the one the
  private playbooks made). **Not yet exercised:** the SparkVault upload task and `vllm_cluster.yml up` / `down`
  (including the firewall probe).
- **Same-dataset comparisons: done 2026-11-06.** qwen-agent, lightning, llama-3.3-70b and qwen2.5-7b were re-run on
  `BENCH_DATASET=prompts` (`playbooks/gx10_bench_all.yml` in the homelab Ansible, label `<model>-bench-prompts`) and
  are comparable with the gpt-oss-120b cluster record. The earlier random-dataset records for the three candidates
  are marked `suspect` and superseded. Keep the dataset in each record's `source`, and re-run on `prompts` for any
  model added later. Two bench bugs surfaced and are fixed: the prompts dataset needs pandas, which the stock image
  lacks (the script installs it under `/tmp` of the container), and a warm-up correction understated tok/s by 1/(N+1).
- **Tool-call and replay results: collected by hand, 2026-11-06.** `replay-dump.py` (localai) prints a
  `REPLAY_RESULT` line and `tools/replay-record.py` turns it into a record. The captured request is readable only by
  the `hermes` user, so this is a manual step per model and not part of the benchmark playbook. Results on the one
  dump (10 requests per variant): qwen-agent, lightning and gpt-oss-120b 20/20; llama-3.3-70b 11/20 (10/10 without
  the trailing period, 1/10 with it). **Not applicable:** qwen2.5-7b, whose launch recipe sets no `--max-model-len` (the model's
  default is probably 32K, shorter than the dump's roughly 47K-token prompt); testing it would need a recipe change. The dump is Qwen-shaped, so a pass shows the model follows that one request's
  tool schema, not that it handles every tool format. Models served at 64K need `REPLAY_MAX_TOKENS` because the dump
  asks for 65536 output tokens.
- **Judge evals: qwen2.5-7b done 2026-11-07** (overall 5.6, 12/12 prompts, via the LiteLLM `scratch/*` route in localai).
  gpt-oss-120b and the others were recorded earlier. The harness writes only an `eval` block, so the record's
  `id`, `model`, `node` and `source` are added by hand before it goes into the catalogue.

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
| `ansible/` | Playbooks: deploy a model, benchmark it, record and upload the result ([README](ansible/README.md)). |
| `tools/` | `check-recipes.py`, `import-results.py`, `results-report.py` (Markdown report), `bench-vllm.sh` (+ `bench-prompts.jsonl`), `submit-result.sh`. |
| `scripts/` | `init-env.sh` (creates `sparkvault.env`), `sync-app.sh` (copies the app to your server), `sync-catalogue.sh` (copies your catalogue). |
| `Dockerfile`, `docker-compose.yml`, `sparkvault.env.example` | Container files. |
