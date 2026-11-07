"""Accurate micro-benchmarks of primitives used on the message/login hot paths.
NOT end-to-end stack throughput. Runs on the current host with the plexichat venv deps.

Usage: cd /root/code-plexichat/plexichat && .venv/bin/python loadtest/bench_primitives.py
"""
import json, os, time, statistics, base64, hashlib, zlib, sqlite3, tempfile, platform
from argon2 import PasswordHasher
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def bench_argon2(runs=5):
    ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
    pw = 'LoadTest!Passw0rd#2026'
    h = ph.hash(pw)
    ver = []
    for _ in range(runs):
        t0=time.perf_counter(); ph.verify(h,pw); ver.append((time.perf_counter()-t0)*1000)
    return {"time_cost":3,"memory_cost_kib":65536,"parallelism":2,"runs":runs,
            "verify_ms": [round(x,2) for x in ver],
            "verify_mean_ms": round(statistics.mean(ver),2),
            "verify_p95_ms": round(sorted(ver)[max(int(len(ver)*0.95)-1,0)],2),
            "single_process_max_verify_per_s": round(1000/statistics.mean(ver),2)}

def bench_message_crypto(msg_chars=240, tokens=40, n=2000):
    key = AESGCM.generate_key(bit_length=256); aes = AESGCM(key)
    msg = ('x' * max(msg_chars//4,1) + ' ') * 4
    msg = (msg * ((msg_chars // max(len(msg),1)) + 1))[:msg_chars]
    aad = b'123456789'
    t0=time.perf_counter()
    for _ in range(n):
        nonce=os.urandom(12); ct=aes.encrypt(nonce,msg.encode(),aad)
        f"ENC:1:{base64.b64encode(nonce+ct).decode()}"
    t1=time.perf_counter()
    root=os.urandom(32); idx_key=hashlib.blake2b(root,key=b'message_content',digest_size=32).digest()
    t2=time.perf_counter()
    for _ in range(n):
        hashlib.blake2b(msg.lower().strip().encode(), key=idx_key, digest_size=32).hexdigest()
    t3=time.perf_counter()
    toks=[f"word{j}" for j in range(tokens)]
    t4=time.perf_counter(); it=500
    for _ in range(it):
        for tok in toks:
            hashlib.blake2b(tok.encode(), key=idx_key, digest_size=32).hexdigest()
    t5=time.perf_counter()
    return {"msg_chars":msg_chars,"token_count":tokens,"n":n,
            "aesgcm_encrypt_us": round((t1-t0)/n*1e6,2),
            "whole_blind_index_us": round((t3-t2)/n*1e6,2),
            "per_token_index_us": round((t5-t4)/(it*tokens)*1e6,2),
            "all_tokens_index_us": round((t5-t4)/it*1e6,2)}

def bench_fanout_encode(payload_bytes=491, n=2000):
    payload = {"op":0,"t":"MESSAGE_CREATE","s":42,"d":{"id":"123456789012345678",
        "channel_id":"123456789012345678","author_id":"123456789012345678",
        "content":"Hello load test message "*5,"created_at":1762541883000,
        "edited_at":None,"deleted":False,"attachments":[],"embeds":[],"mentions":[],
        "author_username":"loaduser_1","author_avatar_url":None,"reactions":[],"pinned":False}}
    t0=time.perf_counter()
    for _ in range(n): json.dumps(payload).encode()
    t1=time.perf_counter()
    ctx=zlib.compressobj(); last=b''
    t2=time.perf_counter()
    for _ in range(n):
        b=json.dumps(payload).encode(); last=ctx.compress(b)+ctx.flush(zlib.Z_SYNC_FLUSH)
    t3=time.perf_counter()
    return {"payload_bytes":len(json.dumps(payload)),"compressed_frame_bytes":len(last),
            "json_dumps_us": round((t1-t0)/n*1e6,2),
            "json_plus_zlib_us": round((t3-t2)/n*1e6,2),
            "per_recipient_conn_encode_us_est": round((t3-t2)/n*1e6,2)}

def bench_sqlite_patterns(n=500):
    fd,path = tempfile.mkstemp(suffix='.db'); os.close(fd)
    try:
        db=sqlite3.connect(path); db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE VIRTUAL TABLE search_messages_fts USING fts5(
            message_id UNINDEXED, content, author_id UNINDEXED, author_username,
            conversation_id UNINDEXED, server_id UNINDEXED, channel_id UNINDEXED,
            created_at UNINDEXED, has_attachments UNINDEXED, has_embeds UNINDEXED,
            has_links UNINDEXED, mentions UNINDEXED, is_pinned UNINDEXED, attachment_types UNINDEXED,
            tokenize='porter unicode61')""")
        content = 'Hello load test message this is a realistic chat message with a few words for tokenization'
        t0=time.perf_counter()
        for i in range(n):
            db.execute("DELETE FROM search_messages_fts WHERE message_id=?",(str(i),))
            db.execute("INSERT INTO search_messages_fts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(i),content,'1','user','1','1','1','0','0','0','0','[]','0',''))
        db.commit(); t1=time.perf_counter()
        db.execute("CREATE TABLE msg_message_tokens(message_id TEXT, token_index TEXT, position INT)")
        tokens=[f"idx{j}" for j in range(25)]
        t2=time.perf_counter()
        for i in range(n):
            db.execute("DELETE FROM msg_message_tokens WHERE message_id=?",(str(i),))
            db.executemany("INSERT OR IGNORE INTO msg_message_tokens VALUES (?,?,?)",
                [(str(i),tok,pos) for pos,tok in enumerate(tokens)])
        db.commit(); t3=time.perf_counter()
        return {"note":"local sqlite3 file, NOT the production Postgres/Valkey path; shows relative op cost only",
                "n":n,"fts_delete_insert_us":round((t1-t0)/n*1e6,2),
                "token_rows25_delete_insert_us":round((t3-t2)/n*1e6,2)}
    finally:
        os.unlink(path)

def main():
    res = {"kind":"primitive_microbench","host":{"platform":platform.platform(),"machine":platform.machine(),
            "python":platform.python_version(),"cpu_count":os.cpu_count()},
           "measured_at": time.time(),
           "caveat":"Primitives only (CPU/crypto/codec/local-sqlite). NOT end-to-end Plexichat stack throughput; excludes Postgres network/txn, FTS via Postgres, Valkey, permission checks, asyncio/uvicorn overhead, ClamAV, and fanout socket I/O.",
           "argon2": bench_argon2(), "message_crypto": bench_message_crypto(),
           "fanout_encode": bench_fanout_encode(), "sqlite_patterns": bench_sqlite_patterns()}
    out = os.path.join(os.path.dirname(__file__), "results", "primitives.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out,"w") as f: json.dump(res,f,indent=2)
    print(json.dumps(res, indent=2))
    print(f"\nwrote {out}", flush=True)

if __name__ == "__main__":
    main()
