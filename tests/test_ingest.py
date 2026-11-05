import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from conftest import PASSWORD, TOKEN, csrf_of

AUTH = {"Authorization": f"Bearer {TOKEN}"}


def rec(**kw):
    d = {"schema_version": 1, "id": "2026-11-04-node-a-llama-3.3-70b-bench", "date": "2026-11-04",
         "model": "llama-3.3-70b", "node": "node-a", "topology": "single", "label": "llama-3.3-70b-bench",
         "validity": "ok", "bench": {"single_stream_tok_s": 5.32, "concurrent": {"num_prompts": 20, "output_tok_s": 89.36}},
         "source": "test"}
    d.update(kw)
    return d


def post(client, body, headers=AUTH):
    data = body if isinstance(body, (bytes, str)) else json.dumps(body)
    return client.post("/api/results", content=data, headers={**headers, "Content-Type": "application/json"})


def test_valid_record_is_stored_and_shown_as_pending(client, env):
    r = post(client, rec())
    assert r.status_code == 201 and r.json() == {"id": rec()["id"], "status": "pending"}
    assert (env.inbox_dir / f"{rec()['id']}.json").is_file()


def test_no_token_or_wrong_token_is_401_and_nothing_is_written(client, env):
    assert post(client, rec(), headers={}).status_code == 401
    assert post(client, rec(), headers={"Authorization": "Bearer " + "x" * 40}).status_code == 401
    assert post(client, rec(), headers={"Authorization": TOKEN}).status_code == 401     # no "Bearer "
    assert list(env.inbox_dir.iterdir()) == []


def test_upload_is_off_when_no_token_is_configured(env):
    off = Settings(catalogue_dir=env.catalogue_dir, hf_dir=env.hf_dir, data_dir=env.data_dir, secret_key=env.secret_key,
                   admin_hash=env.admin_hash, inbox_dir=env.inbox_dir, ingest_token="")
    c = TestClient(create_app(off), follow_redirects=False)
    assert post(c, rec()).status_code == 404 and list(env.inbox_dir.iterdir()) == []


def test_session_login_does_not_authorise_upload(authed, env):
    # a browser session is not a bearer token: no cookie, no CSRF path into this endpoint
    r = authed.post("/api/results", content=json.dumps(rec()), headers={"Content-Type": "application/json"})
    assert r.status_code == 401


def test_five_wrong_tokens_lock_the_address(client):
    for _ in range(5):
        assert post(client, rec(), headers={"Authorization": "Bearer " + "x" * 40}).status_code == 401
    assert post(client, rec()).status_code == 429          # even the right token waits out the lock


def test_schema_violations_are_422(client, env):
    for bad in (rec(node="bad node!"), rec(validity="great"), rec(schema_version=2), {"id": "x"},
                rec(bench={"single_stream_tok_s": -1}), rec(extra_field=1)):
        r = post(client, bad)
        assert r.status_code == 422, bad
    assert post(client, b"{nope").status_code == 422
    assert post(client, "[1,2]").status_code == 422
    assert list(env.inbox_dir.iterdir()) == []


def test_unknown_model_is_422(client):
    r = post(client, rec(model="not-a-model", id="2026-11-04-node-a-x"))
    assert r.status_code == 422 and "library.yaml" in r.json()["error"]


def test_hostile_ids_cannot_become_paths(client, env, tmp_path):
    for evil in ("../escape", "a/b", ".hidden", "..", "x" * 200, "has space", "nul\x00byte", "a\\b"):
        r = post(client, rec(id=evil))
        assert r.status_code == 422, evil
    assert list(env.inbox_dir.iterdir()) == [] and not (tmp_path / "escape.json").exists()


def test_existing_id_is_409_in_inbox_and_in_committed_results(client, env):
    assert post(client, rec()).status_code == 201
    first = (env.inbox_dir / f"{rec()['id']}.json").read_text()
    assert post(client, rec(bench={"single_stream_tok_s": 999})).status_code == 409
    assert (env.inbox_dir / f"{rec()['id']}.json").read_text() == first              # not overwritten
    committed = json.loads(next((env.catalogue_dir / "results").glob("2026-11-03-node-a-clock-fix.json")).read_text())
    assert post(client, committed).status_code == 409


def test_oversize_is_413_and_bad_length_is_400(client, env):
    big = rec(source="x" * 70000)
    assert post(client, big).status_code == 413
    r = client.post("/api/results", content=b"{}", headers={**AUTH, "Content-Length": "abc"})
    assert r.status_code in (400, 422)       # the server may reject the malformed header before the app does
    assert list(env.inbox_dir.iterdir()) == []


def test_unwritable_inbox_is_503_not_a_500(client, env):
    env.inbox_dir.rmdir()
    assert post(client, rec()).status_code == 503


def test_pending_results_show_in_the_ui_until_a_committed_copy_exists(authed, env):
    assert post(authed, rec()).status_code == 201
    page = authed.get("/m/llama-3.3-70b").text
    assert "pending" in page
    assert "1 result uploaded, not committed" in authed.get("/").text
    # the committed copy supersedes the inbox copy
    (env.catalogue_dir / "results" / f"{rec()['id']}.json").write_text(json.dumps(rec()))
    page = authed.get("/m/llama-3.3-70b").text
    assert "uploaded, not committed" not in authed.get("/").text and page.count(rec()["label"]) == 1


def test_bad_file_in_inbox_is_reported_not_fatal(authed, env):
    (env.inbox_dir / "junk.json").write_text("{nope")
    page = authed.get("/")
    assert page.status_code == 200 and "inbox/junk.json" in page.text


def test_short_token_refuses_to_start(env):
    import pytest
    bad = Settings(catalogue_dir=env.catalogue_dir, hf_dir=env.hf_dir, data_dir=env.data_dir, secret_key=env.secret_key,
                   admin_hash=env.admin_hash, ingest_token="short")
    with pytest.raises(SystemExit):
        create_app(bad)


def test_store_itself_refuses_to_overwrite_even_if_parse_is_bypassed(env):
    # two uploads of the same id can both pass the "does it exist" check; the file open must still refuse
    import pytest
    from app import ingest
    ingest.store(env.inbox_dir, rec())
    first = (env.inbox_dir / f"{rec()['id']}.json").read_text()
    with pytest.raises(ingest.Rejected) as e:
        ingest.store(env.inbox_dir, rec(bench={"single_stream_tok_s": 999}))
    assert e.value.status == 409
    assert (env.inbox_dir / f"{rec()['id']}.json").read_text() == first
