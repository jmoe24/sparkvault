# SparkVault Ansible playbooks

Bring a vLLM model up on your GPU nodes, benchmark it, and file the result in SparkVault, all from
one `ansible-playbook` run. This is the deploy, benchmark, record and upload loop the rest of the repo
describes.

They manage vLLM containers on any Docker host with NVIDIA GPUs, and were built and used on two ASUS Ascent
GX10 (NVIDIA GB10 / DGX Spark-class) nodes, which is where the defaults and the two-node cluster flow come
from. Host names, paths and addresses are all variables or inventory.

## What is here

| Path | What |
|---|---|
| `playbooks/vllm_status.yml` | Read-only: vllm containers, restart policy, `/health`. Run it first. |
| `playbooks/vllm_deploy.yml` | Rolling replace, one node at a time, with a health wait and a smoke completion. `-e sv_bench=true` benchmarks the result. |
| `playbooks/vllm_rollback.yml` | Remove a trial container and make sure the standing `vllm-main` runs. |
| `playbooks/vllm_bench_all.yml` | Deploy, benchmark and roll back every model in `sv_bench_models`, one by one. |
| `playbooks/vllm_cluster.yml` | Two-node tensor-parallel (TP-2) with Ray: `up`, `down`, `status`. |
| `tasks/vllm_bench.yml`, `tasks/vllm_bench_one.yml` | Included by the playbooks above; not run on their own. |
| `group_vars/vllm_nodes.yml` | **Every site-specific setting**, with comments. Edit this one. |
| `inventory.example.yml` | Copy to `inventory.yml` (git-ignored). |
| `docs/node-setup.md` | One-time setup of each node, design notes and gotchas. |

## Requirements

On the control machine (the "Ansible server"):

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # ansible-core
ansible-galaxy collection install -r requirements.yml   # no-op: only ansible.builtin modules are used
```

On each node: Docker, `git`, `curl`, `python3`, an unprivileged `ansible` account in the `docker` group,
and the three things you provide yourself (the playbooks run them, they do not create them):

1. **A recipes clone** at `sv_repo_dir` holding your launch scripts (`scripts/run-vllm.sh`,
   `scripts/candidates/<name>.sh`), the catalogue `library.yaml`, and `tools/bench-vllm.sh` +
   `tools/bench-prompts.jsonl` copied from this repo. The playbooks never build a vLLM command line.
2. **A key file** `/etc/sparkvault/vllm.env` containing `VLLM_API_KEY=...` (your launch scripts must use it).
3. For the cluster playbook: a cluster launch script `scripts/run-vllm-cluster.sh` taking `up head`,
   `up worker`, `down`, `status`, and the cluster image on both nodes.

Set-up steps with the commands are in [docs/node-setup.md](docs/node-setup.md).

## Configure

```bash
cd ansible
cp inventory.example.yml inventory.yml     # your node addresses
$EDITOR group_vars/vllm_nodes.yml                # paths, benchmark table, interconnect addresses
ssh-keyscan <node> >> ~/.ssh/known_hosts   # host_key_checking is on in ansible.cfg
ansible vllm_nodes -m ping
ansible vllm_nodes -m command -a id              # must list the docker group
```

**Catalogue on the Ansible server (optional).** If you keep your catalogue (`library.yaml` + `results/`) in its own
repo, clone it on the Ansible server and set `sv_catalogue_dir` to its absolute path (in `group_vars/vllm_nodes.yml`,
or in an override file). The benchmark tasks then read `library.yaml` from there, not from the nodes' recipes clones,
so there is one copy. `git pull` it before a run. `tools/import-results.py` is separate and needs no setting: it takes
`--catalogue DIR` on the command line.

To upload results to SparkVault, export the token and URL in the shell that runs Ansible (never commit
them). The token must equal `SPARKVAULT_INGEST_TOKEN` in `sparkvault.env` on the server:

```bash
export SPARKVAULT_INGEST_URL=http://<sparkvault-host>:8080/api/results
export SPARKVAULT_INGEST_TOKEN=...
```

Without them every record is still written to `sv_results_dir` (default `~/sparkvault-inbox`) and the run
says it did not upload.

## Use

Run everything from this `ansible/` folder. `-l node1` limits a run to one node and is the safest way to try
anything new. `--list-tasks` and `--check` change nothing.

```bash
# What is running?
ansible-playbook playbooks/vllm_status.yml

# Redeploy the standing model, one node at a time (a failed node stops the roll):
ansible-playbook playbooks/vllm_deploy.yml \
  -e sv_script=scripts/run-vllm.sh -e sv_container=vllm-main

# Try a candidate on one node (stops vllm-main there until you roll back), and benchmark it:
ansible-playbook playbooks/vllm_deploy.yml -l node2 \
  -e sv_script=scripts/candidates/candidate-a.sh -e sv_container=vllm-test -e sv_stop_main=true \
  -e sv_bench=true -e sv_model=candidate-a
ansible-playbook playbooks/vllm_rollback.yml -l node2

# Benchmark every row of sv_bench_models, rolling back after each (even on failure):
ansible-playbook playbooks/vllm_bench_all.yml --list-tasks
ansible-playbook playbooks/vllm_bench_all.yml
ansible-playbook playbooks/vllm_bench_all.yml -e sv_only=candidate-a
ansible-playbook playbooks/vllm_bench_all.yml -e sv_bench_dataset=random

# TP-2 across both nodes (takes the standing model off BOTH until `down`):
ansible-playbook playbooks/vllm_cluster.yml -e sv_cluster_action=status
ansible-playbook playbooks/vllm_cluster.yml -e sv_cluster_action=up -e sv_bench=true -e sv_model=<id>
ansible-playbook playbooks/vllm_cluster.yml -e sv_cluster_action=down
```

### Variables you pass with `-e`

| Variable | Playbook | Meaning |
|---|---|---|
| `sv_script` | deploy | `scripts/run-vllm.sh` or `scripts/candidates/<name>.sh`; anything else is refused |
| `sv_container` | deploy | `vllm-main` (port 8000) or `vllm-test` (port 8001) |
| `sv_stop_main` | deploy | stop `vllm-main` first (candidates run standalone). Default `false` |
| `sv_git_pull` | deploy | `git pull --ff-only` the clone. Default `false`: the clone belongs to your account, pull it first |
| `sv_bench`, `sv_model` | deploy, cluster | benchmark after the smoke test; `sv_model` is the catalogue id and its `launch_script` must equal `sv_script` |
| `sv_bench_dataset` | bench | `prompts` (long-form questions, runs to the token limit) or `random`. Numbers compare only within one dataset |
| `sv_only` | bench_all | one model id instead of all |
| `sv_cluster_action` | cluster | `up`, `down` or `status` |
| `sv_api_host`, `sv_input_rules_applied` | cluster | bind the head's API to a LAN address; requires the host firewall rules first |
| `sv_skip_rules_check` | cluster | skip the firewall probes (not recommended) |

## How a benchmark record is made

After a deploy and a smoke test, `tasks/vllm_bench.yml` runs `tools/bench-vllm.sh` on the node (one
single-stream request, then a concurrent run, while sampling the GPU clock), turns its JSON into a record
that fits `app/results.schema.json`, writes it to `sv_results_dir`, and POSTs it to SparkVault's inbox, where
it shows as pending until you import it. A record is `ok` only if the run completed with no failed requests and
the GPU clock was not clamped; otherwise it is written as `suspect` with the reason. Before running, it asserts
the model is in the catalogue and that the script you deployed is that model's `launch_script`, so a result
cannot be filed under the wrong model.

## Safety notes

- The API key is read on the node and never passes through Ansible variables, `-e`, or output. Tasks whose
  command line carries it have `no_log: true`. vLLM prints its own arguments, key included, at start: never
  paste unfiltered `docker logs` output (redact with `sed -E 's/vllm-[0-9a-f]{32}/<redacted>/g'` if your keys
  have that shape, or your own pattern).
- `docker` group membership is root-equivalent on a node. Treat the `ansible` account's SSH key as sensitive.
- The cluster playbook cannot apply host firewall rules (it has no root). Ray listens unauthenticated on all
  interfaces, so it probes from the control machine and stops if a high port answers. See docs/node-setup.md.
- Nothing was written to be run unattended against nodes you have not tried it on: start with `status`,
  then `-l <one node>`.
