from uuid import uuid4

from softarr.auth.sessions import create_session_cookie, read_session_cookie


class TestSessions:
    def test_roundtrip(self):
        uid = uuid4()
        token = create_session_cookie(uid, "admin")
        data = read_session_cookie(token)
        assert data is not None
        assert data["uid"] == str(uid)
        assert data["u"] == "admin"

    def test_tampered_token_returns_none(self):
        uid = uuid4()
        token = create_session_cookie(uid, "admin")
        tampered = token[:-5] + "XXXXX"
        assert read_session_cookie(tampered) is None

    def test_empty_token_returns_none(self):
        assert read_session_cookie("") is None

    def test_garbage_token_returns_none(self):
        assert read_session_cookie("not.a.valid.token") is None

    # ------------------------------------------------------------------
    # Disclaimer accepted (da) flag
    # ------------------------------------------------------------------

    def test_disclaimer_accepted_false_by_default(self):
        """Session cookie created without disclaimer_accepted defaults da=False."""
        uid = uuid4()
        token = create_session_cookie(uid, "alice", role="admin")
        data = read_session_cookie(token)
        assert data is not None
        assert data.get("da") is False

    def test_disclaimer_accepted_true_when_set(self):
        """Session cookie carries da=True when disclaimer_accepted=True."""
        uid = uuid4()
        token = create_session_cookie(
            uid, "alice", role="admin", disclaimer_accepted=True
        )
        data = read_session_cookie(token)
        assert data is not None
        assert data.get("da") is True

    def test_disclaimer_not_accepted_flag_is_false(self):
        """Session cookie carries da=False when disclaimer_accepted=False explicitly."""
        uid = uuid4()
        token = create_session_cookie(
            uid, "alice", role="admin", disclaimer_accepted=False
        )
        data = read_session_cookie(token)
        assert data is not None
        assert data.get("da") is False
