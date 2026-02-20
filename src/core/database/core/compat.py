from .. import dialect


class _CompatibilityRegex:
    def __init__(self, pattern):
        self.pattern = pattern

    def sub(self, repl, string):
        def wrapper(match):
            if match.group(1):
                return match.group(1)
            return repl

        return self.pattern.sub(wrapper, string)

    def __getattr__(self, name):
        return getattr(self.pattern, name)


_PLACEHOLDER_PATTERN = _CompatibilityRegex(dialect._PLACEHOLDER_PATTERN)
