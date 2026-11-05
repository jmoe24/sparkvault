import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import Settings
from app.main import create_app
from conftest import PASSWORD, ROOT, csrf_of


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# ---- authentication ----
def test_everything_but_healthz_and_login_needs_a_session(client):
    for path in ("/", "/m/qwen-agent", "/drift", "/nonexistent"):
        r = client.get(path)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), path
    assert client.get("/healthz").text == "ok"
    assert client.get("/login").status_code == 200


def test_wrong_password_is_401_and_gives_no_session(client):
    t = csrf_of(client.get("/login").text)
    r = client.post("/login", data={"password": "nope", "csrf": t})
    assert r.status_code == 401 and "Wrong password" in r.text
    assert client.get("/").status_code == 303


def test_login_without_csrf_is_refused(client):
    client.get("/login")
    assert client.post("/login", data={"password": PASSWORD}).status_code == 403
    assert client.post("/login", data={"password": PASSWORD, "csrf": "forged"}).status_code == 403


def test_cookie_is_httponly_and_samesite_strict(client):
    t = csrf_of(client.get("/login").text)
    r = client.post("/login", data={"password": PASSWORD, "csrf": t})
    c = r.headers["set-cookie"].lower()
    assert "httponly" in c and "samesite=strict" in c


def test_next_cannot_redirect_off_site(client):
    for evil in ("//evil.example", "https://evil.example/x", "/\\evil.example"):
        t = csrf_of(client.get("/login").text)
        r = client.post("/login", data={"password": PASSWORD, "csrf": t, "next": evil})
        assert r.status_code == 303 and r.headers["location"] == "/", evil
        client.cookies.clear()


def test_lockout_after_five_failures_even_with_right_password(client):
    t = csrf_of(client.get("/login").text)
    for _ in range(5):
        assert client.post("/login", data={"password": "bad", "csrf": t}).status_code == 401
    assert client.post("/login", data={"password": PASSWORD, "csrf": t}).status_code == 429


def test_logout_needs_csrf(authed):
    assert authed.post("/logout", data={}).status_code == 303
    assert authed.get("/").status_code == 200            # forged logout did nothing
    t = csrf_of(authed.get("/").text)
    authed.post("/logout", data={"csrf": t})
    assert authed.get("/").status_code == 303


def test_refuses_to_start_without_secrets(env):
    import dataclasses
    for bad in (dataclasses.replace(env, secret_key="short"), dataclasses.replace(env, admin_hash="")):
        with pytest.raises(SystemExit):
            create_app(bad)


def test_password_hash_roundtrip():
    h = auth.hash_password("a long enough password")
    assert h.startswith("scrypt:") and "$" not in h and "a long enough password" not in h
    assert auth.verify_password("a long enough password", h)
    assert not auth.verify_password("another", h) and not auth.verify_password("x", "garbage")


def test_security_headers(authed):
    r = authed.get("/")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "script-src" not in r.headers["content-security-policy"]      # no inline script allowed
    assert "<script" not in r.text and ' style="' not in r.text


# ---- catalogue ----
def test_catalogue_lists_all_models_with_headline_from_valid_runs(authed):
    t = text(authed.get("/").text)
    assert "5 of 5 models" in t
    assert "90.3" in t and "40.7" not in t              # clamped (suspect) run never the headline
    assert "1 suspect run" in t


def test_filters_and_search(authed):
    assert "1 of 5 models" in text(authed.get("/?status=active").text)
    assert "2 of 5 models" in text(authed.get("/?node=node-b").text)
    assert "1 of 5 models" in text(authed.get("/?q=llama").text)
    assert "No models match" in authed.get("/?q=zzzz").text
    assert "5 of 5 models" in text(authed.get("/?status=bogus&node=evil&sort=drop&mode=x").text)   # bad params ignored


def test_sort_judge_is_descending_and_unscored_models_come_last(authed):
    ids = re.findall(r'href="/m/([^"]+)"', authed.get("/?sort=judge").text)
    assert ids[:3] == ["qwen-agent", "nemotron-3.5-lightning", "llama-3.3-70b"]   # 8.52, 8.23, 6.73
    assert set(ids[3:]) == {"gpt-oss-120b", "qwen2.5-7b"}                                              # no judge score
    asc = re.findall(r'href="/m/([^"]+)"', authed.get("/?sort=judge&dir=asc").text)
    assert asc[:3] == ["llama-3.3-70b", "nemotron-3.5-lightning", "qwen-agent"]
    assert set(asc[3:]) == {"gpt-oss-120b", "qwen2.5-7b"}                                              # still last when reversed


def test_table_mode(authed):
    assert authed.get("/?mode=table").text.count('class="row"') == 5


def test_detail_shows_recipe_locks_and_command_without_secrets(authed):
    h = authed.get("/m/qwen-agent").text
    t = text(h)
    assert "--quantization modelopt" in t and "required on GB10" in t
    assert '--api-key "$VLLM_API_KEY"' in h or "--api-key &#34;$VLLM_API_KEY&#34;" in h
    assert not re.search(r"vllm-[0-9a-f]{32}", h)
    assert "suspect" in t                                  # the clamped run is listed


def test_unknown_model_is_404_for_a_signed_in_user(authed):
    assert authed.get("/m/nope").status_code == 404
    assert authed.get("/m/../../etc/passwd").status_code in (404, 307, 308)
    assert authed.get("/static/../app/auth.py").status_code == 404


def test_model_without_recipe_says_it_cannot_be_deployed(authed):
    assert "cannot be deployed" in text(authed.get("/m/llama-3.3-70b").text)


# ---- NAS cache drift ----
def test_drift_view_tells_every_failure_signature_apart(authed):
    t = text(authed.get("/drift").text)
    row = lambda mid: t[t.index(mid):t.index(mid) + 220]
    assert "ok Matches the pinned revision 491c2f1ea524" in row("qwen-agent")
    assert "drift" in row("gpt-oss-120b") and ("c" * 12) in row("gpt-oss-120b")
    assert "broken" in row("llama-3.3-70b") and "dangling" in row("llama-3.3-70b")
    assert "broken" in row("nemotron-3.5-lightning") and ".incomplete" in row("nemotron-3.5-lightning")
    assert "missing" in row("qwen2.5-7b")             # catalogue says on_nas, mock cache has none
    assert "foo/bar" in t                                  # in the cache, not in the catalogue
    assert "4 models need attention" in t or "3 models need attention" in t


def test_shared_blob_layout_is_recognised_and_size_counted_once(authed, env):
    from app import cache
    i = cache.scan_model(env.hf_dir, "openai/gpt-oss-120b")
    assert i.layout == "shared" and i.size == 5000 and i.dangling == 0
    assert cache.scan_model(env.hf_dir, "nvidia/Qwen3.6-35B-A3B-NVFP4").layout == "per-model"


def test_scanner_never_writes(env):
    from app import cache
    before = sorted((p, p.stat().st_mtime_ns) for p in env.hf_dir.rglob("*") if not p.is_symlink())
    for r in cache.list_repos(env.hf_dir):
        cache.scan_model(env.hf_dir, r)
    cache.shared_incomplete(env.hf_dir)
    assert before == sorted((p, p.stat().st_mtime_ns) for p in env.hf_dir.rglob("*") if not p.is_symlink())


# ---- bad data never takes the app down ----
def test_invalid_result_file_is_reported_not_fatal(authed, env):
    (env.catalogue_dir / "results" / "2026-01-01-node-a-bad.json").write_text(json.dumps({"id": "2026-01-01-node-a-bad"}))
    (env.catalogue_dir / "results" / "2026-01-02-node-a-junk.json").write_text("{not json")
    h = authed.get("/").text
    assert "2 problems" in text(h) and "2026-01-01-node-a-bad.json" in h and "2026-01-02-node-a-junk.json" in h
    assert "5 of 5 models" in text(h)


def test_result_for_unknown_model_is_reported(authed, env):
    r = json.loads(next((env.catalogue_dir / "results").glob("2026-11-02-*.json")).read_text())
    r.update(id="2026-11-04-node-a-ghost", model="ghost")
    (env.catalogue_dir / "results" / "2026-11-04-node-a-ghost.json").write_text(json.dumps(r))
    assert "unknown model" in authed.get("/").text


def test_broken_library_yaml_is_reported(authed, env):
    (env.catalogue_dir / "library.yaml").write_text("models: [unclosed")
    h = authed.get("/").text
    assert "library.yaml" in h and "0 of 0 models" in text(h)


def test_html_in_catalogue_data_is_escaped(authed, env):
    p = env.catalogue_dir / "library.yaml"
    p.write_text(p.read_text().replace("Standing model in the example.", "<script>alert(1)</script> Standing model."))
    h = authed.get("/m/qwen-agent").text
    assert "<script>alert(1)" not in h and "&lt;script&gt;" in h


# ---- the recipe renderer is the one the golden check proves ----
def test_golden_recipe_check_passes():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "check-recipes.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ---- bundled data (no git on the NAS) ----
def test_repo_commit_file_is_used_and_dirty_is_shown(authed, env):
    (env.catalogue_dir / "REPO_COMMIT").write_text("abcdef1234567890abcdef1234567890abcdef12+dirty\n")
    t = text(authed.get("/").text)
    assert "repo data abcdef1 +uncommitted" in t
    (env.catalogue_dir / "REPO_COMMIT").write_text("abcdef1234567890abcdef1234567890abcdef12\n")
    assert "repo data abcdef1 · read-only" in text(authed.get("/").text)


def test_works_with_no_git_and_no_commit_file(authed, env):
    import shutil
    shutil.rmtree(env.catalogue_dir / ".git")
    assert "repo data unknown" in text(authed.get("/").text)


# ---- an unreadable mount is reported as such, never a 500 or a false "absent" ----
@pytest.mark.skipif(__import__("os").geteuid() == 0, reason="root ignores directory modes")
def test_unreadable_cache_is_reported_not_a_500(authed, env):
    hub = env.hf_dir / "hub"
    hub.chmod(0o000)
    try:
        for path in ("/drift", "/m/qwen-agent"):
            r = authed.get(path)
            assert r.status_code == 200, path
            assert "unreadable" in r.text and "permission denied" in r.text, path
        assert "absent" not in text(authed.get("/drift").text).split("States:")[0]    # not mislabelled as missing
        assert "need attention" in authed.get("/drift").text
    finally:
        hub.chmod(0o755)


def test_missing_cache_folder_is_a_mount_problem_not_everything_absent(authed, env):
    import shutil
    shutil.rmtree(env.hf_dir / "hub")
    t = text(authed.get("/drift").text)
    assert "not found (is the Hugging Face folder mounted" in t and "unreadable" in t
    assert authed.get("/m/qwen-agent").status_code == 200


@pytest.mark.skipif(__import__("os").geteuid() == 0, reason="root ignores directory modes")
def test_unreadable_single_model_dir_is_reported(authed, env):
    d = env.hf_dir / "hub" / "models--openai--gpt-oss-120b"
    d.chmod(0o000)
    try:
        t = text(authed.get("/drift").text)
        row = t[t.index("gpt-oss-120b"):t.index("gpt-oss-120b") + 260]
        assert "unreadable" in row and "permission denied" in row
        assert "ok Matches the pinned revision 491c2f1ea524" in t        # the others are unaffected
    finally:
        d.chmod(0o755)
