import base64
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
import zipfile
from unittest.mock import patch

from app.extraction import ExtractionError, WaitingForModel, available_model, normalize_item
from app.server import Application, make_server

# A real 1x1 PNG; upload validation is exercised without third-party libraries.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9WQAAAAASUVORK5CYII=")


def entry(title="Dune", comment="My note"):
    return {"title": title, "categories": ["Books"], "tags": ["science fiction"], "link": "", "summary": "A book to read",
            "source_text": "Dune — Frank Herbert", "comment": comment, "details": [{"label": "Author", "value": "Frank Herbert"}]}


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = make_server("127.0.0.1", 0, self.tmp.name, worker=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.cookie = ""
        self.csrf = ""
        self.call("setup", {"password": "test-password-long"})

    def tearDown(self):
        self.server.shutdown()
        self.server.app.close()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def call(self, path, body=None, headers=None, raw=False):
        request_headers = {"Cookie": self.cookie}
        if body is not None:
            request_headers.update({"Content-Type": "application/json", "X-Requested-With": "ScreenshotManager", "X-CSRF-Token": self.csrf})
        request_headers.update(headers or {})
        req = urllib.request.Request(self.url + "/api/" + path,
                                     data=json.dumps(body).encode() if body is not None else None, headers=request_headers)
        try:
            with urllib.request.urlopen(req) as response:
                if response.headers.get("Set-Cookie"):
                    self.cookie = response.headers["Set-Cookie"].split(";")[0]
                payload = response.read()
                data = payload if raw else json.loads(payload)
                if not raw and "csrf" in data:
                    self.csrf = data["csrf"]
                return response.status, data
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read())
            finally:
                exc.close()

    def upload(self, grouped=False):
        status, data = self.call("upload", {"files": [{"data": base64.b64encode(PNG).decode(), "comment": "First"},
                                                     {"data": base64.b64encode(PNG).decode(), "comment": "Second"}],
                                           "grouped": grouped, "comment": "Grouped"})
        self.assertEqual(status, 200, data)
        return data["ids"]

    def ready(self, jid):
        self.call("job", {"id": jid, "action": "manual"})
        return next(j for j in self.call("state")[1]["jobs"] if j["id"] == jid)

    def save_job(self, jid, data=None, merge=None, keep=True):
        job = self.ready(jid)
        return self.call("review", {"id": jid, "generation": job["generation"], "items": [{"data": data or entry(), "merge_id": merge}], "keep_images": keep})

    def test_auth_csrf_and_setup_lock(self):
        cookie = self.cookie
        self.cookie = ""
        self.assertEqual(self.call("state")[0], 401)
        self.cookie = cookie
        self.assertEqual(self.call("setup", {"password": "other-password-long"})[0], 409)
        self.assertEqual(self.call("entry", {"action": "save", "data": entry()}, {"X-CSRF-Token": "wrong"})[0], 403)
        self.assertEqual(self.call("entry", {"action": "save", "data": entry()}, {"Origin": "https://evil.example"})[0], 403)

    def test_separate_and_grouped_comments(self):
        self.assertEqual(len(self.upload()), 2)
        jobs = self.call("state")[1]["jobs"]
        self.assertEqual({j["comment"] for j in jobs}, {"First", "Second"})
        grouped = self.upload(True)[0]
        job = next(j for j in self.call("state")[1]["jobs"] if j["id"] == grouped)
        self.assertEqual(job["comment"], "Grouped")
        self.assertEqual(len(job["images"]), 2)

    def test_shortcut_upload_token_scope_rotation_and_revocation(self):
        self.assertFalse(self.call("settings")[1]["has_shortcut_token"])
        self.assertEqual(self.call("shortcut-token", {"action": "create"}, {"X-CSRF-Token": "wrong"})[0], 403)
        token = self.call("shortcut-token", {"action": "create"})[1]["token"]
        self.assertNotIn(token, json.dumps(self.server.app.store.config()))
        self.assertNotIn("shortcut_token_hash", self.call("settings")[1])
        self.cookie = ""

        def send(raw=PNG, bearer=token, mime="image/png"):
            req = urllib.request.Request(self.url + "/api/shortcut-upload", data=raw,
                headers={"Authorization": "Bearer " + bearer, "Content-Type": mime})
            try:
                with urllib.request.urlopen(req) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as exc:
                with exc:
                    return exc.code, json.load(exc)

        self.assertEqual(send(bearer="wrong")[0], 401)
        self.assertEqual(send(raw=b"invalid")[0], 400)
        self.assertEqual(send(mime="text/plain")[0], 415)
        self.assertEqual(send(raw=PNG, mime="application/octet-stream")[0], 200)
        status, result = send()
        self.assertEqual(status, 200)
        self.assertEqual(len(result["ids"]), 1)
        self.assertEqual(self.call("state", headers={"Authorization": "Bearer " + token})[0], 401)
        self.assertEqual(self.call("settings", headers={"Authorization": "Bearer " + token})[0], 401)
        self.call("login", {"password": "test-password-long"})
        self.assertEqual(len(self.call("state")[1]["jobs"]), 2)
        backup = self.call("export", raw=True)[1]
        with zipfile.ZipFile(io.BytesIO(backup)) as archive:
            self.assertNotIn(token.encode(), archive.read("collection.json"))
            self.assertNotIn(b"shortcut_token_hash", archive.read("collection.json"))
        self.call("shortcut-token", {"action": "create"})
        self.assertEqual(send()[0], 401)
        token2 = self.call("shortcut-token", {"action": "create"})[1]["token"]
        self.call("shortcut-token", {"action": "revoke"})
        self.assertEqual(send(bearer=token2)[0], 401)

    def test_shortcut_upload_rate_limit_and_size(self):
        token = self.call("shortcut-token", {"action": "create"})[1]["token"]
        import time
        self.server.app.shortcut_attempts = [time.time()] * 60
        req = urllib.request.Request(self.url + "/api/shortcut-upload", data=PNG,
            headers={"Authorization": "Bearer " + token, "Content-Type": "image/png"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req)
        self.assertEqual(caught.exception.code, 429)
        caught.exception.close()
        self.server.app.shortcut_attempts = []
        req = urllib.request.Request(self.url + "/api/shortcut-upload", data=PNG,
            headers={"Authorization": "Bearer " + token, "Content-Type": "image/png",
                     "Content-Length": str(8 * 1024 * 1024 + 1)})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req)
        self.assertEqual(caught.exception.code, 413)
        caught.exception.close()

    def test_invalid_upload_atomic(self):
        status, _ = self.call("upload", {"files": [{"data": base64.b64encode(PNG).decode()}, {"data": "not-an-image"}]})
        self.assertEqual(status, 400)
        self.assertEqual(self.call("state")[1]["jobs"], [])
        self.assertEqual(list(self.server.app.store.images.iterdir()), [])

    def test_review_archive_restore_delete(self):
        jid = self.upload(True)[0]
        self.assertEqual(self.save_job(jid)[0], 200)
        state = self.call("state")[1]
        self.assertEqual(state["jobs"], [])
        saved = state["entries"][0]
        self.assertEqual(len(saved["images"]), 2)
        self.call("entry", {"id": saved["id"], "action": "archive", "archived": True})
        self.assertTrue(self.call("state")[1]["entries"][0]["archived"])
        self.call("entry", {"id": saved["id"], "action": "archive", "archived": False})
        self.call("entry", {"id": saved["id"], "action": "delete"})
        self.assertEqual(self.call("state")[1]["entries"], [])
        self.assertEqual(list(self.server.app.store.images.iterdir()), [])

    def test_merge_preserves_comment_and_image_references(self):
        self.call("entry", {"action": "save", "data": entry(comment="Original comment")})
        eid = self.call("state")[1]["entries"][0]["id"]
        jid = self.upload(True)[0]
        self.assertEqual(self.save_job(jid, entry(comment="New comment"), eid)[0], 200)
        saved = self.call("state")[1]["entries"][0]
        self.assertEqual(saved["data"]["comment"], "Original comment\n\nNew comment")
        self.assertEqual(len(saved["images"]), 2)

    def test_atomic_review_rejects_invalid_second_proposal(self):
        jid = self.upload(True)[0]
        job = self.ready(jid)
        invalid = entry(); invalid["categories"] = ["Not a category"]
        status, _ = self.call("review", {"id": jid, "generation": job["generation"], "items": [{"data": entry()}, {"data": invalid}]})
        self.assertEqual(status, 400)
        self.assertEqual(self.call("state")[1]["entries"], [])
        self.assertEqual(len(self.call("state")[1]["jobs"]), 1)

    def test_stale_review_cannot_overwrite(self):
        jid = self.upload(True)[0]; job = self.ready(jid)
        self.call("job", {"id": jid, "action": "retry"})
        self.ready(jid)
        status, _ = self.call("review", {"id": jid, "generation": job["generation"], "items": [{"data": entry()}]})
        self.assertEqual(status, 409)

    def test_backup_restore_excludes_credentials_and_restores_pending(self):
        self.server.app.store.set_config({"token": "private-api-token", "url": "http://127.0.0.1:1234", "model": "test"})
        self.save_job(self.upload(True)[0])
        self.upload()
        status, backup = self.call("export", raw=True)
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(backup)) as z:
            manifest = z.read("collection.json")
            self.assertNotIn(b"private-api-token", manifest)
            self.assertNotIn(b"password", manifest)
        eid = self.call("state")[1]["entries"][0]["id"]
        self.call("entry", {"id": eid, "action": "delete"})
        status, result = self.call("restore", {"confirm": "REPLACE", "data": base64.b64encode(backup).decode()})
        self.assertEqual(status, 200, result)
        state = self.call("state")[1]
        self.assertEqual(len(state["entries"]), 1)
        self.assertEqual(len(state["jobs"]), 2)
        self.assertEqual(self.server.app.store.config()["token"], "private-api-token")
        self.assertEqual(len(list(self.server.app.store.images.iterdir())), 4)

    def test_malicious_backup_does_not_replace_collection(self):
        self.call("entry", {"action": "save", "data": entry()})
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../../escape", "bad")
        self.assertEqual(self.call("restore", {"confirm": "REPLACE", "data": base64.b64encode(buf.getvalue()).decode()})[0], 400)
        self.assertEqual(len(self.call("state")[1]["entries"]), 1)

    def test_category_rename_updates_entries(self):
        self.call("entry", {"action": "save", "data": entry()})
        jid = self.upload(True)[0]
        with self.server.app.store.connect() as db:
            db.execute("UPDATE jobs SET status='processing' WHERE id=?", (jid,))
        self.assertEqual(self.call("category", {"old": "Books", "name": "Reading"})[0], 200)
        state = self.call("state")[1]
        self.assertEqual(state["entries"][0]["data"]["categories"], ["Reading"])
        self.assertEqual(state["jobs"][0]["status"], "waiting")
        restarted = Application(self.tmp.name, worker=False)
        self.assertNotIn("Books", restarted.store.categories())
        self.assertIn("Reading", restarted.store.categories())
        restarted.close()

    def test_model_token_never_returned(self):
        self.call("settings", {"url": "http://127.0.0.1:1234", "model": "test", "token": "private", "mode": "auto", "timeout": 180})
        result = self.call("settings")[1]
        self.assertTrue(result["has_token"])
        self.assertNotIn("token", result)

    def test_auto_approve_setting_is_optional_and_persistent(self):
        self.assertFalse(self.call("settings")[1]["auto_approve"])
        self.assertEqual(self.call("settings", {"auto_approve": True})[0], 200)
        self.assertTrue(self.call("settings")[1]["auto_approve"])
        self.assertEqual(self.call("settings", {"auto_approve": "yes"})[0], 400)
        self.assertEqual(self.call("settings", {"auto_approve": False})[0], 200)
        self.assertFalse(self.call("settings")[1]["auto_approve"])

    def test_cloud_settings_and_export_keep_keys_private(self):
        status, _ = self.call("settings", {"provider": "google", "model": "gemini-2.5-flash", "token": "cloud-secret"})
        self.assertEqual(status, 400)
        status, result = self.call("settings", {"provider": "google", "model": "gemini-2.5-flash", "token": "cloud-secret", "external_confirmed": True})
        self.assertEqual(status, 200, result)
        settings = self.call("settings")[1]
        self.assertEqual(settings["provider"], "google")
        self.assertTrue(settings["providers"]["google"]["has_token"])
        self.assertNotIn("cloud-secret", json.dumps(settings))
        with zipfile.ZipFile(io.BytesIO(self.call("export", raw=True)[1])) as archive:
            self.assertNotIn(b"cloud-secret", archive.read("collection.json"))


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Application(self.tmp.name, worker=False)
        self.jid = uuid.uuid4().hex
        with self.app.store.connect() as db:
            db.execute("INSERT INTO jobs(id,created,comment,images,status) VALUES (?,0,'','[]','waiting')", (self.jid,))

    def tearDown(self):
        self.app.close(); self.tmp.cleanup()

    def test_offline_backoff_is_durable(self):
        with patch("app.server.extract", side_effect=WaitingForModel("Offline")):
            self.app.process_one()
        job = self.app.store.snapshot()["jobs"][0]
        self.assertEqual(job["status"], "waiting")
        self.assertGreater(job["next_attempt"], 0)
        self.assertEqual(job["attempts"], 1)
        restarted = Application(self.tmp.name, worker=False)
        self.assertEqual(restarted.store.snapshot()["jobs"][0]["status"], "waiting")
        restarted.close()

    def test_restart_requeues_processing(self):
        with self.app.store.connect() as db:
            db.execute("UPDATE jobs SET status='processing'")
        restarted = Application(self.tmp.name, worker=False)
        self.assertEqual(restarted.store.snapshot()["jobs"][0]["status"], "waiting")
        restarted.close()

    def test_discard_during_processing_does_not_resurrect(self):
        def fake(*args):
            with self.app.store.connect() as db:
                db.execute("DELETE FROM jobs WHERE id=?", (self.jid,))
            return [entry()]
        with patch("app.server.extract", side_effect=fake):
            self.app.process_one()
        self.assertEqual(self.app.store.snapshot()["jobs"], [])

    def test_auto_approve_saves_all_entries_and_preserves_comment_and_images(self):
        self.app.store.set_config({"auto_approve": True})
        with self.app.store.connect() as db:
            db.execute("UPDATE jobs SET comment='Personal note',images=? WHERE id=?", (json.dumps(["saved.png"]), self.jid))
        with patch("app.server.extract", return_value=[entry(), entry("Another book")]):
            self.app.process_one()
        state = self.app.store.snapshot()
        self.assertEqual(state["jobs"], [])
        self.assertEqual(len(state["entries"]), 2)
        self.assertTrue(all(e["data"]["comment"] == "Personal note" and e["images"] == ["saved.png"] for e in state["entries"]))

    def test_auto_approve_does_not_save_stale_result(self):
        self.app.store.set_config({"auto_approve": True})
        def fake(*args):
            with self.app.store.connect() as db:
                db.execute("UPDATE jobs SET generation=generation+1,status='waiting' WHERE id=?", (self.jid,))
            return [entry()]
        with patch("app.server.extract", side_effect=fake):
            self.app.process_one()
        self.assertEqual(self.app.store.snapshot()["entries"], [])
        self.assertEqual(self.app.store.snapshot()["jobs"][0]["status"], "waiting")

    def test_success_waits_for_confirmation(self):
        with patch("app.server.extract", return_value=[entry()]):
            self.app.process_one()
        state = self.app.store.snapshot()
        self.assertEqual(state["jobs"][0]["status"], "ready")
        self.assertEqual(state["entries"], [])

    def test_invalid_model_output_fails_without_losing_upload(self):
        with patch("app.server.extract", side_effect=ExtractionError("Invalid output")):
            self.app.process_one()
        self.assertEqual(self.app.store.snapshot()["jobs"][0]["status"], "failed")


class ModelTests(unittest.TestCase):
    def test_only_loaded_instance_selected(self):
        metadata = {"models": [{"key": "qwen/test", "capabilities": {"vision": True}, "loaded_instances": []}]}
        with patch("app.extraction.request", return_value=metadata) as call:
            with self.assertRaises(WaitingForModel):
                available_model({"url": "http://model:1234", "model": "qwen/test"})
            self.assertEqual(call.call_args.args[1], "/api/v1/models")
        metadata["models"][0]["loaded_instances"] = [{"id": "custom-instance"}]
        with patch("app.extraction.request", return_value=metadata):
            self.assertEqual(available_model({"url": "http://model:1234", "model": "qwen/test"})["id"], "custom-instance")

    def test_unsafe_link_rejected(self):
        data = entry(); data["link"] = "javascript:alert(1)"
        with self.assertRaises(ValueError):
            normalize_item(data, ["Books"])


if __name__ == "__main__":
    unittest.main()
