import os
import sys
import shutil
import pytest
import yaml
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from utils.config import ConfigLoader, MalformedConfigAction

TEMP_CONFIG_DIR = os.path.abspath("temp/config")


@pytest.fixture
def clean_config_dir():
    if os.path.exists(TEMP_CONFIG_DIR):
        shutil.rmtree(TEMP_CONFIG_DIR)
    os.makedirs(TEMP_CONFIG_DIR)
    yield
    if os.path.exists(TEMP_CONFIG_DIR):
        shutil.rmtree(TEMP_CONFIG_DIR)


class TestBasicConfigLoading:
    """Tests for basic configuration loading."""

    def test_create_default_yaml(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        defaults = {"key": "value"}

        loader = ConfigLoader(path, default_config=defaults)

        assert os.path.exists(path)
        assert loader.get("key") == "value"

        with open(path, "r") as f:
            content = yaml.safe_load(f)
        assert content == defaults

    def test_create_default_json(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.json")
        defaults = {"key": "value"}

        loader = ConfigLoader(path, default_config=defaults)

        assert os.path.exists(path)
        assert loader.get("key") == "value"

    def test_create_default_yml_extension(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yml")
        defaults = {"test": "data"}

        loader = ConfigLoader(path, default_config=defaults)

        assert os.path.exists(path)
        assert loader.get("test") == "data"

    def test_load_existing_yaml(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        data = {"existing": "value", "number": 42}

        with open(path, "w") as f:
            yaml.dump(data, f)

        loader = ConfigLoader(path)
        assert loader.get("existing") == "value"
        assert loader.get("number") == 42

    def test_load_existing_json(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.json")
        data = {"existing": "value", "number": 42}

        with open(path, "w") as f:
            json.dump(data, f)

        loader = ConfigLoader(path)
        assert loader.get("existing") == "value"
        assert loader.get("number") == 42

    def test_empty_default_config(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        assert os.path.exists(path)
        assert loader.config == {}


class TestGetMethod:
    """Tests for the get() method."""

    def test_get_existing_key(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path, default_config={"key": "value"})

        assert loader.get("key") == "value"

    def test_get_nonexistent_key_default_none(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        assert loader.get("nonexistent") is None

    def test_get_nonexistent_key_custom_default(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        assert loader.get("nonexistent", "default_value") == "default_value"

    def test_get_nested_structure(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        data = {"database": {"host": "localhost", "port": 5432}}
        loader = ConfigLoader(path, default_config=data)

        db_config = loader.get("database")
        assert db_config["host"] == "localhost"
        assert db_config["port"] == 5432

    def test_get_various_types(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        data = {
            "string": "text",
            "integer": 42,
            "float": 3.14,
            "boolean": True,
            "list": [1, 2, 3],
            "dict": {"nested": "value"},
        }
        loader = ConfigLoader(path, default_config=data)

        assert loader.get("string") == "text"
        assert loader.get("integer") == 42
        assert loader.get("float") == 3.14
        assert loader.get("boolean") is True
        assert loader.get("list") == [1, 2, 3]
        assert loader.get("dict") == {"nested": "value"}


class TestSetMethod:
    """Tests for the set() method."""

    def test_set_and_save(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        loader.set("new_key", 123)

        loader2 = ConfigLoader(path)
        assert loader2.get("new_key") == 123

    def test_set_overwrites_existing(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path, default_config={"key": "old"})

        loader.set("key", "new")
        assert loader.get("key") == "new"

        loader2 = ConfigLoader(path)
        assert loader2.get("key") == "new"

    def test_set_multiple_values(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        loader.set("key1", "value1")
        loader.set("key2", "value2")
        loader.set("key3", "value3")

        assert loader.get("key1") == "value1"
        assert loader.get("key2") == "value2"
        assert loader.get("key3") == "value3"

    def test_set_complex_structure(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        complex_data = {"nested": {"deep": {"value": 42}}, "list": [1, 2, 3]}
        loader.set("complex", complex_data)

        loader2 = ConfigLoader(path)
        retrieved = loader2.get("complex")
        assert retrieved["nested"]["deep"]["value"] == 42
        assert retrieved["list"] == [1, 2, 3]

    def test_set_json_format(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.json")
        loader = ConfigLoader(path)

        loader.set("key", "value")

        with open(path, "r") as f:
            data = json.load(f)
        assert data["key"] == "value"


class TestMalformedConfigHandling:
    """Tests for malformed configuration file handling."""

    def test_malformed_yaml_crash_on_single(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "bad.yaml")
        with open(path, "w") as f:
            f.write("key: value\n[[[[invalid yaml syntax")

        with pytest.raises(ValueError, match="Config file is malformed"):
            ConfigLoader(path, malformed_action=MalformedConfigAction.CRASH_ON_SINGLE)

    def test_malformed_yaml_crash_on_many(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "bad.yaml")
        with open(path, "w") as f:
            f.write("key: value\n[[[[invalid yaml syntax")

        with pytest.raises(ValueError, match="Config file is malformed"):
            ConfigLoader(path, malformed_action=MalformedConfigAction.CRASH_ON_MANY)

    def test_malformed_yaml_ignore(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "bad.yaml")
        with open(path, "w") as f:
            f.write("key: value\n[[[[invalid yaml syntax")

        defaults = {"fallback": True}
        loader = ConfigLoader(
            path, default_config=defaults, malformed_action=MalformedConfigAction.IGNORE
        )

        assert loader.get("fallback") is True
        assert loader.get("key") is None

    def test_malformed_json_crash_on_single(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "bad.json")
        with open(path, "w") as f:
            f.write('{"key": "value", invalid}')

        with pytest.raises(ValueError, match="Config file is malformed"):
            ConfigLoader(path, malformed_action=MalformedConfigAction.CRASH_ON_SINGLE)

    def test_malformed_json_ignore(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "bad.json")
        with open(path, "w") as f:
            f.write('{"key": "value", invalid}')

        defaults = {"safe": "value"}
        loader = ConfigLoader(
            path, default_config=defaults, malformed_action=MalformedConfigAction.IGNORE
        )

        assert loader.get("safe") == "value"

    def test_empty_yaml_file(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "empty.yaml")
        with open(path, "w") as f:
            f.write("")

        loader = ConfigLoader(path)
        assert loader.config == {}

    def test_yaml_with_only_comments(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "comments.yaml")
        with open(path, "w") as f:
            f.write("# This is a comment\n# Another comment\n")

        loader = ConfigLoader(path)
        assert loader.config == {}


class TestUnsupportedFormat:
    """Tests for unsupported config file formats."""

    def test_unsupported_extension(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.txt")

        with pytest.raises(ValueError, match="Unsupported config format"):
            ConfigLoader(path)

    def test_no_extension(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config")

        with pytest.raises(ValueError, match="Unsupported config format"):
            ConfigLoader(path)

    def test_xml_extension(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.xml")

        with pytest.raises(ValueError, match="Unsupported config format"):
            ConfigLoader(path)


class TestDirectoryCreation:
    """Tests for automatic directory creation."""

    def test_creates_nested_directories(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "nested", "deep", "config.yaml")

        loader = ConfigLoader(path, default_config={"test": "value"})

        assert os.path.exists(path)
        assert loader.get("test") == "value"

    def test_handles_existing_directories(self, clean_config_dir):
        nested_dir = os.path.join(TEMP_CONFIG_DIR, "existing")
        os.makedirs(nested_dir)

        path = os.path.join(nested_dir, "config.yaml")
        ConfigLoader(path)

        assert os.path.exists(path)


class TestSecurityConfigInjection:
    """Security tests for configuration injection attacks."""

    def test_yaml_injection_attempt(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        malicious_yaml = "!!python/object/apply:os.system ['echo hacked']"
        loader.set("key", malicious_yaml)

        loader2 = ConfigLoader(path)
        value = loader2.get("key")
        assert value == malicious_yaml
        assert isinstance(value, str)

    def test_yaml_safe_load_protects_against_code_execution(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "malicious.yaml")

        with open(path, "w") as f:
            f.write("key: !!python/object/apply:os.system ['echo hacked']")

        with pytest.raises(ValueError):
            ConfigLoader(path, malformed_action=MalformedConfigAction.CRASH_ON_SINGLE)

    def test_special_characters_in_keys(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        loader.set("key<script>", "value")
        loader.set("key'; DROP TABLE", "value2")

        assert loader.get("key<script>") == "value"
        assert loader.get("key'; DROP TABLE") == "value2"

    def test_special_characters_in_values(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        dangerous_values = [
            "<script>alert('xss')</script>",
            "'; DROP TABLE users; --",
            "../../../etc/passwd",
            "${env:SECRET_KEY}",
            "$(whoami)",
            "`rm -rf /`",
        ]

        for i, value in enumerate(dangerous_values):
            loader.set(f"key{i}", value)

        for i, value in enumerate(dangerous_values):
            assert loader.get(f"key{i}") == value

    def test_path_traversal_in_config_path(self, clean_config_dir):
        malicious_path = os.path.join(TEMP_CONFIG_DIR, "..", "..", "config.yaml")
        normalized_path = os.path.abspath(malicious_path)

        ConfigLoader(normalized_path, default_config={"safe": True})
        assert os.path.exists(normalized_path)

    def test_null_bytes_in_config_values(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        value_with_null = "normal\x00text"
        loader.set("key", value_with_null)

        assert loader.get("key") == value_with_null

    def test_very_large_config_file(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "large.yaml")
        loader = ConfigLoader(path)

        for i in range(1000):
            loader.set(f"key{i}", f"value{i}")

        loader2 = ConfigLoader(path)
        assert loader2.get("key0") == "value0"
        assert loader2.get("key999") == "value999"

    def test_deeply_nested_structure(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        deep_structure = {
            "level1": {"level2": {"level3": {"level4": {"level5": "deep_value"}}}}
        }
        loader.set("deep", deep_structure)

        loader2 = ConfigLoader(path)
        value = loader2.get("deep")
        assert value["level1"]["level2"]["level3"]["level4"]["level5"] == "deep_value"

    def test_unicode_in_config(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        unicode_values = {
            "chinese": "中文",
            "emoji": "🔒🔐",
            "arabic": "العربية",
            "mixed": "Hello 世界 🌍",
        }

        for key, value in unicode_values.items():
            loader.set(key, value)

        loader2 = ConfigLoader(path)
        for key, value in unicode_values.items():
            assert loader2.get(key) == value


class TestConfigPersistence:
    """Tests for configuration persistence across instances."""

    def test_multiple_loaders_same_file(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")

        loader1 = ConfigLoader(path)
        loader1.set("key1", "value1")

        loader2 = ConfigLoader(path)
        assert loader2.get("key1") == "value1"

        loader2.set("key2", "value2")

        loader3 = ConfigLoader(path)
        assert loader3.get("key1") == "value1"
        assert loader3.get("key2") == "value2"

    def test_updates_visible_after_reload(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")

        loader = ConfigLoader(path, default_config={"original": "value"})
        loader.set("updated", "new_value")

        with open(path, "r") as f:
            data = yaml.safe_load(f)

        assert "original" in data
        assert "updated" in data
        assert data["updated"] == "new_value"


class TestMalformedConfigActionEnum:
    """Tests for MalformedConfigAction enum."""

    def test_enum_values(self):
        assert MalformedConfigAction.CRASH_ON_SINGLE.value == "crash_on_single"
        assert MalformedConfigAction.CRASH_ON_MANY.value == "crash_on_many"
        assert MalformedConfigAction.IGNORE.value == "ignore"

    def test_enum_comparison(self):
        assert (
            MalformedConfigAction.CRASH_ON_SINGLE
            == MalformedConfigAction.CRASH_ON_SINGLE
        )
        assert MalformedConfigAction.CRASH_ON_SINGLE != MalformedConfigAction.IGNORE


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_config_with_none_values(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        loader.set("null_value", None)

        loader2 = ConfigLoader(path)
        assert loader2.get("null_value") is None

    def test_config_with_empty_string(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        loader.set("empty", "")

        loader2 = ConfigLoader(path)
        assert loader2.get("empty") == ""

    def test_config_with_zero_values(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        loader.set("zero_int", 0)
        loader.set("zero_float", 0.0)

        loader2 = ConfigLoader(path)
        assert loader2.get("zero_int") == 0
        assert loader2.get("zero_float") == 0.0

    def test_config_with_boolean_false(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        loader.set("false_value", False)

        loader2 = ConfigLoader(path)
        assert loader2.get("false_value") is False

    def test_config_with_empty_list(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        loader.set("empty_list", [])

        loader2 = ConfigLoader(path)
        assert loader2.get("empty_list") == []

    def test_config_with_empty_dict(self, clean_config_dir):
        path = os.path.join(TEMP_CONFIG_DIR, "config.yaml")
        loader = ConfigLoader(path)

        loader.set("empty_dict", {})

        loader2 = ConfigLoader(path)
        assert loader2.get("empty_dict") == {}
