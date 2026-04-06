"""Tests for JWT capability token generation and validation."""

import uuid

import pytest

from app.core.auth import (
    AuthError,
    check_authority,
    create_agent_token,
    decode_token,
    extract_agent_id,
    require_scope,
)


class TestCreateToken:
    def test_create_and_decode_roundtrip(self, agent_id):
        token = create_agent_token(agent_id, owner_id="test-owner")
        claims = decode_token(token)

        assert claims["sub"] == str(agent_id)
        assert claims["owner_id"] == "test-owner"
        assert claims["iss"] == "handoff-server"
        assert "exp" in claims
        assert "iat" in claims
        assert "jti" in claims

    def test_default_scopes(self, agent_id):
        token = create_agent_token(agent_id, owner_id="test")
        claims = decode_token(token)
        assert set(claims["scopes"]) == {"negotiate", "handoff", "discover"}

    def test_custom_scopes(self, agent_id):
        token = create_agent_token(agent_id, owner_id="test", scopes=["discover"])
        claims = decode_token(token)
        assert claims["scopes"] == ["discover"]

    def test_authority_included(self, agent_id):
        authority = {"max_spend": 5000, "allowed_domains": ["hotels"]}
        token = create_agent_token(agent_id, owner_id="test", authority=authority)
        claims = decode_token(token)
        assert claims["authority"]["max_spend"] == 5000
        assert claims["authority"]["allowed_domains"] == ["hotels"]


class TestDecodeToken:
    def test_invalid_token_raises(self):
        with pytest.raises(AuthError) as exc:
            decode_token("not-a-valid-token")
        assert "Invalid token" in str(exc.value)

    def test_decode_preserves_jti(self, agent_id):
        token = create_agent_token(agent_id, owner_id="test")
        claims = decode_token(token)
        assert claims["jti"]  # non-empty UUID string


class TestRequireScope:
    def test_scope_present_passes(self):
        claims = {"scopes": ["negotiate", "handoff"]}
        require_scope(claims, "negotiate")  # Should not raise

    def test_missing_scope_raises(self):
        claims = {"scopes": ["discover"]}
        with pytest.raises(AuthError) as exc:
            require_scope(claims, "negotiate")
        assert exc.value.status_code == 403

    def test_empty_scopes_raises(self):
        claims = {"scopes": []}
        with pytest.raises(AuthError):
            require_scope(claims, "handoff")


class TestCheckAuthority:
    def test_no_restrictions_passes(self):
        claims = {"authority": {}}
        check_authority(claims, domain="hotels", spend=1000)

    def test_allowed_domain_passes(self):
        claims = {"authority": {"allowed_domains": ["hotels", "flights"]}}
        check_authority(claims, domain="hotels")

    def test_disallowed_domain_raises(self):
        claims = {"authority": {"allowed_domains": ["hotels"]}}
        with pytest.raises(AuthError) as exc:
            check_authority(claims, domain="flights")
        assert exc.value.status_code == 403

    def test_spend_within_limit_passes(self):
        claims = {"authority": {"max_spend": 5000}}
        check_authority(claims, spend=3000)

    def test_spend_over_limit_raises(self):
        claims = {"authority": {"max_spend": 5000}}
        with pytest.raises(AuthError):
            check_authority(claims, spend=6000)


class TestExtractAgentId:
    def test_extract_valid_id(self, agent_id):
        claims = {"sub": str(agent_id)}
        assert extract_agent_id(claims) == agent_id

    def test_extract_invalid_uuid_raises(self):
        with pytest.raises(ValueError):
            extract_agent_id({"sub": "not-a-uuid"})
