from app.security import make_session, verify_password, verify_session


def test_password_comparison() -> None:
    assert verify_password("correct horse", "correct horse")
    assert not verify_password("wrong", "correct horse")


def test_signed_session_expires() -> None:
    secret = "s" * 32
    session = make_session(secret, now=1_000)

    assert verify_session(session, secret, now=1_100, ttl=200)
    assert not verify_session(session, secret, now=1_201, ttl=200)
    assert not verify_session(session, "x" * 32, now=1_100, ttl=200)
    assert not verify_session("broken", secret, now=1_100, ttl=200)

