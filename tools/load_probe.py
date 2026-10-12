"""Authenticated, bounded load probe. References are evidence, never an SLA contract."""
import asyncio
import json
import os
import statistics
import time

import httpx

BASE_URL=os.getenv("NEXORA_BASE_URL","http://127.0.0.1:8090")
TOKEN=os.environ["NEXORA_ACCESS_TOKEN"]
TENANT=os.environ["NEXORA_TENANT_ID"]
REQUESTS=int(os.getenv("NEXORA_LOAD_REQUESTS","200"))
CONCURRENCY=min(int(os.getenv("NEXORA_LOAD_CONCURRENCY","10")),50)


async def main()->None:
    semaphore=asyncio.Semaphore(CONCURRENCY);latencies=[];statuses={}
    async with httpx.AsyncClient(base_url=BASE_URL,headers={"Authorization":f"Bearer {TOKEN}","X-Tenant-ID":TENANT},timeout=15) as client:
        async def one(index:int)->None:
            path=("/api/v1/alerts","/api/v1/incidents","/api/v1/asset-topology")[index%3]
            async with semaphore:
                started=time.perf_counter();response=await client.get(path);latencies.append((time.perf_counter()-started)*1000)
                statuses[response.status_code]=statuses.get(response.status_code,0)+1
        await asyncio.gather(*(one(index) for index in range(REQUESTS)))
    ordered=sorted(latencies)
    result={"requests":REQUESTS,"concurrency":CONCURRENCY,"status_counts":statuses,"error_rate":sum(v for k,v in statuses.items() if k>=400)/REQUESTS,
            "latency_ms":{"p50":statistics.median(ordered),"p95":ordered[int(len(ordered)*.95)-1],"p99":ordered[int(len(ordered)*.99)-1],"max":max(ordered)}}
    print(json.dumps(result,indent=2))


if __name__=="__main__":asyncio.run(main())
