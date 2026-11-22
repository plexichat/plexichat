import sys


def test_probe():
    from src.api.routes import artifacts_helpers as helpers

    print("PROBE file:", helpers.__file__)
    print("PROBE has:", hasattr(helpers, "_can_emit_artifact_message"))
    print("PROBE id:", id(helpers))
    print("PROBE modules id:", id(sys.modules.get("src.api.routes.artifacts_helpers")))
