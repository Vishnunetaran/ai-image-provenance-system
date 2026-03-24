"""
T-041, T-042, T-043, T-044, T-045: Unit tests for auth.py

Tests:
  - Generate key and lookup
  - Rate limiting constraints (headers and 429 output)
  - Key revocation
"""
import pytest
from flask import Flask, jsonify, request
from bs4 import BeautifulSoup

from provena_flask.auth import (
    create_key_record,
    revoke_key,
    lookup_key,
    require_api_key,
    _usage_register,
    _usage_verify,
)
from provena_flask.models import db

# We need an app context to test Flask decorators
@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    """Use an in-memory SQLite DB for testing."""
    import os
    db_path = str(tmp_path / "test.db")
    os.environ["SQLITE_PATH"] = db_path
    db.SQLITE_PATH = db_path
    db.run_migrations()
    
    # Reset usage counters
    _usage_register.clear()
    _usage_verify.clear()
    
    yield
    os.remove(db_path)


@pytest.fixture
def test_app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    
    @app.route("/test/register", methods=["POST"])
    @require_api_key(kind="register")
    def register():
        return jsonify({"success": True}), 200

    @app.route("/test/verify", methods=["POST"])
    @require_api_key(kind="verify")
    def verify():
        return jsonify({"success": True}), 200

    return app

@pytest.fixture
def app_client(test_app):
    return test_app.test_client()

@pytest.fixture
def free_key():
    return create_key_record(org_name="Test Org", tier="free")


class TestAuthLifecycle:
    def test_create_and_lookup_key(self):
        record = create_key_record(org_name="Testing", tier="free")
        assert "plaintext_key" in record
        
        lookup = lookup_key(record["plaintext_key"])
        assert lookup is not None
        assert lookup["org_name"] == "Testing"
        
    def test_revoke_key(self):
        record = create_key_record(org_name="Revoke Me", tier="free")
        key_id = record["key_id"]
        
        # Valid before revocation
        assert lookup_key(record["plaintext_key"]) is not None
        
        # Revoke
        assert revoke_key(key_id) is True
        
        # Invalid after revocation
        assert lookup_key(record["plaintext_key"]) is None

    def test_invalid_key_fails(self):
        assert lookup_key("prov_sk_invalid") is None
        assert lookup_key("not_a_key") is None


class TestAuthMiddleware:
    def test_missing_auth_header(self, app_client):
        res = app_client.post("/test/verify")
        assert res.status_code == 401
        assert res.json["error"]["code"] == "MISSING_AUTH"

    def test_valid_auth(self, app_client, free_key):
        res = app_client.post(
            "/test/verify", 
            headers={"Authorization": f"Bearer {free_key['plaintext_key']}"}
        )
        assert res.status_code == 200
        assert "X-RateLimit-Limit" in res.headers

    def test_revoked_auth(self, app_client, free_key):
        revoke_key(free_key["key_id"])
        res = app_client.post(
            "/test/verify", 
            headers={"Authorization": f"Bearer {free_key['plaintext_key']}"}
        )
        assert res.status_code == 401
        assert res.json["error"]["code"] == "INVALID_API_KEY"

    def test_rate_limit_exceeded(self, app_client):
        # Create a key with limit 2
        record = create_key_record("Limit Tester", "free")
        key_id = record["key_id"]
        
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE api_keys SET daily_verify_limit = 2 WHERE id = ?",
                (key_id,)
            )
            
        headers = {"Authorization": f"Bearer {record['plaintext_key']}"}
        
        # Call 1: OK
        res = app_client.post("/test/verify", headers=headers)
        assert res.status_code == 200
        
        # Call 2: OK
        res = app_client.post("/test/verify", headers=headers)
        assert res.status_code == 200
        
        # Call 3: Rate Limited
        res = app_client.post("/test/verify", headers=headers)
        assert res.status_code == 429
        assert res.json["error"]["code"] == "RATE_LIMIT_EXCEEDED"
        assert res.headers["X-RateLimit-Remaining"] == "0"
