from src.utils.encryption import blind_index


def test_user_email_encryption_decryption(auth_manager, db):
    # This test verifies that the AuthManager correctly handles encrypted email
    # It should use the blind index for lookup and decrypt the email for the model

    email = "test-encryption@example.com"
    username = "testencrypt"
    password = "SecurePassword123!"

    # Register user
    user = auth_manager.register(username, email, password)
    assert user is not None
    user_id = user.id

    # Check database directly to see encrypted values
    row = db.fetch_one(
        "SELECT email_index, email_encrypted FROM auth_users WHERE id = ?", (user_id,)
    )
    assert row is not None
    assert row["email_encrypted"] is not None
    assert row["email_encrypted"] != email
    assert row["email_index"] is not None

    # Verify we can find user by email index (scope MUST be "user_email")
    email_idx = blind_index(email, scope="user_email")
    row_by_idx = db.fetch_one(
        "SELECT id FROM auth_users WHERE email_index = ?", (email_idx,)
    )
    assert row_by_idx is not None
    assert row_by_idx["id"] == user_id

    # Load user via AuthManager and check if email is decrypted
    loaded_user = auth_manager.get_user(user_id)
    assert loaded_user.email == email


def test_user_dob_encryption(auth_manager, db):
    # Verifies that Date of Birth is encrypted at rest
    manager = auth_manager._get_manager()

    # Enable age gate and DOB verification in the manager's cached config
    old_accounts = manager._config.get("accounts", {})
    new_accounts = old_accounts.copy()
    new_accounts["age_gate_enabled"] = True
    new_accounts["age_verification_type"] = "dob"

    # Temporary swap config
    manager._config["accounts"] = new_accounts
    try:
        email = "dob@example.com"
        username = "dobuser"
        dob = "1990-01-01"

        user = auth_manager.register(username, email, "SecurePass123!", dob=dob)
        user_id = user.id

        # Check DB
        row = db.fetch_one(
            "SELECT date_of_birth FROM auth_users WHERE id = ?", (user_id,)
        )
        assert row["date_of_birth"] is not None
        assert row["date_of_birth"] != dob

        # Load via manager
        loaded_user = auth_manager.get_user(user_id)
        assert loaded_user.date_of_birth == dob
    finally:
        # Restore config
        manager._config["accounts"] = old_accounts


def test_session_pii_protection(auth_manager, db):
    # Verifies that session IP and UA are encrypted at rest and decrypted in model

    username = "sessionpii"
    password = "SecurePassword123!"
    email = "sessionpii@example.com"
    ip = "1.2.3.4"
    ua = "TestBrowser/1.0"

    auth_manager.register(username, email, password)
    login_result = auth_manager.login(username, password, ip_address=ip, user_agent=ua)

    user_id = login_result.user.id
    session_id = login_result.session.id

    # Check database directly
    row = db.fetch_one(
        "SELECT ip_encrypted, ua_encrypted FROM auth_sessions WHERE id = ?",
        (session_id,),
    )
    assert row["ip_encrypted"] != ip
    assert row["ua_encrypted"] != ua

    # Load via AuthManager module
    sessions = auth_manager.get_sessions(user_id)
    session = next((s for s in sessions if s.id == session_id), None)
    assert session is not None
    assert session.ip_address == ip
    assert session.user_agent == ua


def test_audit_log_pii_protection(auth_manager, db):
    # Verifies audit log IP protection

    username = "auditpii"
    password = "SecurePassword123!"
    ip = "9.8.7.6"

    user = auth_manager.register(username, "audit@example.com", password)
    user_id = user.id
    auth_manager.login(username, password, ip_address=ip)

    # Check latest audit entry in DB
    row = db.fetch_one(
        "SELECT ip_encrypted FROM auth_audit_log ORDER BY timestamp DESC LIMIT 1"
    )
    assert row["ip_encrypted"] is not None
    assert row["ip_encrypted"] != ip

    # Retrieve via AuthManager
    history = auth_manager.get_login_history(user_id)
    assert len(history) > 0
    entry = history[0]
    assert entry.ip_address == ip
