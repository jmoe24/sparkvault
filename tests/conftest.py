import json
import re
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import auth                      # noqa: E402
from app.config import Settings           # noqa: E402
from app.main import create_app           # noqa: E402

PASSWORD = "correct horse battery"
TOKEN = "t" * 40
QWEN = "491c2f1ea524c639598bf8fa787a93fed5a6fbce"
GPT = "b5c939de8f754692c1647ca79fbf85e8c1e70f8a"


def _blob(path: Path, data=b"x" * 64):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def make_cache(hf: Path):
    """A fake HF cache covering every state the drift view has to tell apart."""
    hub = hf / "hub"
    # qwen-agent: per-model blobs, matches the pin -> ok
    q = hub / "models--nvidia--Qwen3.6-35B-A3B-NVFP4"
    _blob(q / "blobs" / "aaa")
    (q / "snapshots" / QWEN).mkdir(parents=True)
    (q / "snapshots" / QWEN / "model.safetensors").symlink_to("../../blobs/aaa")
    (q / "refs").mkdir()
    (q / "refs" / "main").write_text(QWEN)
    # gpt-oss: SHARED blob store (newer hf), but refs/main moved -> drift
    g = hub / "models--openai--gpt-oss-120b"
    _blob(hub / "blobs" / "ab" / "abc123", b"y" * 5000)
    (g / "blobs").mkdir(parents=True)
    (g / "blobs" / "abc123").symlink_to("../../blobs/ab/abc123")
    (g / "snapshots" / ("c" * 40)).mkdir(parents=True)
    (g / "snapshots" / ("c" * 40) / "model.safetensors").symlink_to("../../blobs/abc123")
    (g / "refs").mkdir()
    (g / "refs" / "main").write_text("c" * 40)
    # llama: the failed-rsync signature, dangling link -> broken
    l = hub / "models--nvidia--Llama-3.3-70B-Instruct-NVFP4"
    (l / "snapshots" / ("d" * 40)).mkdir(parents=True)
    (l / "snapshots" / ("d" * 40) / "model.safetensors").symlink_to("../../blobs/missing")
    (l / "refs").mkdir()
    (l / "refs" / "main").write_text("d" * 40)
    # nemotron: interrupted download -> broken
    n = hub / "models--nvidia--NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4"
    _blob(n / "blobs" / "eee.incomplete")
    _blob(n / "blobs" / "fff")
    (n / "snapshots" / ("e" * 40)).mkdir(parents=True)
    (n / "snapshots" / ("e" * 40) / "m.safetensors").symlink_to("../../blobs/fff")
    (n / "refs").mkdir()
    (n / "refs" / "main").write_text("e" * 40)
    # stray model nobody catalogued
    s = hub / "models--foo--bar"
    (s / "snapshots").mkdir(parents=True)


@pytest.fixture
def env(tmp_path):
    repo, hf = tmp_path / "repo", tmp_path / "hf"
    repo.mkdir(parents=True)
    shutil.copy(ROOT / "examples" / "library.yaml", repo / "library.yaml")
    shutil.copytree(ROOT / "examples" / "results", repo / "results")
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("1234567890abcdef1234567890abcdef12345678\n")
    make_cache(hf)
    st = Settings(catalogue_dir=repo, hf_dir=hf, data_dir=tmp_path / "data", secret_key="k" * 40,
                  admin_hash=auth.hash_password(PASSWORD), inbox_dir=tmp_path / "inbox", ingest_token=TOKEN)
    (tmp_path / "inbox").mkdir()
    return st


@pytest.fixture
def client(env):
    return TestClient(create_app(env), follow_redirects=False)


def csrf_of(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


@pytest.fixture
def authed(client):
    t = csrf_of(client.get("/login").text)
    r = client.post("/login", data={"password": PASSWORD, "csrf": t, "next": "/"})
    assert r.status_code == 303
    return client
