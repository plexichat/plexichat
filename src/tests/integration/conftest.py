"""Collection support for the SEC100 invariant suites.

The repository-wide ``python_files`` glob is ``test_*.py``, but the SEC100
invariant files use the ``sec100_`` prefix.  This hook makes them collectable
during normal directory/suite discovery while leaving explicitly-passed files
to pytest's own collector (so a file is never collected twice).
"""

from __future__ import annotations

import pytest


def pytest_collect_file(file_path, parent):
    name = file_path.name
    if not (name.startswith("sec100_") and file_path.suffix == ".py"):
        return None

    # pytest already collects files named explicitly on the command line;
    # returning a module for those would double-collect them.
    try:
        resolved = file_path.resolve()
        for arg in parent.config.args:
            try:
                if (parent.config.rootpath / arg).resolve() == resolved:
                    return None
            except (OSError, TypeError):
                continue
    except (OSError, TypeError):
        pass

    return pytest.Module.from_parent(parent, path=file_path)
