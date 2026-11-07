# Node setup and notes

One-time steps per GPU node, done by hand as an administrator (an Ansible run would need the account it is
creating). Names below match `group_vars/gx10.yml`; change them there if you change them here.

## 1. The `ansible` account

An unprivileged user with no password and no sudo, in the `docker` group, that only your Ansible server's key
can log in as. `PUB` is the public half of the key named in `ansible_ssh_private_key_file`.

```bash
sudo adduser --disabled-password --gecos '' ansible
sudo usermod -aG docker ansible
sudo install -d -m 700 -o ansible -g ansible /home/ansible/.ssh
echo "$PUB" | sudo tee /home/ansible/.ssh/authorized_keys >/dev/null
sudo chown ansible:ansible /home/ansible/.ssh/authorized_keys && sudo chmod 600 /home/ansible/.ssh/authorized_keys
```

`docker` group membership is root-equivalent on that node. That is the real credential this setup creates.

## 2. The recipes clone (`sv_repo_dir`)

Clone your recipes repo somewhere the `ansible` user can read but not write (a path under `/opt` owned by your
admin account is simplest). It must contain, relative to the clone root:

- the launch scripts: `scripts/run-vllm.sh` (the standing model) and `scripts/candidates/<name>.sh`;
- the catalogue `library.yaml` (`sv_library_file`), in which each model's `launch_script` names its script;
- `tools/bench-vllm.sh` and `tools/bench-prompts.jsonl` (copy them from this repo's `tools/`);
- for TP-2: `scripts/run-vllm-cluster.sh`.

The launch scripts own the vLLM flags. The playbooks refuse any script path that is not `scripts/run-vllm.sh`
or `scripts/candidates/<name>.sh`. The `ansible` user cannot `git pull` the clone, so pull it as its owner
before deploying (a stale clone runs stale scripts; the bench tasks refuse a clone whose bench script has no
`BENCH_JSON` support). If the clone's owner differs from `ansible`:

```bash
sudo git config --system --add safe.directory /opt/sparkvault-recipes
```

## 3. The key file

```bash
sudo install -d -m 755 /etc/sparkvault
echo 'VLLM_API_KEY=<this node'"'"'s key>' | sudo tee /etc/sparkvault/vllm.env >/dev/null
sudo chown root:docker /etc/sparkvault/vllm.env && sudo chmod 640 /etc/sparkvault/vllm.env
```

One key per node. The playbooks `source` this file on the node, so the key is never an Ansible variable.
A non-interactive SSH session does not read `.bashrc`, which is why it is a file and not an `export` there.
To rotate: edit the file, then redeploy (a running container keeps the old key); update any gateway that holds
a copy.

## 4. Check it

```bash
ansible gx10 -m ping
ansible gx10 -m command -a id          # must list docker
ansible-playbook playbooks/gx10_vllm_status.yml
```

## TP-2 cluster

`gx10_vllm_cluster.yml` needs: the inventory names `gx101` (Ray head and API) and `gx102` (worker); the two
interconnect addresses in `sv_qsfp_ips` configured on the nodes; the image `sv_cluster_image` on both nodes
(`up` checks before it stops anything); and a `scripts/run-vllm-cluster.sh` that takes `up head`, `up worker`,
`down` and `status`, reads `MXFP4_LAYERS` / `API_HOST` from its environment if you use `sv_mxfp4_layers` /
`sv_api_host`, and restarts `vllm-main` on `down`.

The cluster preflight reads `MODEL_REV`, `MODEL_CACHE` and `MODEL_DIR` (as `NAME="value"` lines) from the cluster
script to check the weights are present at the pinned revision with no broken symlinks; keep those lines or edit
that task.

**Firewall.** Ray's head port listens on all interfaces without authentication, and host networking bypasses
Docker's `DOCKER-USER` chain. You must provide host INPUT rules that drop high ports from everything except the
interconnect (and allow the gateway on :8000 if you use `sv_api_host`), applied as root on both nodes before
`up` (the hint text is `sv_input_rules_apply_hint`). The playbook has no root, so it tests the rules instead:
before it stops anything, each node opens a throwaway listener on `sv_probe_port` and the control machine tries
to connect over the LAN; a connection stops the run. After the head starts it does the same for Ray's `:6379`
and fails the play with a `down` instruction if it answers. The probe only proves the DROP for the control
machine's address.

The cluster container is `--restart no` on purpose: a reboot mid-trial brings nothing back; run `down`.

## Gotchas

- **A stopped `vllm-main` stays stopped across reboots** (`unless-stopped` respects a manual `docker stop`). After
  any trial or cluster run, run the rollback playbook.
- **`/health` takes a minute or two after a start**; the wait task prints `FAILED - RETRYING` lines. That is
  normal; it fails only after `sv_health_retries` tries.
- **vLLM logs its start-up arguments including the API key.** Never paste raw `docker logs`, `docker inspect` or
  `ps` output.
- **Node sessions may have `http_proxy` set.** A `curl` to a non-localhost address then goes to the proxy and
  returns 000 even when the server is healthy. Use `curl --noproxy '*'`; any new task that talks to a
  non-localhost address must too.
- **Run the playbook version you think you are running.** `git log -1 --oneline` in the checkout on the
  Ansible server before a run.
- **`/health` 200 plus the smoke completion is the check.** The smoke test catches an empty-completion fault that
  `/health` does not.
- A trial container uses `--restart unless-stopped`, so a forgotten one comes back after a reboot: finish with
  the rollback playbook.
