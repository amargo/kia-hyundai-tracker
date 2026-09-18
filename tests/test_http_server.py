"""Tests for http_server's auth gate and the synchronous-flag parsing bug."""

import http_server


class TestTruthy:
    def test_common_true_values(self):
        for value in ("1", "true", "True", "yes", "on"):
            assert http_server._truthy(value) is True

    def test_the_bug_this_replaces(self):
        # bool("false") is True in plain Python - this is exactly why /charge's
        # synchronous=false used to behave as if synchronous=true was requested.
        assert bool("false") is True
        assert http_server._truthy("false") is False

    def test_empty_and_none(self):
        assert http_server._truthy("") is False
        assert http_server._truthy(None) is False

    def test_garbage_defaults_to_false(self):
        assert http_server._truthy("nope") is False


class TestAuthGate:
    def _client(self):
        http_server.app.config["TESTING"] = True
        return http_server.app.test_client()

    def test_no_password_configured_allows_access(self, monkeypatch):
        monkeypatch.delenv("HTTP_SERVER_PASSWORD", raising=False)
        resp = self._client().get("/")
        assert resp.status_code == 200

    def test_missing_credentials_are_rejected(self, monkeypatch):
        monkeypatch.setenv("HTTP_SERVER_PASSWORD", "s3cr3t")
        resp = self._client().get("/")
        assert resp.status_code == 401

    def test_wrong_credentials_are_rejected(self, monkeypatch):
        monkeypatch.setenv("HTTP_SERVER_PASSWORD", "s3cr3t")
        resp = self._client().get("/", headers={"X-Api-Key": "wrong"})
        assert resp.status_code == 401

    def test_bearer_token_is_accepted(self, monkeypatch):
        monkeypatch.setenv("HTTP_SERVER_PASSWORD", "s3cr3t")
        resp = self._client().get("/", headers={"Authorization": "Bearer s3cr3t"})
        assert resp.status_code == 200

    def test_api_key_header_is_accepted(self, monkeypatch):
        monkeypatch.setenv("HTTP_SERVER_PASSWORD", "s3cr3t")
        resp = self._client().get("/", headers={"X-Api-Key": "s3cr3t"})
        assert resp.status_code == 200

    def test_query_param_is_accepted(self, monkeypatch):
        monkeypatch.setenv("HTTP_SERVER_PASSWORD", "s3cr3t")
        resp = self._client().get("/?password=s3cr3t")
        assert resp.status_code == 200
