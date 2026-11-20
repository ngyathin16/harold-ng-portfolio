"""API boundary tests and optional real PostgreSQL queue integration."""
from contextlib import contextmanager
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from service.app import create_app,table_query
from service.config import Settings
from service.worker import claim,execute,cleanup


def test_unconfigured_service_is_unavailable_not_empty():
    with TestClient(create_app(Settings())) as client:
        response=client.get('/v1/datasets')
        assert response.status_code==503
        assert 'detail' in response.json()


def test_table_filters_bind_values_and_reject_unknown_identifiers():
    definition={'columns':[{'name':'name','type':'TEXT'},{'name':'cost','type':'INTEGER'}],'primary_key':['name']}
    where,args,order=table_query(definition,'pub',[{'column':'name','op':'contains','value':"'; DROP TABLE units; --"}],None,'asc')
    assert 'DROP' not in where.as_string()
    assert 'DROP' in args[1]
    assert order.as_string()=='"name" ASC NULLS LAST'
    for filters in [[{'column':'name; DROP TABLE units','op':'eq','value':'x'}],[{'column':'cost','op':'gte','value':'invalid'}]]:
        with pytest.raises(HTTPException):
            table_query(definition,'pub',filters,None,'asc')


@pytest.fixture
def service():
    url=os.environ.get('RESEARCH_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set RESEARCH_TEST_DATABASE_URL to a migrated PostgreSQL test database')
    identity='service-test-'+uuid.uuid4().hex
    rules={'dataset_id':identity,'snapshot_id':'fixture-v1','publication_id':identity,'version':'fixture-rules',
           'assumptions':[],
           'units':[{'form_id':str(i),'unit_id':str(i),'champion_id':str(i),'name':'Unit '+str(i),
                     'cost':i+1,'slots':1,'locked':False,'traits':['trait'],'evidence':[]} for i in range(3)],
           'traits':[{'trait_id':'trait','name':'Trait','breakpoints':[{'breakpoint_id':'two','minimum':2,'status':'supported','evidence':[]}]}]}
    with ConnectionPool(url,min_size=1,max_size=4,kwargs={'row_factory':dict_row}) as pool:
        with pool.connection() as conn:
            conn.execute("INSERT INTO research.datasets(id,name,description,adapter,capabilities) VALUES (%s,'Fixture','Test fixture','tft_set19','[]')",(identity,))
            conn.execute("""INSERT INTO research.publications(id,dataset_id,snapshot_id,status,manifest,scope,source_sha256,bundle_sha256,schema_fingerprint,published_at)
                VALUES (%s,%s,'fixture-v1','published','{}','{}','fixture','fixture','fixture',now())""",(identity,identity))
            conn.execute('INSERT INTO research.rulesets(publication_id,version,rules) VALUES (%s,%s,%s)',(identity,rules['version'],Jsonb(rules)))
        settings=Settings(database_url=url,session_secret=identity,max_requests_network=100,max_requests_session=100)
        try:
            with TestClient(create_app(settings,pool=pool)) as client:
                yield client,pool,settings,identity,rules
        finally:
            with pool.connection() as conn:
                conn.execute('DELETE FROM research.query_subscriptions WHERE run_id IN (SELECT id FROM research.query_runs WHERE publication_id=%s)',(identity,))
                conn.execute('DELETE FROM research.query_runs WHERE publication_id=%s',(identity,))
                conn.execute('DELETE FROM research.rulesets WHERE publication_id=%s',(identity,))
                conn.execute('DELETE FROM research.publications WHERE id=%s',(identity,))
                conn.execute('DELETE FROM research.datasets WHERE id=%s',(identity,))


def submission(client,publication,**query):
    response=client.post('/v1/queries',json={'publication_id':publication,'query':{'team_size':{'min':2,'max':2},**query}})
    assert response.status_code==202,response.text
    return response.json()


def test_identical_jobs_share_work_but_not_cancellation(service):
    client,pool,settings,pub,rules=service
    first=submission(client,pub,include=['1','0'])
    second=submission(client,pub,include=['0','1'])
    assert first['query_id']==second['query_id']
    assert first['subscription_id']!=second['subscription_id']
    path='/v1/queries/'+first['query_id']+'/cancel'
    assert client.post(path,json={'subscription_id':first['subscription_id']},headers={'X-Session-Token':second['session_token']}).status_code==403
    response=client.post(path,json={'subscription_id':first['subscription_id']},headers={'X-Session-Token':first['session_token']})
    assert response.json()['other_subscribers_active'] is True
    assert client.get('/v1/queries/'+first['query_id']).json()['state']=='queued'
    client.post(path,json={'subscription_id':second['subscription_id']},headers={'X-Session-Token':second['session_token']})
    assert client.get('/v1/queries/'+first['query_id']).json()['state']=='cancelled'


def test_worker_restart_export_and_expired_cache(service):
    client,pool,settings,pub,rules=service
    submitted=submission(client,pub,min_active_traits=1)
    run=claim(pool,settings)
    assert run['id']==submitted['query_id']
    with pool.connection() as conn:
        conn.execute("UPDATE research.query_runs SET lease_until=now()-interval '1 second' WHERE id=%s",(run['id'],))
    resumed=claim(pool,settings)
    assert resumed['lease_owner']!=run['lease_owner']
    execute(pool,settings,run)
    assert client.get('/v1/queries/'+run['id']).json()['state']=='running'
    execute(pool,settings,resumed)
    status=client.get('/v1/queries/'+run['id']).json()
    assert status['complete'] and status['result_count']==3
    results=client.get('/v1/queries/'+run['id']+'/results?page_size=2').json()
    assert results['total']==3 and len(results['rows'])==2
    exported=client.get('/v1/queries/'+run['id']+'/export').json()
    assert len(exported['results'])==exported['provenance']['result_count']==3
    assert len({row['configuration_key'] for row in exported['results']})==3
    assert client.get('/v1/queries/'+run['id']+'/export?format=csv').text.count('\n')==5
    with pool.connection() as conn:
        conn.execute("UPDATE research.query_runs SET expires_at=now()-interval '1 second' WHERE id=%s",(run['id'],))
    cleanup(pool)
    assert client.get('/v1/queries/'+run['id']+'/results').status_code==410
    again=submission(client,pub,min_active_traits=1)
    assert again['query_id']==run['id'] and again['state']=='queued'
    cancelled=client.post('/v1/queries/'+run['id']+'/cancel',json={'subscription_id':again['subscription_id']},headers={'X-Session-Token':again['session_token']})
    assert cancelled.json()['other_subscribers_active'] is False
    assert client.get('/v1/queries/'+run['id']).json()['state']=='cancelled'


def test_draft_scope_and_contradictions_rejected(service):
    client,pool,settings,pub,rules=service
    invalid=client.post('/v1/queries',json={'publication_id':pub,'query':{'include':['0'],'exclude':['0']}})
    assert invalid.status_code==422
    for filters in ('{}','[null]','[1]','broken'):
        assert client.get('/v1/publications/'+pub+'/tables/entities',params={'filters':filters}).status_code==422
    for field in ('dataset_id','snapshot_id','publication_id'):
        mismatch=client.post('/v1/queries',json={'publication_id':pub,'query':{field:'another-version'}})
        assert mismatch.status_code==422
    with pool.connection() as conn:
        conn.execute("UPDATE research.publications SET status='draft',published_at=NULL WHERE id=%s",(pub,))
    assert client.get('/v1/publications/'+pub).status_code==404
    assert client.post('/v1/queries',json={'publication_id':pub,'query':{}}).status_code==404


def test_unknown_costs_and_resource_limits_remain_explicit(service):
    client,pool,settings,pub,rules=service
    rules['units'][0]['cost']=None
    with pool.connection() as conn:
        conn.execute('UPDATE research.rulesets SET rules=%s WHERE publication_id=%s',(Jsonb(rules),pub))
    submitted=submission(client,pub,include=['0'])
    run=claim(pool,settings)
    execute(pool,replace(settings,max_results=1),run)
    status=client.get('/v1/queries/'+run['id']).json()
    assert status['state']=='resource_limited' and status['complete'] is False
    result=client.get('/v1/queries/'+run['id']+'/results').json()['rows'][0]
    assert result['total_cost'] is None
    assert client.get('/v1/queries/'+run['id']+'/export').status_code==409


def test_export_snapshot_survives_concurrent_recompute(service):
    client,pool,settings,pub,rules=service
    submitted=submission(client,pub)
    run=claim(pool,settings)
    execute(pool,settings,run)
    endpoint=next(route.endpoint for route in client.app.routes if getattr(route,'path','')=='/v1/queries/{query_id}/export')
    request=Request({'type':'http','method':'GET','path':'/','headers':[],'client':('snapshot-test',123)})
    response=endpoint(run['id'],request,format='json')
    with pool.connection() as conn:
        conn.execute('DELETE FROM research.query_results WHERE run_id=%s',(run['id'],))
        conn.execute("UPDATE research.query_runs SET state='queued',result_count=0 WHERE id=%s",(run['id'],))
    async def consume():
        chunks=[]
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        await response.background()
        return ''.join(chunks)
    exported=json.loads(asyncio.run(consume()))
    assert exported['provenance']['state']=='complete'
    assert len(exported['results'])==exported['provenance']['result_count']==3


def test_active_session_quota_does_not_drop_accepted_jobs(service):
    client,pool,settings,pub,rules=service
    first=submission(client,pub,include=['0'])
    headers={'X-Session-Token':first['session_token']}
    for unit in ('1','2'):
        assert client.post('/v1/queries',headers=headers,json={'publication_id':pub,'query':{'team_size':{'min':2,'max':2},'include':[unit]}}).status_code==202
    assert client.post('/v1/queries',headers=headers,json={'publication_id':pub,'query':{'team_size':{'min':1,'max':1}}}).status_code==429
    assert client.get('/v1/queries/'+first['query_id']).json()['state']=='queued'


def test_worker_failure_retains_only_bounded_partial_results(service,monkeypatch):
    client,pool,settings,pub,rules=service
    submitted=submission(client,pub)
    run=claim(pool,settings)
    def failed(*args,**kwargs):
        raise RuntimeError('synthetic-sensitive-connection-detail')
    monkeypatch.setattr('service.worker.solve',failed)
    execute(pool,settings,run)
    response=client.get('/v1/queries/'+submitted['query_id'])
    assert response.json()['state']=='failed' and not response.json()['complete']
    assert 'synthetic-sensitive' not in response.text
    with pool.connection() as conn:
        assert conn.execute('SELECT expires_at>now() AS bounded FROM research.query_runs WHERE id=%s',(run['id'],)).fetchone()['bounded']


def test_migrated_roles_enforce_source_and_job_permissions(service):
    client,pool,settings,pub,rules=service
    run_id=submission(client,pub)['query_id']
    insert_result='''INSERT INTO research.query_results(run_id,configuration_key,champion_set_key,
        active_trait_count,total_cost,champion_names,result)
        VALUES (%s,'permissions-fixture','permissions-fixture',0,2,'Unit 0','{}') RETURNING run_id'''
    for role in ('research_api','research_worker'):
        with pool.connection() as conn,conn.transaction():
            conn.execute(psycopg.sql.SQL('SET LOCAL ROLE {}').format(psycopg.sql.Identifier(role)))
            assert conn.execute('SELECT current_user AS role').fetchone()['role']==role
            assert conn.execute('SELECT id FROM research.publications WHERE id=%s',(pub,)).fetchone()['id']==pub
            for statement in (
                "UPDATE research.publications SET manifest='{}' WHERE id=%s",
                'UPDATE research_data.units SET cost=9 WHERE publication_id=%s',
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege),conn.transaction():
                    conn.execute(statement,(pub,))
            if role=='research_api':
                with pytest.raises(psycopg.errors.InsufficientPrivilege),conn.transaction():
                    conn.execute(insert_result,(run_id,))
            else:
                assert conn.execute(insert_result,(run_id,)).fetchone()['run_id']==run_id
                assert conn.execute('SELECT count(*) AS total FROM research.query_results WHERE run_id=%s',(run_id,)).fetchone()['total']==1
    with pool.connection() as conn:
        conn.execute("UPDATE research.publications SET status='draft',published_at=NULL WHERE id=%s",(pub,))
    with pool.connection() as conn,conn.transaction():
        conn.execute('SET LOCAL ROLE anon')
        assert conn.execute('SELECT id FROM research.publications WHERE id=%s',(pub,)).fetchone() is None
        assert conn.execute('SELECT id FROM research.datasets WHERE id=%s',(pub,)).fetchone() is None
        with pytest.raises(psycopg.errors.InsufficientPrivilege),conn.transaction():
            conn.execute('SELECT * FROM research.query_runs WHERE id=%s',(run_id,))
