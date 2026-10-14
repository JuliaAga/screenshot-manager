import base64
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from app.extraction import SCHEMA, ExtractionError, WaitingForModel, available_model, base_url, extract, request
from app.provider_config import public_settings, selected_config, settings_update

ITEM = {"title": "Arrival", "categories": ["Movies"], "tags": [], "link": "", "summary": "A film",
        "source_text": "Arrival (2016)", "details": [{"label": "Year", "value": "2016"}]}


class ProfileTests(unittest.TestCase):
    def save(self, config, name, token="", model=None, **extra):
        body = {"provider": name, "token": token, "external_confirmed": True, **extra}
        if model is not None:
            body["model"] = model
        return config | settings_update(config, body, base_url)

    def test_legacy_installation_remains_local(self):
        old = {"url": "http://pi-model:1234", "model": "my-loaded-model", "token": "local-token", "timeout": 180}
        active = selected_config(old)
        self.assertEqual(active["provider"], "lmstudio")
        self.assertEqual(active["model"], "my-loaded-model")
        self.assertEqual(active["token"], "local-token")

    def test_provider_keys_are_isolated_and_retained(self):
        old = {"url": "http://model:1234", "model": "qwen", "token": "local-key"}
        config = self.save(old, "openrouter", "router-key")
        config = self.save(config, "google", "google-key")
        config = self.save(config, "huggingface", "hf-key", "org/vision-model")
        config = self.save(config, "lmstudio")
        self.assertEqual(selected_config(config)["token"], "local-key")
        config = self.save(config, "google")
        self.assertEqual(selected_config(config)["token"], "google-key")
        self.assertEqual(config["providers"]["openrouter"]["token"], "router-key")
        public = json.dumps(public_settings(config))
        for secret in ("local-key", "router-key", "google-key", "hf-key"):
            self.assertNotIn(secret, public)

    def test_no_reuse_of_local_key_for_cloud(self):
        with self.assertRaisesRegex(ValueError, "required"):
            self.save({"token": "local-key"}, "openrouter")

    def test_cloud_requires_explicit_confirmation(self):
        with self.assertRaisesRegex(ValueError, "Confirm"):
            settings_update({}, {"provider": "google", "token": "key"}, base_url)

    def test_paid_openrouter_and_google_models_rejected(self):
        for provider, model in (("openrouter", "vendor/paid-model"), ("google", "gemini-paid-preview")):
            with self.subTest(provider=provider), self.assertRaises(ValueError):
                self.save({}, provider, "key", model)

    def test_clearing_local_token_does_not_clear_other_keys(self):
        config = self.save({"token": "local-key"}, "google", "google-key")
        config = self.save(config, "lmstudio", clear_token=True)
        self.assertEqual(selected_config(config)["token"], "")
        self.assertEqual(config["providers"]["google"]["token"], "google-key")

    def test_cloud_key_can_be_removed_without_removing_other_profiles(self):
        config = self.save({"token": "local-key"}, "google", "google-key")
        config = self.save(config, "google", clear_token=True)
        self.assertEqual(selected_config(config)["token"], "")
        self.assertEqual(config["providers"]["lmstudio"]["token"], "local-key")
        with self.assertRaises(ExtractionError):
            available_model(selected_config(config))


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "sample.png").write_bytes(b"synthetic-image-bytes")
        self.job = {"images": ["sample.png"], "comment": "My personal comment"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_google_native_image_and_schema_payload(self):
        def fake(config, path, body=None, **kwargs):
            if body is None:
                return {"supportedGenerationMethods": ["generateContent"]}
            self.assertTrue(path.endswith(":generateContent"))
            self.assertEqual(body["generationConfig"]["responseJsonSchema"], SCHEMA)
            image = body["contents"][0]["parts"][1]["inlineData"]
            self.assertEqual(image["mimeType"], "image/png")
            self.assertEqual(base64.b64decode(image["data"]), b"synthetic-image-bytes")
            return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps({"items": [ITEM]})}]}}]}
        with patch("app.extraction.request", side_effect=fake):
            items = extract({"provider": "google", "token": "google-key", "model": "gemini-2.5-flash"}, self.job, self.root, ["Movies"])
        self.assertEqual(items[0]["title"], "Arrival")
        self.assertEqual(items[0]["comment"], self.job["comment"])

    def test_google_truncated_response_rejected(self):
        with patch("app.extraction.available_model", return_value={"id": "gemini-2.5-flash", "vision": True}), patch("app.extraction.request", return_value={"candidates": [{"finishReason": "MAX_TOKENS"}]}):
            with self.assertRaises(ExtractionError):
                extract({"provider": "google"}, self.job, self.root, ["Movies"])

    def test_current_google_model_does_not_use_legacy_thinking_budget(self):
        def fake(config, path, body=None, **kwargs):
            if body is None:
                return {"supportedGenerationMethods": ["generateContent"]}
            self.assertIn("gemini-3.8-flash:generateContent", path)
            self.assertEqual(body["generationConfig"]["thinkingConfig"], {"thinkingLevel": "low"})
            return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps({"items": [ITEM]})}]}}]}
        with patch("app.extraction.request", side_effect=fake):
            self.assertEqual(extract({"provider": "google", "model": "gemini-3.8-flash", "token": "key"}, self.job, self.root, ["Movies"])[0]["title"], "Arrival")

    def test_google_404_explains_new_user_restriction_without_echoing_upstream(self):
        error = {"error": {"message": "This model is no longer available to new users. secret-key"}}
        with patch("app.extraction.urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = urllib.error.HTTPError('https://generativelanguage.googleapis.com',404,'error',{},io.BytesIO(json.dumps(error).encode()))
            with self.assertRaisesRegex(ExtractionError,"new Google API users") as caught:
                request({"provider":"google","model":"gemini-2.5-flash","token":"secret-key"},'/v1beta/models/gemini-2.5-flash:generateContent',{})
            self.assertNotIn('secret-key',str(caught.exception))

    def test_openrouter_zero_price_routing(self):
        def fake(config, path, body=None, **kwargs):
            if body is None:
                if path.endswith("/key"):
                    return {"data": {}}
                return {"data": [{"id": "openrouter/free", "pricing": {"prompt": "0", "completion": "0"}}]}
            self.assertEqual(path, "/api/v1/chat/completions")
            self.assertEqual(body["provider"]["max_price"], {"prompt": 0, "completion": 0})
            self.assertTrue(body["provider"]["require_parameters"])
            return {"choices": [{"message": {"content": json.dumps({"items": [ITEM]})}}]}
        with patch("app.extraction.request", side_effect=fake):
            items = extract({"provider": "openrouter", "token": "key", "model": "openrouter/free"}, self.job, self.root, ["Movies"])
        self.assertEqual(items[0]["title"], "Arrival")

    def test_openrouter_catalog_price_change_stops_inference(self):
        def fake(config, path, body=None):
            if path.endswith("/key"):
                return {"data": {}}
            return {"data": [{"id": "vendor/model:free", "pricing": {"prompt": "0.01", "completion": "0"}}]}
        with patch("app.extraction.request", side_effect=fake) as call:
            with self.assertRaises(ExtractionError):
                available_model({"provider": "openrouter", "model": "vendor/model:free", "token": "key"})
            self.assertEqual(call.call_count, 2)

    def test_huggingface_token_checked_and_text_ocr_supported(self):
        calls = []
        def fake(config, path, body=None, **kwargs):
            calls.append(path)
            if path == "/api/whoami-v2":
                return {"name": "test-user"}
            if path == "/v1/models":
                return {"data": [{"id": "org/text-model"}]}
            self.assertEqual(len(body["messages"][1]["content"]), 1)
            self.assertIn("OCR text", body["messages"][1]["content"][0]["text"])
            return {"choices": [{"message": {"content": json.dumps({"items": [ITEM]})}}]}
        with patch("app.extraction.request", side_effect=fake), patch("app.extraction.recognize", return_value="OCR text"):
            items = extract({"provider": "huggingface", "model": "org/text-model:provider", "token": "hf-key", "mode": "ocr"}, self.job, self.root, ["Movies"])
        self.assertEqual(calls, ["/api/whoami-v2", "/v1/models", "/v1/chat/completions"])
        self.assertEqual(items[0]["title"], "Arrival")

    def test_http_auth_hosts_and_google_key_not_in_url(self):
        for provider, expected in (("google", "generativelanguage.googleapis.com"), ("openrouter", "openrouter.ai"), ("huggingface", "router.huggingface.co")):
            with self.subTest(provider=provider):
                response = io.BytesIO(b'{}')
                with patch("app.extraction.urllib.request.build_opener") as opener:
                    opener.return_value.open.return_value = response
                    request({"provider": provider, "token": "secret-key", "url": "http://evil.example"}, "/test")
                    req = opener.return_value.open.call_args.args[0]
                    self.assertIn(expected, req.full_url)
                    self.assertNotIn("secret-key", req.full_url)
                    if provider == "google":
                        self.assertEqual(req.get_header("X-goog-api-key"), "secret-key")
                        self.assertIsNone(req.get_header("Authorization"))
                    else:
                        self.assertEqual(req.get_header("Authorization"), "Bearer secret-key")

    def test_quota_and_credit_errors_do_not_fallback(self):
        for code, error in ((402, ExtractionError), (429, WaitingForModel), (401, ExtractionError)):
            with self.subTest(code=code), patch("app.extraction.urllib.request.build_opener") as opener:
                opener.return_value.open.side_effect = urllib.error.HTTPError('https://openrouter.ai', code, 'error', {}, io.BytesIO(b'private upstream error'))
                with self.assertRaises(error) as raised:
                    request({"provider": "openrouter", "token": "secret"}, "/api/v1/chat/completions", {})
                self.assertNotIn("private upstream error", str(raised.exception))
                self.assertEqual(opener.return_value.open.call_count, 1)
