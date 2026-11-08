"""Compute per-message/login CPU-primitive floor from primitives.json (measured)."""

import json
import os

p = json.load(
    open(os.path.join(os.path.dirname(__file__), "results", "primitives.json"))
)
mc = p["message_crypto"]
fe = p["fanout_encode"]
sp = p["sqlite_patterns"]
a = p["argon2"]
crypto_us = (
    mc["aesgcm_encrypt_us"] + mc["whole_blind_index_us"] + mc["all_tokens_index_us"]
)
sqlite_local_us = sp["fts_delete_insert_us"] + sp["token_rows25_delete_insert_us"] * (
    mc["token_count"] / 25
)
per_rcpt_us = fe["per_recipient_conn_encode_us_est"]
rows = []
for rcpt in [1, 2, 5, 10, 20, 50, 100, 200]:
    floor_ms = (crypto_us + sqlite_local_us + rcpt * per_rcpt_us) / 1000
    # optimistic single-process ceiling IF only these CPU primitives mattered (ignores DB/network/perm/fsync/asyncio)
    ceiling = 1000 / floor_ms
    rows.append(
        {
            "recipient_connections": rcpt,
            "primitive_cpu_floor_ms": round(floor_ms, 3),
            "optimistic_cpu_ceiling_msg_per_s": round(ceiling, 1),
            "caveat": "excludes Postgres txn+network, Valkey, FTS-via-Postgres, per-member channels.view permission checks, asyncio/uvicorn, fsync, socket I/O; real stack is lower",
        }
    )
out = {
    "basis": p["host"],
    "measured_at": p["measured_at"],
    "sample": {
        "msg_chars": mc["msg_chars"],
        "tokens": mc["token_count"],
        "compress": True,
    },
    "argon2_login": {
        "verify_mean_ms": a["verify_mean_ms"],
        "verify_p95_ms": a["verify_p95_ms"],
        "single_process_max_verify_per_s": a["single_process_max_verify_per_s"],
        "memory_cost_kib": a["memory_cost_kib"],
        "note": "dominant per-login CPU+64MiB; 1 backend process => effectively serialized",
    },
    "per_message_components_us": {
        "crypto_total": round(crypto_us, 2),
        "sqlite_local_fts_plus_40_token_rows": round(sqlite_local_us, 2),
        "per_recipient_conn_json_zlib": per_rcpt_us,
    },
    "fanout_floor": rows,
    "disclaimer": "NOT end-to-end measured throughput. Run login_load/ws_idle/msg_send against the target 5600X/12GB default Docker deployment for accurate stack figures.",
}
path = os.path.join(os.path.dirname(__file__), "results", "envelope.json")
json.dump(out, open(path, "w"), indent=2)
print(json.dumps(out, indent=2))
print("wrote", path)
