"""Measure a hosted API without pretending cached searches are uncached work."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import random
import statistics
import time

import httpx


def percentile(values,percent):
    if not values:
        return None
    return sorted(values)[min(len(values)-1,int((len(values)-1)*percent))]


async def measure(args):
    queries=json.loads(Path(args.queries).read_text(encoding='utf-8'))
    if not isinstance(queries,list) or len(queries)!=2 or queries[0]==queries[1]:
        raise ValueError('Provide exactly two distinct submission bodies in --queries JSON')
    latencies=[]; statuses=Counter(); errors=Counter(); polls=[]; submissions=[]
    started=time.monotonic()
    async with httpx.AsyncClient(base_url=args.api.rstrip('/'),timeout=15,limits=httpx.Limits(max_connections=args.readers+4)) as client:
        for definition in queries:
            began=time.monotonic()
            response=await client.post('/v1/queries',json=definition)
            response.raise_for_status()
            run=response.json()
            if run['state']=='complete':
                raise ValueError('A query reused a completed cache. Use a fresh fixture publication or expire its cache administratively before measuring uncached execution.')
            submissions.append({'query_id':run['query_id'],'seconds':time.monotonic()-began,'state':run['state']})
        async def reader(index):
            rng=random.Random(index)
            await asyncio.sleep(rng.random()*min(args.interval,3))
            iteration=0
            while time.monotonic()-started<args.seconds:
                publication=queries[iteration%2]['publication_id']
                paths=[f'/v1/publications/{publication}/catalog?page=1&page_size=25',
                       f'/v1/publications/{publication}/catalog?q=Ahri&page_size=25',
                       f'/v1/publications/{publication}/tables',
                       f'/v1/publications/{publication}/coverage']
                began=time.monotonic()
                try:
                    response=await client.get(paths[iteration%len(paths)])
                    statuses[str(response.status_code)]+=1
                    if response.is_success:
                        latencies.append(time.monotonic()-began)
                    else:
                        errors['http_'+str(response.status_code)]+=1
                except httpx.HTTPError as error:
                    errors[type(error).__name__]+=1
                iteration+=1
                await asyncio.sleep(args.interval)
        async def monitor():
            while time.monotonic()-started<args.seconds:
                for submission in submissions:
                    try:
                        response=await client.get('/v1/queries/'+submission['query_id'])
                        if response.is_success:
                            run=response.json()
                            polls.append({'elapsed':time.monotonic()-started,**{k:run.get(k) for k in ('query_id','state','complete','result_count','created_at','started_at','finished_at','error')}})
                    except httpx.HTTPError as error:
                        errors['poll_'+type(error).__name__]+=1
                await asyncio.sleep(5)
        await asyncio.gather(*(reader(i) for i in range(args.readers)),monitor())
    elapsed=time.monotonic()-started
    final={row['query_id']:row for row in polls}
    report={'recorded_at':datetime.now(timezone.utc).isoformat(),'api':args.api,'seconds':elapsed,
            'readers':args.readers,'interval_seconds':args.interval,'successful_reads':len(latencies),'response_statuses':dict(statuses),
            'errors':dict(errors),'successful_reads_per_second':len(latencies)/elapsed,
            'read_p50_seconds':percentile(latencies,.5),'read_p95_seconds':percentile(latencies,.95),
            'read_max_seconds':max(latencies,default=None),'submissions':submissions,'final_queries':list(final.values()),
            'query_observations':polls,
            'limits':'HTTP measurements only. Record worker CPU/RSS, database connections/disk and object egress from host monitoring separately.'}
    report['passed']=bool(latencies) and not errors and report['read_p95_seconds']<=1 and all(x['seconds']<=2 for x in submissions) and len(final)==2 and all(row['complete'] for row in final.values())
    for row in final.values():
        if row['started_at'] and row['finished_at']:
            row['execution_seconds']=(datetime.fromisoformat(row['finished_at'])-datetime.fromisoformat(row['started_at'])).total_seconds()
            row['queue_wait_seconds']=(datetime.fromisoformat(row['started_at'])-datetime.fromisoformat(row['created_at'])).total_seconds()
            if row['execution_seconds']>120:
                report['passed']=False
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:report[key] for key in ('passed','seconds','successful_reads','errors','read_p95_seconds')},indent=2))
    return 0 if report['passed'] else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api',required=True)
    parser.add_argument('--queries',required=True,help='JSON array of two full POST /v1/queries bodies against fresh retained fixtures')
    parser.add_argument('--readers',type=int,default=50)
    parser.add_argument('--seconds',type=int,default=600)
    parser.add_argument('--interval',type=float,default=6)
    parser.add_argument('--output',default='verification/load.json')
    args=parser.parse_args()
    if args.readers<1 or args.seconds<1 or args.interval<=0:
        parser.error('Readers, seconds and interval must be positive')
    return asyncio.run(measure(args))


if __name__=='__main__':
    raise SystemExit(main())
