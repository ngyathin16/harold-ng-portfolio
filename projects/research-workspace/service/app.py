"""Bounded, publication-scoped reads and anonymous query subscriptions."""
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import logging
from pathlib import Path
import secrets
import sys
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolTimeout

from .config import Settings, CapacityUnavailable, check_capacity

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def capability_hash(token, secret):
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def network_identity(request, settings):
    # Until hosted ingress is qualified, all Cloud Run visitors share a bucket.
    # Caller-supplied forwarding headers never establish a quota identity.
    if settings.client_ip_mode == 'shared':
        return 'cloudrun-shared-network'
    return request.client.host if request.client else 'unknown'


def require_publication(conn, publication_id):
    value = conn.execute('SELECT * FROM research.publications WHERE id=%s AND status=\'published\'', (publication_id,)).fetchone()
    if value is None:
        raise HTTPException(404, 'Publication is unavailable. Choose a retained published version.')
    return value


def table_definition(conn, publication_id, name):
    value = conn.execute('SELECT * FROM research.table_definitions WHERE publication_id=%s AND name=%s', (publication_id, name)).fetchone()
    if value is None:
        raise HTTPException(404, 'This table is missing or incompatible in the selected publication.')
    return value


def table_query(definition, publication_id, filters, sort, direction):
    columns = {column['name']: column for column in definition['columns']}
    where, values = [sql.SQL('publication_id=%s')], [publication_id]
    if len(filters) > 12:
        raise HTTPException(422, 'At most twelve filters are supported.')
    for item in filters:
        name, operation = item.get('column'), item.get('op')
        if name not in columns:
            raise HTTPException(422, 'Unknown filter column.')
        column = sql.Identifier(name)
        if operation == 'is_null':
            where.append(sql.SQL('{} IS NULL').format(column))
            continue
        if operation not in ('eq', 'contains', 'gte', 'lte'):
            raise HTTPException(422, 'Unsupported filter operation.')
        value = item.get('value')
        column_type = columns[name]['type'].lower()
        if operation == 'contains':
            if not any(part in column_type for part in ('text', 'char')) or not isinstance(value, str):
                raise HTTPException(422, 'Contains requires a text column and text value.')
            where.append(sql.SQL("{} ILIKE %s ESCAPE '\\'").format(column))
            values.append('%' + value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        else:
            if value is None:
                raise HTTPException(422, 'Use is_null to find unknown values.')
            try:
                if 'int' in column_type:
                    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                        raise ValueError()
                    value = int(value)
                elif any(part in column_type for part in ('real', 'double', 'numeric', 'float')):
                    value = float(value)
                    if not __import__('math').isfinite(value):
                        raise ValueError()
                elif not isinstance(value, str):
                    raise ValueError()
            except (TypeError, ValueError):
                raise HTTPException(422, 'Filter value does not match the column type.') from None
            operator = {'eq': '=', 'gte': '>=', 'lte': '<='}[operation]
            where.append(sql.SQL('{} {} %s').format(column, sql.SQL(operator)))
            values.append(value)
    stable = definition['primary_key'] or list(columns)
    order = []
    if sort:
        if sort not in columns:
            raise HTTPException(422, 'Unknown sort column.')
        order.append(sql.SQL('{} {} NULLS LAST').format(sql.Identifier(sort), sql.SQL(direction.upper())))
    order += [sql.SQL('{} ASC NULLS LAST').format(sql.Identifier(name)) for name in stable if name != sort]
    return sql.SQL(' AND ').join(where), values, sql.SQL(', ').join(order)


def read_table(conn, publication_id, name, page=1, page_size=50, sort=None, direction='asc', filters=(), full=False):
    definition = table_definition(conn, publication_id, name)
    where, values, order = table_query(definition, publication_id, filters, sort, direction)
    table = sql.Identifier('research_data', name)
    total = conn.execute(sql.SQL('SELECT count(*) AS total FROM {} WHERE {}').format(table, where), values).fetchone()['total']
    rows = conn.execute(sql.SQL('SELECT * FROM {} WHERE {} ORDER BY {} LIMIT %s OFFSET %s').format(table, where, order),
                        [*values, page_size, (page - 1) * page_size]).fetchall()
    truncated=[]
    if not full:
        for index,row in enumerate(rows):
            large=[key for key,value in row.items() if isinstance(value,str) and len(value)>4096]
            if large:
                truncated.append({'row':index,'columns':large})
                for key in large:
                    row[key]=row[key][:4096]
    return dict(rows=rows, total=total, page=page, page_size=page_size, columns=definition['columns'], truncated=truncated)


def rate_limit(conn, key, maximum):
    row = conn.execute('''INSERT INTO research.request_limits(key,window_start,hits)
        VALUES (%s,date_trunc('minute',now()),1)
        ON CONFLICT(key,window_start) DO UPDATE SET hits=research.request_limits.hits+1 RETURNING hits''', (key,)).fetchone()
    if row['hits'] > maximum:
        raise HTTPException(429, 'Request limit reached. Retry in one minute.', headers={'Retry-After': '60'})


def require_capacity(settings):
    try:
        check_capacity(settings)
    except CapacityUnavailable as error:
        raise HTTPException(503, str(error), headers={'Retry-After': '60'}) from None


class Submission(BaseModel):
    model_config = ConfigDict(extra='forbid')
    publication_id: str = Field(min_length=1, max_length=160)
    query: dict[str, Any]


class Cancellation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    subscription_id: str = Field(min_length=1, max_length=160)


def create_app(settings=None, pool=None, markers=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        try:
            if app.state.markers is None and settings.deployment_mode == 'serverless':
                from .markers import GCSMarkers
                app.state.markers = GCSMarkers(settings)
            if app.state.pool is None and settings.database_url:
                app.state.pool = ConnectionPool(settings.database_url, min_size=0, max_size=settings.pool_size,
                    timeout=3, kwargs={'row_factory': dict_row}, open=True)
            yield
        finally:
            if pool is None and app.state.pool:
                app.state.pool.close()
            if markers is None and app.state.markers:
                app.state.markers.close()

    app = FastAPI(title='Research workspace', version='1.0.0', lifespan=lifespan)
    app.state.pool = pool
    app.state.markers = markers
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_methods=['GET', 'POST'],
                       allow_headers=['Content-Type', 'X-Session-Token'], expose_headers=['Retry-After'])

    @app.exception_handler(psycopg.Error)
    @app.exception_handler(PoolTimeout)
    async def database_error(request, error):
        return JSONResponse(status_code=503, content={'detail': 'Research data is temporarily unavailable. Your selected version is unchanged; retry shortly.'})

    def connection(request: Request):
        if app.state.pool is None:
            raise HTTPException(503, 'Research service has no database configured.')
        with app.state.pool.connection() as conn:
            conn.execute("SET LOCAL statement_timeout='5000ms'")
            if request.method == 'GET':
                network = capability_hash(network_identity(request, settings),settings.session_secret or 'read-rate-limit')
                rate_limit(conn,'read-network:'+network,600)
                conn.commit()
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                conn.execute("SET LOCAL statement_timeout='5000ms'")
            yield conn

    @app.get('/live')
    def live():
        return {'status': 'ok'}

    @app.get('/health')
    @app.get('/ready')
    def health():
        if app.state.pool is None:
            raise HTTPException(503, 'Research service has no database configured.')
        with app.state.pool.connection() as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            conn.execute("SET LOCAL statement_timeout='1500ms'")
            conn.execute('SELECT 1')
            if settings.deployment_mode == 'serverless':
                stalled = conn.execute("""SELECT EXISTS(SELECT 1 FROM research.query_runs r
                    LEFT JOIN research.dispatch_outbox o ON o.run_id=r.id AND o.generation=r.dispatch_generation
                    WHERE (r.state='queued' AND COALESCE(o.created_at,r.created_at)<now()-interval '15 minutes')
                    OR (r.state='running' AND r.lease_until<now())) AS stalled""").fetchone()['stalled']
                return {'status': 'ok', 'search': 'stalled' if stalled else 'available'}
        return {'status': 'ok'}

    @app.get('/v1/datasets')
    def datasets(conn=Depends(connection)):
        rows = conn.execute('''SELECT d.*,to_jsonb(latest) AS latest_publication FROM research.datasets d
            LEFT JOIN research.publications latest ON latest.id=d.latest_publication_id AND latest.status='published' WHERE EXISTS
            (SELECT 1 FROM research.publications p WHERE p.dataset_id=d.id AND p.status='published') ORDER BY d.name,d.id''').fetchall()
        for row in rows:
            row['capabilities'] = [value for value in row['capabilities'] if value != 'downloads']
        return {'datasets': rows}

    @app.get('/v1/datasets/{dataset_id}/publications')
    def publications(dataset_id: str, conn=Depends(connection)):
        return {'publications': conn.execute("SELECT * FROM research.publications WHERE dataset_id=%s AND status='published' ORDER BY published_at DESC,id", (dataset_id,)).fetchall()}

    @app.get('/v1/publications/{publication_id}')
    def publication(publication_id: str, conn=Depends(connection)):
        return require_publication(conn, publication_id)

    @app.get('/v1/publications/{publication_id}/catalog')
    def catalog(publication_id: str, q: str = Query('', max_length=500), kind: str | None = None,
                cost: int | None = None, trait: str | None = None, shop_only: bool | None = None,
                locked: bool | None = None, item_category: str | None = None, rarity: str | None = None,
                sort: Literal['name','cost','kind'] = 'name', page: int = Query(1, ge=1, le=1000000),
                page_size: int = Query(50, ge=1, le=200), conn=Depends(connection)):
        require_publication(conn, publication_id)
        names = {r['name'] for r in conn.execute('SELECT name FROM research.table_definitions WHERE publication_id=%s', (publication_id,))}
        if 'research_catalog' not in names:
            raise HTTPException(404, 'This dataset supports table browsing but has no catalog adapter.')
        where, args = ['c.publication_id=%s'], [publication_id]
        joins = ''
        entity_columns = ''
        if 'entities' in names:
            joins += ' LEFT JOIN research_data.entities e ON e.publication_id=c.publication_id AND e.entity_id=c.id'
            entity_columns = ',e.lifecycle_status,e.variant_key'
        if 'units' in names:
            joins += ' LEFT JOIN research_data.units u ON u.publication_id=c.publication_id AND u.entity_id=c.id'
        if q:
            term = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            search = "(c.name ILIKE %s OR c.description ILIKE %s OR c.id ILIKE %s"
            args += [term] * 3
            if 'entity_aliases' in names:
                search += ' OR EXISTS (SELECT 1 FROM research_data.entity_aliases a WHERE a.publication_id=c.publication_id AND a.entity_id=c.id AND a.alias ILIKE %s)'
                args.append(term)
            where.append(search + ')')
        if kind:
            where.append('c.kind=%s'); args.append(kind)
        if any(value is not None for value in (cost, shop_only, locked)) and 'units' not in names:
            raise HTTPException(422, 'Unit filters are unsupported for this dataset.')
        for field, value in [('cost',cost),('shop_unit',None if shop_only is None else int(shop_only)),('locked',None if locked is None else int(locked))]:
            if value is not None:
                where.append('u.' + field + '=%s'); args.append(value)
        if trait:
            if 'unit_traits' not in names:
                raise HTTPException(422, 'Trait filters are unsupported for this dataset.')
            where.append('EXISTS (SELECT 1 FROM research_data.unit_traits t WHERE t.publication_id=c.publication_id AND t.unit_id=c.id AND t.trait_id=%s)'); args.append(trait)
        for table, column, value in [('items','category',item_category),('augments','rarity',rarity)]:
            if value is not None:
                if table not in names:
                    raise HTTPException(422, 'This filter is unsupported for this dataset.')
                where.append(f'EXISTS (SELECT 1 FROM research_data.{table} f WHERE f.publication_id=c.publication_id AND f.entity_id=c.id AND f.{column}=%s)'); args.append(value)
        base = ' FROM research_data.research_catalog c' + joins + ' WHERE ' + ' AND '.join(where)
        total = conn.execute('SELECT count(*) AS total' + base, args).fetchone()['total']
        order = {'name':'lower(c.name),c.id','kind':'c.kind,lower(c.name),c.id','cost':'u.cost NULLS LAST,lower(c.name),c.id'}[sort]
        if sort == 'cost' and 'units' not in names:
            raise HTTPException(422, 'Cost sorting is unsupported for this dataset.')
        unit_columns = ',u.cost,u.shop_unit,u.locked' if 'units' in names else ''
        rows = conn.execute('SELECT c.*' + unit_columns + entity_columns + base + ' ORDER BY ' + order + ' LIMIT %s OFFSET %s', [*args,page_size,(page-1)*page_size]).fetchall()
        kinds=[r['kind'] for r in conn.execute('SELECT DISTINCT kind FROM research_data.research_catalog WHERE publication_id=%s ORDER BY kind',(publication_id,))]
        return dict(rows=rows,total=total,page=page,page_size=page_size,kinds=kinds)

    @app.get('/v1/publications/{publication_id}/tables')
    def tables(publication_id: str, conn=Depends(connection)):
        require_publication(conn, publication_id)
        return {'tables': conn.execute('SELECT * FROM research.table_definitions WHERE publication_id=%s ORDER BY name', (publication_id,)).fetchall()}

    @app.get('/v1/publications/{publication_id}/tables/{name}')
    def table(publication_id: str, name: str, page: int = Query(1,ge=1,le=1000000), page_size: int = Query(50,ge=1,le=200),
              sort: str | None = None, direction: Literal['asc','desc'] = 'asc', filters: str = Query('[]',max_length=8000), full: bool = False, conn=Depends(connection)):
        require_publication(conn, publication_id)
        try:
            parsed = json.loads(filters)
            if not isinstance(parsed,list) or any(not isinstance(v,dict) for v in parsed):
                raise ValueError()
        except (ValueError,TypeError):
            raise HTTPException(422, 'Filters must be a JSON array of column, op, value objects.') from None
        if full and page_size>1:
            raise HTTPException(422,'Full field loading requires page_size=1. Select one row to expand.')
        return read_table(conn,publication_id,name,page,page_size,sort,direction,parsed,full)

    @app.get('/v1/publications/{publication_id}/records/{record_id:path}')
    def record(publication_id: str, record_id: str, conn=Depends(connection)):
        require_publication(conn, publication_id)
        item = conn.execute('SELECT * FROM research_data.research_catalog WHERE publication_id=%s AND id=%s', (publication_id,record_id)).fetchone()
        if item is None:
            raise HTTPException(404, 'This record is unavailable in the selected snapshot. No name-based substitute was selected.')
        definitions = {r['name']:r for r in conn.execute('SELECT * FROM research.table_definitions WHERE publication_id=%s', (publication_id,))}
        sections = {}
        owners = {'entities':'entity_id','units':'entity_id','unit_stats':'entity_id','abilities':'unit_id','unit_traits':'unit_id',
                  'unlock_rules':'unit_id','traits':'entity_id','trait_breakpoints':'trait_id','items':'entity_id','item_recipes':'result_id',
                  'augments':'entity_id','effect_values':'entity_id','curated_records':'record_id'}
        for name,column in owners.items():
            if name in definitions:
                found = read_table(conn,publication_id,name,page_size=200,filters=[{'column':column,'op':'eq','value':record_id}])
                if found['total']:
                    sections[name] = found['rows']
        if 'unit_traits' in definitions and item['kind']=='trait':
            sections['members'] = read_table(conn,publication_id,'unit_traits',page_size=200,filters=[{'column':'trait_id','op':'eq','value':record_id}])['rows']
        evidence = read_table(conn,publication_id,'v_evidence',page_size=200,filters=[{'column':'entity_id','op':'eq','value':record_id}]) if 'v_evidence' in definitions else {'rows':[],'total':0}
        source = conn.execute('SELECT * FROM research_data.source_documents WHERE publication_id=%s AND source_id=%s',(publication_id,item['source_id'])).fetchone() if 'source_documents' in definitions else None
        if 'source_documents' in definitions:
            source_ids=sorted({row['source_id'] for row in evidence['rows']})
            documents={row['source_id']:row for row in conn.execute('SELECT * FROM research_data.source_documents WHERE publication_id=%s AND source_id=ANY(%s)',(publication_id,source_ids))}
            for row in evidence['rows']:
                document=documents.get(row['source_id'],{})
                row.update({key:document.get(key) for key in ('title','status','published_at','evidence_origin','stale')})
        gaps = read_table(conn,publication_id,'field_gaps',page_size=200,filters=[{'column':'entity_id','op':'eq','value':record_id}]) if 'field_gaps' in definitions else {'rows':[],'total':0}
        relationships = []
        if 'entity_relationships' in definitions:
            relationships = conn.execute('''SELECT * FROM research_data.entity_relationships WHERE publication_id=%s
                AND (source_entity_id=%s OR target_entity_id=%s) ORDER BY source_entity_id,target_entity_id,relationship LIMIT 200''',(publication_id,record_id,record_id)).fetchall()
        return dict(record=item,sections=sections,evidence=evidence['rows'],evidence_total=evidence['total'],source=source,
                    gaps=gaps['rows'],gaps_total=gaps['total'],relationships=relationships)

    @app.get('/v1/publications/{publication_id}/coverage')
    def coverage(publication_id: str, conn=Depends(connection)):
        published = require_publication(conn, publication_id)
        names = {r['name'] for r in conn.execute('SELECT name FROM research.table_definitions WHERE publication_id=%s',(publication_id,))}
        result = {'validation':published['manifest'].get('validation'), 'scope':published['scope'], 'issue_counts':{}}
        for label,name in [('coverage','coverage_entries'),('discrepancies','discrepancies'),('source_health','source_documents')]:
            data = read_table(conn,publication_id,name,page_size=200) if name in names else {'rows':[],'total':0}
            result[label] = data['rows']; result['issue_counts'][name] = data['total']
        if 'field_gaps' in names:
            result['issue_counts']['field_gaps'] = table_definition(conn,publication_id,'field_gaps')['row_count']
        return result

    @app.get('/v1/publications/{publication_id}/rules')
    def rules(publication_id: str, version: str | None = None, conn=Depends(connection)):
        require_publication(conn,publication_id)
        row = conn.execute('SELECT rules FROM research.rulesets WHERE publication_id=%s AND (%s::text IS NULL OR version=%s) ORDER BY created_at DESC,version LIMIT 1', (publication_id,version,version)).fetchone()
        if row is None:
            raise HTTPException(404,'The requested historical ruleset is unavailable, or this dataset does not support team queries.')
        return row['rules']

    @app.post('/v1/queries',status_code=202)
    def submit(body: Submission, request: Request, x_session_token: str | None = Header(None), conn=Depends(connection)):
        if not settings.session_secret:
            raise HTTPException(503,'Query admission is not configured.')
        if len(canonical(body.query)) > 12000:
            raise HTTPException(413,'Query definition is too large.')
        publication = require_publication(conn,body.publication_id)
        version = body.query.get('ruleset_version')
        rules_row = conn.execute('SELECT * FROM research.rulesets WHERE publication_id=%s AND (%s::text IS NULL OR version=%s) ORDER BY created_at DESC,version LIMIT 1',(body.publication_id,version,version)).fetchone()
        if rules_row is None:
            raise HTTPException(409,'Requested ruleset is unavailable; select a retained ruleset explicitly.')
        from solver import normalize_query, SOLVER_VERSION
        scope={'publication_id':body.publication_id,'dataset_id':publication['dataset_id'],'snapshot_id':publication['snapshot_id'],'ruleset_version':rules_row['version']}
        if any(key in body.query and body.query[key]!=value for key,value in scope.items()):
            raise HTTPException(422,'Saved query scope differs from the selected publication. Select its original snapshot and ruleset.')
        query = {**scope,**body.query}
        try:
            normalized = normalize_query(query,rules_row['rules'])
        except (ValueError,TypeError,KeyError) as error:
            raise HTTPException(422,str(error)) from None
        token = x_session_token or secrets.token_urlsafe(32)
        if len(token) < 32 or len(token) > 128:
            raise HTTPException(422,'Invalid session capability.')
        session = capability_hash(token,settings.session_secret)
        network = capability_hash(network_identity(request, settings),settings.session_secret)
        rate_limit(conn,'submit-session:'+session,settings.max_requests_session)
        rate_limit(conn,'submit-network:'+network,settings.max_requests_network)
        key = hashlib.sha256(canonical({'query':normalized,'solver_version':SOLVER_VERSION}).encode()).hexdigest()
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('research-query-admission',))
        existing = conn.execute('SELECT * FROM research.query_runs WHERE query_hash=%s FOR UPDATE',(key,)).fetchone()
        active = conn.execute('''SELECT count(*) AS total FROM research.query_subscriptions s JOIN research.query_runs r ON r.id=s.run_id
            WHERE s.session_hash=%s AND s.active AND r.state IN ('queued','running') AND r.query_hash<>%s''',(session,key)).fetchone()['total']
        if active >= settings.max_active_session:
            raise HTTPException(429,'You already have the maximum number of active queries. Wait or cancel a subscription.')
        reusable = existing and (existing['state'] in ('queued','running') or (existing['state']=='complete' and existing['expires_at'] is not None and conn.execute('SELECT %s > now() AS valid',(existing['expires_at'],)).fetchone()['valid']))
        if not reusable:
            require_capacity(settings)
            queued = conn.execute("SELECT count(*) AS total FROM research.query_runs WHERE state IN ('queued','running')").fetchone()['total']
            if queued >= settings.max_queued:
                raise HTTPException(503,'Team search is at capacity. Browsing remains available; retry shortly.',headers={'Retry-After':'30'})
            if existing:
                run_id = existing['id']
                conn.execute('UPDATE research.query_subscriptions SET active=false WHERE run_id=%s',(run_id,))
                conn.execute("INSERT INTO research.query_artifact_gc(storage_key,delete_after) SELECT storage_key,now()+interval '2 minutes' FROM research.query_artifacts WHERE run_id=%s ON CONFLICT DO NOTHING",(run_id,))
                conn.execute('DELETE FROM research.query_artifacts WHERE run_id=%s',(run_id,))
                conn.execute('DELETE FROM research.query_results WHERE run_id=%s',(run_id,))
                conn.execute("UPDATE research.query_runs SET state='queued',attempts=0,result_count=0,champion_set_count=NULL,progress='{}',error=NULL,started_at=NULL,finished_at=NULL,lease_owner=NULL,lease_until=NULL,expires_at=NULL WHERE id=%s",(run_id,))
            else:
                run_id = secrets.token_urlsafe(18)
                conn.execute('''INSERT INTO research.query_runs(id,publication_id,ruleset_version,solver_version,query_hash,definition)
                    VALUES (%s,%s,%s,%s,%s,%s)''',(run_id,body.publication_id,rules_row['version'],SOLVER_VERSION,key,Jsonb(normalized)))
            state = 'queued'
            if settings.deployment_mode == 'serverless':
                from .admission import reserve
                from .dispatch import enqueue
                from .markers import MarkerUnavailable
                try:
                    reserve(conn, run_id, settings)
                    generation = enqueue(conn, run_id, settings, app.state.markers)
                except (CapacityUnavailable, MarkerUnavailable) as error:
                    raise HTTPException(503, str(error), headers={'Retry-After': '60'}) from None
        else:
            run_id,state = existing['id'],existing['state']
        subscription = conn.execute('''INSERT INTO research.query_subscriptions(id,run_id,session_hash) VALUES (%s,%s,%s)
            ON CONFLICT(run_id,session_hash) DO UPDATE SET active=true RETURNING id''',(secrets.token_urlsafe(18),run_id,session)).fetchone()['id']
        if settings.deployment_mode == 'serverless':
            conn.commit()
        if settings.deployment_mode == 'serverless' and not reusable:
            from .cloud import wake
            try:
                wake(settings, run_id, generation)
            except Exception:
                # Durable intent survives task API failures and process termination.
                logging.warning('Dispatch wakeup pending for query %s generation %s', run_id, generation)
        return {'query_id':run_id,'state':state,'subscription_id':subscription,'session_token':token,'query':normalized}

    def get_run(conn,query_id):
        run = conn.execute('''SELECT r.* FROM research.query_runs r JOIN research.publications p ON p.id=r.publication_id
            WHERE r.id=%s AND p.status='published' ''',(query_id,)).fetchone()
        if not run:
            raise HTTPException(404,'This query is unavailable.')
        return run

    @app.get('/v1/queries/{query_id}')
    def query_status(query_id: str, conn=Depends(connection)):
        run = get_run(conn,query_id)
        expired = run['expires_at'] is not None and conn.execute('SELECT %s <= now() AS expired',(run['expires_at'],)).fetchone()['expired']
        progress = {key: value for key, value in run['progress'].items() if not key.startswith('artifact_')}
        return {'query_id':run['id'],'publication_id':run['publication_id'],'state':run['state'],'query':run['definition'],
                'ruleset_version':run['ruleset_version'],'solver_version':run['solver_version'],'progress':progress,
                'result_count':run['result_count'],'champion_set_count':run['champion_set_count'],'complete':run['state']=='complete' and not expired,
                'error':run['error'],'created_at':run['created_at'],'started_at':run['started_at'],'finished_at':run['finished_at'],'expired':expired}

    @app.get('/v1/queries/{query_id}/results')
    def results(query_id: str, page: int = Query(1,ge=1,le=1000000),page_size: int = Query(50,ge=1,le=200),
                sort: Literal['active_traits','total_cost','champions'] = 'active_traits',conn=Depends(connection)):
        run = get_run(conn,query_id)
        if run['expires_at'] and conn.execute('SELECT %s <= now() AS expired',(run['expires_at'],)).fetchone()['expired']:
            raise HTTPException(410,'Result cache expired. Submit the saved query to recompute against the same rules.')
        complete = run['state']=='complete'
        order = {'active_traits':'active_trait_count DESC','total_cost':'total_cost NULLS LAST','champions':'champion_names'}[sort] if complete else 'discovery_index'
        rows = conn.execute('SELECT result FROM research.query_results WHERE run_id=%s ORDER BY '+order+',configuration_key LIMIT %s OFFSET %s',(query_id,page_size,(page-1)*page_size)).fetchall()
        return dict(rows=[r['result'] for r in rows],total=run['result_count'],page=page,page_size=page_size,complete=complete,state=run['state'])

    @app.post('/v1/queries/{query_id}/cancel')
    def cancel(query_id: str,body: Cancellation,x_session_token: str | None = Header(None),conn=Depends(connection)):
        get_run(conn,query_id)
        if not x_session_token or not settings.session_secret:
            raise HTTPException(403,'Only the original session can cancel its own subscription.')
        session = capability_hash(x_session_token,settings.session_secret)
        conn.execute('SELECT id FROM research.query_runs WHERE id=%s FOR UPDATE',(query_id,))
        changed = conn.execute('''UPDATE research.query_subscriptions SET active=false WHERE id=%s AND run_id=%s AND session_hash=%s RETURNING id''',(body.subscription_id,query_id,session)).fetchone()
        if not changed:
            raise HTTPException(403,'This session does not own that subscription.')
        remaining = conn.execute('SELECT count(*) AS total FROM research.query_subscriptions WHERE run_id=%s AND active',(query_id,)).fetchone()['total']
        if remaining == 0:
            conn.execute("UPDATE research.query_runs SET state='cancelled',finished_at=now(),lease_owner=NULL,lease_until=NULL,expires_at=now()+(%s*interval '1 day'),error='All subscribers cancelled. Results are incomplete.' WHERE id=%s AND state IN ('queued','running')",(settings.result_retention_days,query_id))
        if settings.deployment_mode == 'serverless':
            conn.commit()
        return {'subscription_id':body.subscription_id,'cancelled':True,'other_subscribers_active':remaining>0}

    return app


app = create_app()
