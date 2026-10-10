"""Split modules backing the root ``src/tests/conftest.py``.

The root conftest grew past the 1000-line maintainability limit; its
fixtures/hooks now live here, grouped by concern, and are re-imported into
``src/tests/conftest.py`` so pytest fixture/hook discovery is unchanged.
"""
