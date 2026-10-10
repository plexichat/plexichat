"""Regression tests for the Stash cleanup fallback manager (Suite L7).

The cleanup job's fallback previously constructed ``StashManager(db)`` with no
media module, silently degrading retention: ``purge_deleted_items`` could not
delete referenced media objects and kept soft-deleted rows forever. The
fallback must wire the media module when available and log loudly when it
cannot, instead of failing silently.
"""


class TestStashCleanupFallback:
    def test_prefers_bootstrapped_singleton(self, db):
        """When the domain is initialised, the singleton (media wired) wins."""
        from src.core.suite import stash as stash_domain
        from src.core.suite.stash.cleanup import StashCleanupJob

        stash_domain.setup(db)
        job = StashCleanupJob(db)
        try:
            manager = job._resolve_manager()
            assert manager is stash_domain.get_manager()
        finally:
            stash_domain._manager = None

    def test_fallback_wires_media_when_available(self, db, media_manager, monkeypatch):
        """The fallback manager must carry the media module when it is available."""
        from src.core.suite import stash as stash_domain
        from src.core.suite.stash.cleanup import StashCleanupJob
        import src.core.media as media_domain

        monkeypatch.setattr(stash_domain, "_manager", None)
        # The media module is not initialised through ``media.setup`` in the
        # test env, so simulate an available module boundary.
        monkeypatch.setattr(media_domain, "_get_manager", lambda: media_manager)

        job = StashCleanupJob(db)
        manager = job._resolve_manager()
        assert manager._media is media_manager

    def test_fallback_without_media_is_loud_not_silent(self, db, monkeypatch, caplog):
        """Without a media module the fallback must log a warning, not silently degrade."""
        from src.core.suite import stash as stash_domain
        from src.core.suite.stash.cleanup import StashCleanupJob

        monkeypatch.setattr(stash_domain, "_manager", None)
        import src.core.media as media_domain

        def _raise():
            raise RuntimeError("media not initialised")

        monkeypatch.setattr(media_domain, "_get_manager", _raise)

        job = StashCleanupJob(db)
        with caplog.at_level("WARNING", logger="src.core.suite.stash.cleanup"):
            manager = job._resolve_manager()
        assert manager._media is None
        assert any(
            "no media module wired" in record.message for record in caplog.records
        )
