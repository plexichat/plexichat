"""Regression tests for the next verified admin hardening batch."""

from unittest.mock import MagicMock


def test_search_users_escapes_like_wildcards():
    from src.core.admin.users import search_users

    db = MagicMock()
    db.fetch_all.return_value = []
    search_users(db, "%_", 20, 0)
    query, params = db.fetch_all.call_args.args
    assert "ESCAPE" in query
    assert params[:2] == (r"%\%\_%", r"%\%\_%")


def test_dm_stats_accepts_tuple_rows():
    from src.core.antispam.detector import DMSpamDetector

    db = MagicMock()
    db.fetch_all.side_effect = [
        [("rate_limited", 2)],
        [(123, 2)],
    ]
    stats = DMSpamDetector(db).get_spam_stats(9)
    assert stats["events_by_type"] == {"rate_limited": 2}
    assert stats["top_spammers"] == [{"sender_id": 123, "count": 2}]


def test_admin_logger_reports_failures_without_print(capsys):
    from src.core.admin.logging import AdminLogger, AdminLogEntry

    file_logger = MagicMock()
    file_logger.log.side_effect = RuntimeError("sink failed")
    logger = AdminLogger(file_logger)
    assert (
        logger.log_action(
            MagicMock(), AdminLogEntry(admin_id=1, action="test"), log_to_db=False
        )
        is False
    )
    assert capsys.readouterr().out == ""
    file_logger.exception.assert_called_once()


def test_admin_template_errors_do_not_include_exception(monkeypatch):
    from src.api.routes.admin import utils

    monkeypatch.setattr(
        utils.Path,
        "read_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret path")),
    )
    result = utils.load_admin_template("login.html")
    assert "secret path" not in result
    assert result == "<h1>Template Error</h1>"
