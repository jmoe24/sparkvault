import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "make-compose.py"
spec = importlib.util.spec_from_file_location("make_compose", SCRIPT)
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)

TEMPLATE = (ROOT / "docker-compose.yml").read_text()


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def vals(tmp_path, text):
    p = tmp_path / "deploy.conf"
    p.write_text(text)
    return p


def test_only_the_marked_lines_change(tmp_path):
    v = mc.read_values(vals(tmp_path, "user = 1000:10\nhf_dir = /volume1/models/huggingface\nport = 8090\n"))
    out = mc.render(TEMPLATE, v, "deploy.conf")
    changed = [l for l in out.splitlines() if l not in TEMPLATE.splitlines() and not l.startswith("# GENERATED")]
    assert len(changed) == 3 and all("# @sv" in l for l in changed)
    assert 'user: "1000:10"' in out and '"8090:8080"' in out and "/volume1/models/huggingface:/hf:ro" in out
    assert out.count("# @sv") == TEMPLATE.count("# @sv")           # the markers survive, so it can be re-rendered


def test_rendering_twice_is_stable(tmp_path):
    v = mc.read_values(vals(tmp_path, "user = 1000:10\nport = 8090\n"))
    once = mc.render(TEMPLATE, v, "deploy.conf")
    assert mc.render(once.split("\n", 1)[1], v, "deploy.conf") == once


@pytest.mark.parametrize("bad", [
    "user = 1000:10\nuser = 1:1\n", "port = 99999\n", "port = 80x\n", "user = josh\n", "hf_dir = relative/path\n",
    'hf_dir = /a b\n', "hf_dir = /a:b\n", 'hf_dir = /a"b\n', "nope = 1\n", "just a line\n"])
def test_bad_values_are_refused(tmp_path, bad):
    with pytest.raises(SystemExit):
        mc.read_values(vals(tmp_path, bad))


def test_a_changed_template_line_is_refused():
    with pytest.raises(SystemExit):
        mc.render(TEMPLATE.replace('user: "1000:1000"', "user: 1000"), {}, "x")


def test_dry_run_writes_nothing_apply_writes_and_check_detects_drift(tmp_path):
    v, out = vals(tmp_path, "user = 1000:10\n"), tmp_path / "docker-compose.yaml"
    assert run("--values", str(v), "--out", str(out)).returncode == 0 and not out.exists()
    assert run("--values", str(v), "--out", str(out), "--check").returncode == 1      # missing = drift
    assert run("--values", str(v), "--out", str(out), "--apply").returncode == 0 and out.exists()
    assert run("--values", str(v), "--out", str(out), "--check").returncode == 0
    out.write_text(out.read_text().replace("restart: unless-stopped", "restart: always"))
    assert run("--values", str(v), "--out", str(out), "--check").returncode == 1
    run("--values", str(v), "--out", str(out), "--apply")
    assert (tmp_path / "docker-compose.yaml.bak").exists() and "unless-stopped" in out.read_text()


def test_the_template_is_valid_compose_yaml_with_relative_paths():
    import yaml
    d = yaml.safe_load(TEMPLATE)["services"]["sparkvault"]
    assert d["build"]["context"] == "./app" and d["env_file"] == "./sparkvault.env"
    assert "./app/app:/srv/app:ro" in d["volumes"] and d["read_only"] is True and d["cap_drop"] == ["ALL"]
