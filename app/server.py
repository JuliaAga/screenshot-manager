"""Small private-network web app, with a durable, serial extraction worker."""
import base64
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import struct
import threading
import time
import urllib.parse
import uuid
import zipfile
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .extraction import ExtractionError, WaitingForModel, available_model, base_url, extract, normalize_item
from .store import Store
from .provider_config import public_settings, settings_update

LOG = logging.getLogger("screenshots")
STATIC = Path(__file__).parent / "static"
MAX_BODY = 32 * 1024 * 1024
IMAGE_NAME = re.compile(r"^[a-f0-9]{32}\.(png|jpg)$")
MUTATION = threading.RLock()


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def password_record(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ApiError("Use a password of 12–256 characters")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 300000).hex()
    return {"salt": salt, "digest": digest}


def password_matches(password, record):
    if not isinstance(password, str) or len(password) > 256:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(record["salt"]), 300000).hex()
    return hmac.compare_digest(digest, record["digest"])


def image_extension(raw):
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        width, height = struct.unpack(">II", raw[16:24])
        if not width or not height or width * height > 24000000:
            raise ApiError("Image is too large; resize to at most 24 megapixels")
        return "png"
    if raw.startswith(b"\xff\xd8\xff") and raw.endswith(b"\xff\xd9"):
        # Decode SOF dimensions without loading pixels on the Pi.
        pos = 2
        while pos + 4 < len(raw):
            if raw[pos] != 255:
                break
            while pos < len(raw) and raw[pos] == 255:
                pos += 1
            marker = raw[pos]
            pos += 1
            if marker == 0xDA:
                break
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            length = int.from_bytes(raw[pos:pos + 2], "big")
            if length < 2 or pos + length > len(raw):
                break
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                height, width = struct.unpack(">HH", raw[pos + 3:pos + 7])
                if not width or not height or width * height > 24000000:
                    raise ApiError("Image is too large; resize to at most 24 megapixels")
                return "jpg"
            pos += length
    raise ApiError("Use a valid PNG or JPEG screenshot")


def prepare_images(files):
    if not isinstance(files, list) or not 1 <= len(files) <= 10:
        raise ApiError("Choose between 1 and 10 screenshots")
    result = []
    total = 0
    for file in files:
        if not isinstance(file, dict) or not isinstance(file.get("data"), str):
            raise ApiError("Invalid upload")
        try:
            raw = base64.b64decode(file["data"], validate=True)
        except ValueError as exc:
            raise ApiError("Invalid image encoding") from exc
        total += len(raw)
        if not raw or len(raw) > 8 * 1024 * 1024 or total > 20 * 1024 * 1024:
            raise ApiError("Limit: 8 MB per image and 20 MB per batch")
        name = uuid.uuid4().hex + "." + image_extension(raw)
        result.append((name, raw))
    return result


class Application:
    def __init__(self, data_dir, worker=True):
        self.store = Store(data_dir)
        self.stop = threading.Event()
        self.login_attempts = {}
        self.shortcut_attempts = []
        self.worker = None
        if worker:
            self.worker = threading.Thread(target=self.work, daemon=True, name="extractor")
            self.worker.start()

    def process_one(self):
        with MUTATION, self.store.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE status='waiting' AND next_attempt<=? ORDER BY created LIMIT 1", (time.time(),)).fetchone()
            if not row:
                return False
            job = dict(row)
            job["images"] = json.loads(job["images"])
            db.execute("UPDATE jobs SET status='processing',message='Extracting useful details…',attempts=attempts+1 WHERE id=?", (job["id"],))
        try:
            result = extract(self.store.config(), job, self.store.images, self.store.categories())
            status, message, next_attempt = "ready", "Ready to review", 0
        except WaitingForModel as exc:
            result = []
            status, message = "waiting", str(exc)
            next_attempt = time.time() + min(900, 30 * 2 ** min(job["attempts"], 5))
        except ExtractionError as exc:
            result = []
            status, message, next_attempt = "failed", str(exc), 0
        except Exception:
            LOG.exception("Extraction failed")
            result = []
            status, message, next_attempt = "failed", "Unexpected extraction error. Retry or create manually.", 0
        with MUTATION, self.store.connect() as db:
            # A manual review, retry, restore, comment edit or discard invalidates in-flight work.
            current = db.execute("SELECT generation,status FROM jobs WHERE id=?", (job["id"],)).fetchone()
            setting = db.execute("SELECT value FROM config WHERE key='auto_approve'").fetchone()
            if (status == "ready" and result and setting and json.loads(setting["value"]) is True
                    and current and current["generation"] == job["generation"] and current["status"] == "processing"):
                now = time.time()
                for item in result:
                    data = normalize_item({**item, "comment": job["comment"]}, self.store.categories())
                    db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?)",
                               (uuid.uuid4().hex, now, now, 0, json.dumps(data), json.dumps(job["images"])))
                db.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
                return True
            db.execute("UPDATE jobs SET status=?,message=?,next_attempt=?,proposals=? WHERE id=? AND generation=? AND status='processing'",
                       (status, message, next_attempt, json.dumps(result), job["id"], job["generation"]))
        return True

    def work(self):
        while not self.stop.is_set():
            try:
                self.process_one()
            except Exception:
                LOG.exception("Queue worker error; will retry")
            self.stop.wait(2)

    def close(self):
        self.stop.set()


class Handler(BaseHTTPRequestHandler):
    server_version = "ScreenshotManager"

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        # Do not log URLs containing query strings, request bodies, or cookies.
        LOG.info("%s %s", getattr(self, "command", "HTTP"), urllib.parse.urlsplit(getattr(self, "path", "")).path)

    def send(self, status, body, content_type="application/json", cookie=None, disposition=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(body)

    def json_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApiError("Invalid request size") from exc
        if length <= 0 or length > MAX_BODY:
            raise ApiError("Request too large", 413)
        try:
            body = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError) as exc:
            raise ApiError("Invalid JSON") from exc
        if not isinstance(body, dict):
            raise ApiError("Expected a JSON object")
        return body

    def session(self, required=True):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
            sid = jar["session"].value if "session" in jar else ""
        except cookies.CookieError:
            sid = ""
        session = self.app.store.session(sid)
        if required and not session:
            raise ApiError("Please sign in", 401)
        return session

    def check_origin(self):
        # JSON + a custom header also prevents form-based cross-origin requests.
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host"):
            raise ApiError("Cross-origin request rejected", 403)
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise ApiError("Use application/json", 415)
        if self.headers.get("X-Requested-With") != "ScreenshotManager":
            raise ApiError("Missing request header", 403)

    def mutate_auth(self):
        self.check_origin()
        session = self.session()
        if not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"]):
            raise ApiError("Session expired; reload the page", 403)
        return session

    def do_GET(self):
        try:
            path = urllib.parse.urlsplit(self.path).path
            if path in ("/", "/app.js", "/style.css", "/icon.svg"):
                name = {"/": "index.html"}.get(path, path[1:])
                mime = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8", "svg": "image/svg+xml"}[name.rsplit(".", 1)[1]]
                self.send(200, (STATIC / name).read_bytes(), mime)
                return
            if path == "/api/session":
                session = self.session(False)
                self.send(200, {"setup": not bool(self.app.store.config().get("password")), "authenticated": bool(session),
                                "csrf": session["csrf"] if session else None})
                return
            self.session()
            if path == "/api/state":
                self.send(200, self.app.store.snapshot())
            elif path == "/api/settings":
                self.send(200, public_settings(self.app.store.config()))
            elif path == "/api/export":
                self.export()
            elif path.startswith("/images/"):
                name = path[len("/images/"):]
                if not IMAGE_NAME.fullmatch(name):
                    raise ApiError("Image not found", 404)
                with MUTATION:
                    snapshot = self.app.store.snapshot()
                    referenced = any(name in r["images"] for r in snapshot["jobs"] + snapshot["entries"])
                    file = self.app.store.images / name
                    if not referenced or not file.is_file():
                        raise ApiError("Image not found", 404)
                    self.send(200, file.read_bytes(), "image/png" if name.endswith("png") else "image/jpeg")
            else:
                raise ApiError("Not found", 404)
        except ApiError as exc:
            self.send(exc.status, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            LOG.exception("Request failed")
            self.send(500, {"error": "Something went wrong. Check the server log."})

    def do_POST(self):
        try:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/api/shortcut-upload":
                self.shortcut_upload()
                return
            if path in ("/api/setup", "/api/login"):
                self.check_origin()
                self.login(path, self.json_body())
                return
            session = self.mutate_auth()
            body = self.json_body()
            with MUTATION:
                result = self.route_post(path, body, session)
            self.send(200, result or {"ok": True})
        except ApiError as exc:
            self.send(exc.status, {"error": str(exc)})
        except ValueError as exc:
            self.send(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            LOG.exception("Request failed")
            self.send(500, {"error": "Something went wrong. Check the server log."})

    def shortcut_upload(self):
        # Bearer authentication is deliberately limited to this write-only endpoint.
        with MUTATION:
            digest = self.app.store.config().get("shortcut_token_hash", "")
            auth = self.headers.get("Authorization", "")
            if not digest or not auth.startswith("Bearer ") or not hmac.compare_digest(
                    hashlib.sha256(auth[7:].encode()).hexdigest(), digest):
                raise ApiError("Invalid or revoked Shortcut token", 401)
            now = time.time()
            self.app.shortcut_attempts = [t for t in self.app.shortcut_attempts if t > now - 60]
            if len(self.app.shortcut_attempts) >= 60:
                raise ApiError("Too many uploads; wait a minute and try again", 429)
            self.app.shortcut_attempts.append(now)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ApiError("Invalid request size") from exc
            if not 0 < length <= 8 * 1024 * 1024:
                raise ApiError("Limit: 8 MB per image", 413)
            if self.headers.get("Content-Type", "").split(";")[0] not in (
                    "image/png", "image/jpeg", "application/octet-stream"):
                raise ApiError("Send a PNG or JPEG file", 415)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ApiError("Incomplete image upload")
            result = self.route_post("/api/upload", {"files": [
                {"data": base64.b64encode(raw).decode()}]}, None)
        self.send(200, {**result, "message": "Sent to Later"})

    def login(self, path, body):
        now = time.time()
        with MUTATION:
            # Limit setup/login attempts before doing expensive password hashing.
            self.app.login_attempts = {ip: times for ip, times in self.app.login_attempts.items() if times and times[-1] > now - 600}
            attempts = [t for t in self.app.login_attempts.get(self.client_address[0], []) if t > now - 600]
            if len(attempts) >= 10:
                raise ApiError("Too many attempts. Try again in ten minutes.", 429)
            attempts.append(now)
            self.app.login_attempts[self.client_address[0]] = attempts
            config = self.app.store.config()
            if path == "/api/setup":
                if config.get("password"):
                    raise ApiError("Setup is already complete", 409)
                self.app.store.set_config({"password": password_record(body.get("password"))})
            elif not config.get("password") or not password_matches(body.get("password"), config["password"]):
                raise ApiError("Incorrect password", 401)
            self.app.login_attempts.pop(self.client_address[0], None)
            sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            with self.app.store.connect() as db:
                db.execute("INSERT INTO sessions VALUES (?,?,?)", (sid, csrf, now + 7 * 86400))
        secure = "; Secure" if os.environ.get("COOKIE_SECURE", "0") == "1" else ""
        self.send(200, {"ok": True, "csrf": csrf}, cookie=f"session={sid}; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800{secure}")

    def route_post(self, path, body, session):
        store = self.app.store
        if path == "/api/shortcut-token":
            action = body.get("action")
            if action not in ("create", "revoke"):
                raise ApiError("Choose create or revoke")
            token = secrets.token_urlsafe(32) if action == "create" else ""
            store.set_config({"shortcut_token_hash": hashlib.sha256(token.encode()).hexdigest() if token else ""})
            return {"ok": True, "token": token}
        elif path == "/api/logout":
            with store.connect() as db:
                db.execute("DELETE FROM sessions WHERE id=?", (session["id"],))
        elif path == "/api/settings":
            values = settings_update(store.config(), body, base_url)
            store.set_config(values)
            with store.connect() as db:
                db.execute("UPDATE jobs SET status='waiting', next_attempt=0, generation=generation+1, message='Settings changed; queued again' WHERE status IN ('waiting','failed','processing')")
        elif path == "/api/model-test":
            try:
                model = available_model(store.config())
                return {"ok": True, "model": model}
            except (WaitingForModel, ExtractionError) as exc:
                raise ApiError(str(exc)) from exc
        elif path == "/api/upload":
            files = prepare_images(body.get("files"))
            comment = body.get("comment", "")
            if not isinstance(comment, str) or len(comment) > 10000:
                raise ApiError("Comment too long")
            groups = [files] if body.get("grouped") else [[f] for f in files]
            written = []
            try:
                for name, raw in files:
                    (store.images / name).write_bytes(raw)
                    written.append(name)
                ids = []
                with store.connect() as db:
                    for index, group in enumerate(groups):
                        jid = uuid.uuid4().hex
                        ids.append(jid)
                        personal_comment = comment if body.get("grouped") else body["files"][index].get("comment", "") or comment
                        if not isinstance(personal_comment, str) or len(personal_comment) > 10000:
                            raise ApiError("Comment too long")
                        db.execute("INSERT INTO jobs(id,created,comment,images,status) VALUES (?,?,?,?, 'waiting')",
                                   (jid, time.time(), personal_comment, json.dumps([f[0] for f in group])))
                return {"ok": True, "ids": ids}
            except Exception:
                for name in written:
                    (store.images / name).unlink(missing_ok=True)
                raise
        elif path == "/api/job":
            with store.connect() as db:
                row = db.execute("SELECT * FROM jobs WHERE id=?", (body.get("id"),)).fetchone()
                if not row:
                    raise ApiError("Upload not found", 404)
                action = body.get("action")
                if action == "discard":
                    db.execute("DELETE FROM jobs WHERE id=?", (row["id"],))
                elif action == "retry":
                    db.execute("UPDATE jobs SET status='waiting',next_attempt=0,attempts=0,generation=generation+1,message='Queued again',proposals='[]' WHERE id=?", (row["id"],))
                elif action == "manual":
                    db.execute("UPDATE jobs SET status='ready',generation=generation+1,proposals='[]',message='Ready for a manual entry' WHERE id=?", (row["id"],))
                elif action == "comment":
                    comment = body.get("comment", "")
                    if not isinstance(comment, str) or len(comment) > 10000:
                        raise ApiError("Comment too long")
                    # Changes guide the next extraction; no stale proposal can be saved.
                    db.execute("UPDATE jobs SET comment=?,status='waiting',generation=generation+1,next_attempt=0,proposals='[]',message='Comment updated; queued again' WHERE id=?", (comment, row["id"]))
                else:
                    raise ApiError("Unknown upload action")
            store.cleanup_images()
        elif path == "/api/review":
            self.review(body)
        elif path == "/api/entry":
            self.entry(body)
        elif path == "/api/category":
            name = body.get("name", "").strip()
            if not name or len(name) > 80:
                raise ApiError("Use a category name of 1–80 characters")
            old = body.get("old")
            with store.connect() as db:
                if db.execute("SELECT 1 FROM categories WHERE lower(name)=lower(?)", (name,)).fetchone():
                    raise ApiError("Category already exists")
                if old and not db.execute("SELECT 1 FROM categories WHERE name=?", (old,)).fetchone():
                    raise ApiError("Category not found", 404)
                db.execute("INSERT INTO categories VALUES (?)", (name,))
                if old:
                    for row in db.execute("SELECT id,data FROM entries").fetchall():
                        data = json.loads(row["data"])
                        data["categories"] = [name if c == old else c for c in data["categories"]]
                        db.execute("UPDATE entries SET data=? WHERE id=?", (json.dumps(data), row["id"]))
                    for row in db.execute("SELECT id,proposals FROM jobs").fetchall():
                        proposals = json.loads(row["proposals"])
                        for p in proposals:
                            p["categories"] = [name if c == old else c for c in p["categories"]]
                        db.execute("UPDATE jobs SET proposals=?,generation=generation+1,next_attempt=0,status=CASE WHEN status='processing' THEN 'waiting' ELSE status END WHERE id=?", (json.dumps(proposals), row["id"]))
                    db.execute("DELETE FROM categories WHERE name=?", (old,))
        elif path == "/api/restore":
            self.restore(body)
        else:
            raise ApiError("Not found", 404)

    def review(self, body):
        store = self.app.store
        raw_items = body.get("items", [])
        if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 30:
            raise ApiError("Select at least one entry")
        categories = store.categories()
        prepared = [(normalize_item(i.get("data"), categories), i.get("merge_id")) for i in raw_items if isinstance(i, dict)]
        if len(prepared) != len(raw_items):
            raise ApiError("Invalid review")
        with store.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (body.get("id"),)).fetchone()
            if not row or row["status"] != "ready":
                raise ApiError("Upload is no longer ready. Reload the Inbox.", 409)
            if body.get("generation") != row["generation"]:
                raise ApiError("This upload changed. Reload before saving.", 409)
            images = json.loads(row["images"]) if body.get("keep_images", True) else []
            for data, merge in prepared:
                now = time.time()
                if merge:
                    existing = db.execute("SELECT * FROM entries WHERE id=?", (merge,)).fetchone()
                    if not existing:
                        raise ApiError("Duplicate entry no longer exists", 409)
                    previous = json.loads(existing["data"])
                    # Preserve original comments; append the newly submitted personal comment.
                    old_comment, new_comment = previous.get("comment", ""), data.get("comment", "")
                    data["comment"] = old_comment if new_comment == old_comment or not new_comment else "\n\n".join(filter(None, [old_comment, new_comment]))
                    combined = list(dict.fromkeys(json.loads(existing["images"]) + images))
                    db.execute("UPDATE entries SET data=?,images=?,updated=? WHERE id=?", (json.dumps(data), json.dumps(combined), now, merge))
                else:
                    db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?)", (uuid.uuid4().hex, now, now, 0, json.dumps(data), json.dumps(images)))
            db.execute("DELETE FROM jobs WHERE id=?", (row["id"],))
        store.cleanup_images()

    def entry(self, body):
        store = self.app.store
        eid, action = body.get("id"), body.get("action")
        with store.connect() as db:
            existing = db.execute("SELECT * FROM entries WHERE id=?", (eid,)).fetchone() if eid else None
            if eid and not existing:
                raise ApiError("Entry not found", 404)
            if action == "save":
                data = normalize_item(body.get("data"), store.categories())
                now = time.time()
                if existing:
                    db.execute("UPDATE entries SET data=?,updated=? WHERE id=?", (json.dumps(data), now, eid))
                else:
                    db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?)", (uuid.uuid4().hex, now, now, 0, json.dumps(data), "[]"))
            elif existing and action == "archive":
                db.execute("UPDATE entries SET archived=?,updated=? WHERE id=?", (int(bool(body.get("archived"))), time.time(), eid))
            elif existing and action == "delete":
                db.execute("DELETE FROM entries WHERE id=?", (eid,))
            elif existing and action == "remove_images":
                db.execute("UPDATE entries SET images='[]',updated=? WHERE id=?", (time.time(), eid))
            else:
                raise ApiError("Unknown entry action")
        store.cleanup_images()

    def export(self):
        with MUTATION:
            state = self.app.store.snapshot()
            manifest = {"format": "screenshot-manager", "version": 1, "exported": time.time(), **state}
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("collection.json", json.dumps(manifest, ensure_ascii=False))
                names = {i for obj in state["entries"] + state["jobs"] for i in obj["images"]}
                for name in sorted(names):
                    archive.write(self.app.store.images / name, "images/" + name)
            self.send(200, buffer.getvalue(), "application/zip", disposition='attachment; filename="screenshot-manager-backup.zip"')

    def restore(self, body):
        if body.get("confirm") != "REPLACE":
            raise ApiError("Confirm replacement of this collection")
        try:
            raw = base64.b64decode(body.get("data", ""), validate=True)
            archive = zipfile.ZipFile(io.BytesIO(raw))
        except (ValueError, zipfile.BadZipFile) as exc:
            raise ApiError("Invalid backup") from exc
        with archive:
            info = archive.infolist()
            if len(info) > 5000 or sum(i.file_size for i in info) > 256 * 1024 * 1024:
                raise ApiError("Backup is too large for browser restore")
            if len({i.filename for i in info}) != len(info):
                raise ApiError("Duplicate backup paths")
            if any(i.filename != "collection.json" and not (i.filename.startswith("images/") and IMAGE_NAME.fullmatch(i.filename[7:])) for i in info):
                raise ApiError("Unexpected backup file")
            try:
                manifest = json.loads(archive.read("collection.json"))
                if manifest.get("format") != "screenshot-manager" or manifest.get("version") != 1:
                    raise ValueError("Unsupported backup format")
                categories, entries, jobs = manifest["categories"], manifest["entries"], manifest["jobs"]
                if not isinstance(categories, list) or not categories or len(categories) > 100 or any(not isinstance(c, str) or not c.strip() or len(c) > 80 for c in categories) or len(set(categories)) != len(categories):
                    raise ValueError("Invalid categories")
                if not isinstance(entries, list) or not isinstance(jobs, list) or len(entries) + len(jobs) > 10000:
                    raise ValueError("Too many entries")
                restored_entries, restored_jobs, images = [], [], {}
                for obj in entries + jobs:
                    if not isinstance(obj, dict) or not isinstance(obj.get("images"), list) or len(obj["images"]) > 100:
                        raise ValueError("Invalid image references")
                    for name in obj["images"]:
                        if not isinstance(name, str) or not IMAGE_NAME.fullmatch(name):
                            raise ValueError("Invalid image name")
                        if name not in images:
                            image = archive.read("images/" + name)
                            if len(image) > 8 * 1024 * 1024 or image_extension(image) != name.rsplit(".", 1)[1]:
                                raise ValueError("Invalid image")
                            images[name] = image
                for entry in entries:
                    restored_entries.append((uuid.uuid4().hex, float(entry["created"]), time.time(), int(bool(entry["archived"])),
                                             json.dumps(normalize_item(entry["data"], categories)), json.dumps(entry["images"])))
                for job in jobs:
                    comment = job["comment"]
                    if not isinstance(comment, str) or len(comment) > 10000:
                        raise ValueError("Invalid upload comment")
                    proposals = [normalize_item(p, categories, comment) for p in job["proposals"]]
                    if len(proposals) > 30 or not job["images"]:
                        raise ValueError("Invalid upload")
                    status = "ready" if job["status"] == "ready" else "waiting"
                    restored_jobs.append((uuid.uuid4().hex, float(job["created"]), comment, json.dumps(job["images"]), status, json.dumps(proposals)))
            except (KeyError, ValueError, TypeError, OverflowError, struct.error) as exc:
                raise ApiError("Backup validation failed; your current collection was not replaced") from exc
        # Use fresh image IDs so failed restores cannot overwrite existing files.
        renamed = {old: uuid.uuid4().hex + "." + old.rsplit(".", 1)[1] for old in images}
        written = []
        try:
            for old, image in images.items():
                name = renamed[old]
                (self.app.store.images / name).write_bytes(image)
                written.append(name)
            with self.app.store.connect() as db:
                db.execute("DELETE FROM entries")
                db.execute("DELETE FROM jobs")
                db.execute("DELETE FROM categories")
                db.executemany("INSERT INTO categories VALUES (?)", [(c,) for c in categories])
                for e in restored_entries:
                    e = (*e[:-1], json.dumps([renamed[n] for n in json.loads(e[-1])]))
                    db.execute("INSERT INTO entries VALUES (?,?,?,?,?,?)", e)
                for j in restored_jobs:
                    j = (*j[:3], json.dumps([renamed[n] for n in json.loads(j[3])]), *j[4:])
                    db.execute("INSERT INTO jobs(id,created,comment,images,status,proposals) VALUES (?,?,?,?,?,?)", j)
        except Exception:
            for name in written:
                (self.app.store.images / name).unlink(missing_ok=True)
            raise
        self.app.store.cleanup_images()


def make_server(host="127.0.0.1", port=8787, data_dir="data", worker=True):
    server = ThreadingHTTPServer((host, port), Handler)
    server.app = Application(data_dir, worker)
    return server


def main():
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    server = make_server(os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "8787")), os.environ.get("DATA_DIR", "data"))
    LOG.info("Screenshot Manager listening on %s:%s", *server.server_address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.app.close()
        server.server_close()


if __name__ == "__main__":
    main()
