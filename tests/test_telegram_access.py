from app.store import Store


def test_one_use_invite_shares_owner_and_can_be_revoked(tmp_path):
    store = Store(tmp_path / "access.sqlite3")
    code = store.create_telegram_invite("42")
    assert not store.has_telegram_access("99", "42")
    assert store.redeem_telegram_invite("99", code, "42")
    assert not store.redeem_telegram_invite("100", code, "42")
    assert store.has_telegram_access("99", "42")
    assert not store.has_telegram_access("99", "other")
    assert store.telegram_guests("42") == ["99"]
    assert store.revoke_telegram_access("42", "99")
    assert not store.has_telegram_access("99", "42")
    store.db.close()

