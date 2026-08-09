"""DM Anti-Spam detector - core contract coverage."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class TestDMSpamDetector:
    def test_smoke(self, db):
        from src.core.antispam.detector import DMSpamDetector

        detector = DMSpamDetector(db)
        assert detector is not None
        # A benign single message passes the spam filter with no reason.
        assert detector.check_message(1, 2, "hello") == (True, None)
        # The exact threshold surface will be analysed as the suite
        # stabilises; this just proves the class is importable and
        # accessible through the canonical constructor.

    def test_analyze_text(self, db):
        """Test that benign DM text passes the spam detector."""
        from src.core.antispam.detector import DMSpamDetector

        detector = DMSpamDetector(db)
        allowed, reason = detector.check_message(1, 2, "hello friend, how are you?")
        assert allowed is True
        assert reason is None
