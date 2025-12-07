#!/usr/bin/env python3
"""Update TURN server configuration."""
import yaml

config_path = "/root/.plexichat/config/config.yaml"

with open(config_path, "r") as f:
    cfg = yaml.safe_load(f)

cfg["voice"]["turn_urls"] = [
    "turn:openrelay.metered.ca:80",
    "turn:openrelay.metered.ca:443",
    "turns:openrelay.metered.ca:443"
]
cfg["voice"]["turn_username"] = "openrelayproject"
cfg["voice"]["turn_credential"] = "openrelayproject"

with open(config_path, "w") as f:
    yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)

print("Updated TURN configuration")
