"""Scenario 02: WebSocket connect+identify+heartbeat, hold idle connections.
Measures connection/RAM/heartbeat stability.

Usage:
  PL_WS_URL=ws://host:8000/gateway PL_BASE_URL=http://host:8000/api/v1 \
  PL_USERS=50 PL_HOLD=120 PL_COMPRESS=1 .venv/bin/python loadtest/ws_idle.py
"""
import asyncio, json, os, time, statistics, sys
import aiohttp, websockets
from config import (BASE_URL, WS_URL, USER_PREFIX, USER_PASSWORD, USER_COUNT,
                    TOKEN_MODE_BEARER, RESULTS_DIR, GATEWAY_HEARTBEAT, GATEWAY_IDENTIFY,
                    GATEWAY_HELLO, GATEWAY_HEARTBEAT_ACK)

USERS = int(os.environ.get("PL_USERS", "20"))
HOLD = float(os.environ.get("PL_HOLD", "60"))
COMPRESS = os.environ.get("PL_COMPRESS", "1") == "1"
STAGGER = float(os.environ.get("PL_STAGGER", "0.05"))

async def login_get_ticket(http, i):
    h={"Content-Type":"application/json"}
    if TOKEN_MODE_BEARER: h["X-Plexichat-Token-Mode"]="bearer"
    async with http.post(f"{BASE_URL}/auth/login",
                         json={"username":f"{USER_PREFIX}{i}","password":USER_PASSWORD}, headers=h) as r:
        d=await r.json(content_type=None)
        tok = d.get("token") or (d.get("data") or {}).get("token")
    ah={"Authorization":f"Bearer {tok}"} if tok else {}
    async with http.post(f"{BASE_URL}/auth/ws-ticket", headers=ah) as r:
        td=await r.json(content_type=None)
        return td.get("ticket") or (td.get("data") or {}).get("ticket")

async def one_conn(http, i, stats, stop):
    t0=time.perf_counter()
    try:
        ticket = await login_get_ticket(http, i)
        if not ticket: stats["auth_fail"]+=1; return
        async with websockets.connect(WS_URL, max_size=2**20, open_timeout=15) as ws:
            # HELLO
            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            hello = json.loads(raw); hb_ms = (hello.get("d") or {}).get("heartbeat_interval", 45000)
            # IDENTIFY
            await ws.send(json.dumps({"op":GATEWAY_IDENTIFY,"d":{"token":ticket,"compress":COMPRESS,
                               "properties":{"$browser":"loadtest","$device":"loadtest"}}}))
            ready=False
            async def reader():
                nonlocal ready
                async for msg in ws:
                    d=json.loads(msg)
                    if d.get("t")=="READY": ready=True; stats["ready"]+=1
                    if d.get("op")==GATEWAY_HEARTBEAT_ACK: stats["hb_acks"]+=1
            async def heartbeater():
                while not stop.is_set():
                    await asyncio.sleep(max(hb_ms/1000.0, 5))
                    try: await ws.send(json.dumps({"op":GATEWAY_HEARTBEAT,"d":int(time.time()*1000)})); stats["hb_sent"]+=1
                    except Exception: break
            rt=asyncio.create_task(reader()); ht=asyncio.create_task(heartbeater())
            try:
                await asyncio.wait_for(asyncio.sleep(1.5), timeout=5)
            except Exception: pass
            if not ready: stats["no_ready"]+=1
            stats["connect_ms"].append((time.perf_counter()-t0)*1000)
            await stop.wait()
            rt.cancel(); ht.cancel()
    except Exception as e:
        stats["errors"]+=1; stats["err_samples"].append(str(e)[:160])

async def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stats={"ready":0,"no_ready":0,"errors":0,"auth_fail":0,"hb_sent":0,"hb_acks":0,
           "connect_ms":[],"err_samples":[]}
    stop=asyncio.Event()
    timeout=aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(limit=USERS+10)) as http:
        tasks=[]
        for n in range(USERS):
            i=(n % max(USER_COUNT,1))+1
            tasks.append(asyncio.create_task(one_conn(http,i,stats,stop)))
            await asyncio.sleep(STAGGER)
        # allow connects to settle
        await asyncio.sleep(min(10, max(HOLD*0.2, 2)))
        print(f"holding {stats['ready']} ready conns for {HOLD}s (errors={stats['errors']})")
        await asyncio.sleep(HOLD); stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    def pct(v,p): 
        if not v: return None
        v=sorted(v); return round(v[min(int(len(v)*p/100),len(v)-1)],2)
    res={"scenario":"ws_idle","ws_url":WS_URL,"users":USERS,"hold_s":HOLD,"compress":COMPRESS,
         "ready":stats["ready"],"no_ready":stats["no_ready"],"errors":stats["errors"],
         "auth_fail":stats["auth_fail"],"hb_sent":stats["hb_sent"],"hb_acks":stats["hb_acks"],
         "connect_ms":{"p50":pct(stats["connect_ms"],50),"p95":pct(stats["connect_ms"],95),"max":round(max(stats["connect_ms"]),2) if stats["connect_ms"] else None},
         "err_samples":stats["err_samples"][:5]}
    out=os.path.join(RESULTS_DIR,"ws_idle.json"); json.dump(res,open(out,"w"),indent=2)
    print(json.dumps(res,indent=2)); print(f"wrote {out}")

if __name__=="__main__": asyncio.run(main())
