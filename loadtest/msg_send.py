"""Scenario 03: send messages at fixed rate while receivers hold WS connections.
Measures per-message + fanout path. Requires PL_CHANNEL_ID and provisioned/joined users.

Usage:
  PL_BASE_URL=... PL_WS_URL=... PL_CHANNEL_ID=... PL_RECEIVERS=20 PL_SEND_RPS=5 \
  PL_DURATION=60 .venv/bin/python loadtest/msg_send.py
"""
import asyncio, json, os, time, statistics, sys
import aiohttp, websockets
from config import (BASE_URL, WS_URL, USER_PREFIX, USER_PASSWORD, USER_COUNT,
                    TOKEN_MODE_BEARER, RESULTS_DIR, GATEWAY_HEARTBEAT, GATEWAY_IDENTIFY)

RECEIVERS = int(os.environ.get("PL_RECEIVERS", "10"))
SEND_RPS = float(os.environ.get("PL_SEND_RPS", "2"))
DURATION = float(os.environ.get("PL_DURATION", "30"))
CHANNEL_ID = os.environ.get("PL_CHANNEL_ID", "")
COMPRESS = os.environ.get("PL_COMPRESS", "1") == "1"

def pct(v,p):
    if not v: return None
    v=sorted(v); return round(v[min(int(len(v)*p/100),len(v)-1)],2)

async def auth(http,i):
    h={"Content-Type":"application/json"}
    if TOKEN_MODE_BEARER: h["X-Plexichat-Token-Mode"]="bearer"
    async with http.post(f"{BASE_URL}/auth/login", json={"username":f"{USER_PREFIX}{i}","password":USER_PASSWORD}, headers=h) as r:
        d=await r.json(content_type=None); tok=d.get("token") or (d.get("data") or {}).get("token")
    ah={"Authorization":f"Bearer {tok}"} if tok else {}
    async with http.post(f"{BASE_URL}/auth/ws-ticket", headers=ah) as r:
        td=await r.json(content_type=None)
    return tok, (td.get("ticket") or (td.get("data") or {}).get("ticket")), ah

async def receiver(http,i,stats,stop,send_t0):
    try:
        _, ticket, _ = await auth(http,i)
        async with websockets.connect(WS_URL, max_size=2**20, open_timeout=15) as ws:
            raw=await asyncio.wait_for(ws.recv(),15); hb=(json.loads(raw).get("d") or {}).get("heartbeat_interval",45000)
            await ws.send(json.dumps({"op":GATEWAY_IDENTIFY,"d":{"token":ticket,"compress":COMPRESS,"properties":{"$browser":"loadtest"}}}))
            async def hbloop():
                while not stop.is_set():
                    await asyncio.sleep(max(hb/1000,5))
                    try: await ws.send(json.dumps({"op":GATEWAY_HEARTBEAT,"d":1}))
                    except: break
            ht=asyncio.create_task(hbloop())
            async for msg in ws:
                if stop.is_set(): break
                d=json.loads(msg)
                if d.get("t")=="READY": stats["rcv_ready"]+=1
                if d.get("t")=="MESSAGE_CREATE":
                    stats["rcv_msg"]+=1
                    # optional e2e: sender embeds ts in content prefix lt:<ms>:
                    c=((d.get("d") or {}).get("content") or "")
                    if c.startswith("lt:"):
                        try: sent=float(c.split(":",2)[1]); stats["e2e_ms"].append((time.time()-sent)*1000)
                        except Exception: pass
            ht.cancel()
    except Exception as e: stats["rcv_err"]+=1

async def sender(http,stats,stop):
    # use user 1 as sender (must have access)
    tok, _, ah = await auth(http,1)
    headers=dict(ah); headers["Content-Type"]="application/json"
    interval=1.0/max(SEND_RPS,0.001); n=0
    while not stop.is_set():
        n+=1; ts=time.time(); body={"content":f"lt:{ts}:msg {n}"}
        t0=time.perf_counter(); status=0; err=None
        try:
            async with http.post(f"{BASE_URL}/channels/{CHANNEL_ID}/messages", json=body, headers=headers) as r:
                status=r.status; await r.read()
        except Exception as e: err=str(e)
        dt=(time.perf_counter()-t0)*1000
        stats["send_ms"].append(dt); stats["sent_total"]+=1
        if err or status>=400: stats["send_err"]+=1
        else: stats["send_ok"]+=1
        await asyncio.sleep(interval)

async def main():
    if not CHANNEL_ID: sys.exit("set PL_CHANNEL_ID (from provision results/state.json)")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stats={"rcv_ready":0,"rcv_msg":0,"rcv_err":0,"sent_total":0,"send_ok":0,"send_err":0,"send_ms":[],"e2e_ms":[]}
    stop=asyncio.Event(); timeout=aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(limit=RECEIVERS+10)) as http:
        rtasks=[asyncio.create_task(receiver(http,(n%max(USER_COUNT,1))+1,stats,stop,time.time())) for n in range(RECEIVERS)]
        await asyncio.sleep(3)
        stask=asyncio.create_task(sender(http,stats,stop))
        t0=time.time(); await asyncio.sleep(DURATION); stop.set()
        await asyncio.gather(stask,*rtasks, return_exceptions=True); elapsed=time.time()-t0
    res={"scenario":"msg_send","channel_id":CHANNEL_ID,"receivers":RECEIVERS,"target_send_rps":SEND_RPS,
         "duration_s":round(elapsed,2),"compress":COMPRESS,
         "sent":{"total":stats["sent_total"],"ok":stats["send_ok"],"err":stats["send_err"],
                 "rps":round(stats["send_ok"]/elapsed,3) if elapsed else None,
                 "lat_ms":{"mean":pct(stats["send_ms"],50),"p95":pct(stats["send_ms"],95),"max":round(max(stats["send_ms"]),2) if stats["send_ms"] else None}},
         "recv":{"ready":stats["rcv_ready"],"message_create_events":stats["rcv_msg"],"errors":stats["rcv_err"],
                 "e2e_ms":{"p50":pct(stats["e2e_ms"],50),"p95":pct(stats["e2e_ms"],95),"max":round(max(stats["e2e_ms"]),2) if stats["e2e_ms"] else None}}}
    out=os.path.join(RESULTS_DIR,"msg_send.json"); json.dump(res,open(out,"w"),indent=2)
    print(json.dumps(res,indent=2)); print(f"wrote {out}")

if __name__=="__main__": asyncio.run(main())
