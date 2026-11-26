"""Hosted HTTP deadlines stop synchronous work even after client cancellation."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from service.api_deadline import APIRequestDeadline


PROGRAM = '''
import asyncio, sys, threading, time
from service.api_deadline import APIRequestDeadline
mode=sys.argv[1]
entered=threading.Event()
peer_entered=threading.Event()
async def application(scope, receive, send):
    if mode=='normal':
        await asyncio.to_thread(time.sleep,0.01)
        return
    if mode=='raised':
        raise ValueError('application error')
    if mode=='blocked_loop':
        time.sleep(30)
        return
    def sync_work():
        entered.set()
        print('SYNC_WORK_ENTERED',flush=True)
        time.sleep(30)
    await asyncio.to_thread(sync_work)
async def main():
    app=APIRequestDeadline(application,enabled=mode!='disabled',seconds=0.15)
    if mode=='cancelled':
        task=asyncio.create_task(app({'type':'http'},None,None))
        while not entered.is_set(): await asyncio.sleep(0.001)
        task.cancel()
        try: await task
        except asyncio.CancelledError: print('AWAIT_CANCELLED',flush=True)
        await asyncio.sleep(30)
    elif mode=='peer':
        async def short(scope,receive,send):
            await asyncio.sleep(0.02)
        first=asyncio.create_task(app({'type':'http'},None,None))
        await APIRequestDeadline(short,enabled=True,seconds=0.15)({'type':'http'},None,None)
        print('PEER_COMPLETED',flush=True)
        await first
    elif mode=='disabled':
        async def slow(scope,receive,send): await asyncio.sleep(0.25)
        await APIRequestDeadline(slow,enabled=False,seconds=0.05)({'type':'http'},None,None)
    elif mode=='lifespan':
        async def startup(scope,receive,send): await asyncio.sleep(0.25)
        await APIRequestDeadline(startup,enabled=True,seconds=0.05)({'type':'lifespan'},None,None)
    else:
        try: await app({'type':'http'},None,None)
        except ValueError: print('ERROR_ESCAPED',flush=True)
        await asyncio.sleep(0.3)
asyncio.run(main())
print('NORMAL_EXIT',flush=True)
'''


@pytest.mark.parametrize('mode,exit_code,marker',[
    ('normal',0,'NORMAL_EXIT'),
    ('disabled',0,'NORMAL_EXIT'),
    ('lifespan',0,'NORMAL_EXIT'),
    ('blocked',124,'SYNC_WORK_ENTERED'),
    ('blocked_loop',124,None),
    ('cancelled',124,'AWAIT_CANCELLED'),
    ('raised',124,'ERROR_ESCAPED'),
    ('peer',124,'PEER_COMPLETED'),
])
def test_real_process_stops_lingering_work_but_preserves_completed_requests(mode,exit_code,marker):
    environment={k:v for k,v in os.environ.items() if not k.startswith(('RESEARCH_','GOOGLE_'))}
    result=subprocess.run([sys.executable,'-c',PROGRAM,mode],cwd=Path(__file__).resolve().parents[1],
        env=environment,capture_output=True,text=True,timeout=5)
    assert result.returncode==exit_code,(result.stdout,result.stderr)
    if marker: assert marker in result.stdout


@pytest.mark.parametrize('seconds',[0,-1,16,float('nan'),float('inf'),True,'15'])
def test_invalid_deadline_rejected(seconds):
    with pytest.raises(ValueError): APIRequestDeadline(None,enabled=True,seconds=seconds)


def test_hosted_enablement_must_be_explicit_boolean():
    with pytest.raises(ValueError): APIRequestDeadline(None,enabled='true')


@pytest.mark.parametrize('hosted', [False, True])
def test_actual_api_wraps_budget_work_and_leaves_local_execution_unlimited(hosted):
    code = '''
import asyncio, os, sys, time
from service.api_deadline import APIRequestDeadline
original=APIRequestDeadline.__init__
def short(self, app, *, enabled, seconds=15):
    original(self, app, enabled=enabled, seconds=0.1)
APIRequestDeadline.__init__=short
if sys.argv[1]=='hosted': os.environ['K_SERVICE']='deadline-fixture'
else: os.environ.pop('K_SERVICE',None)
from service.app import create_app
from service.config import Settings
app=create_app(Settings(deployment_mode='serverless'))
class SlowBudget:
    def invocation(self):
        print('BUDGET_STARTED',flush=True)
        time.sleep(0.3)
    def response(self,size): pass
app.state.budget=SlowBudget()
async def receive(): return {'type':'http.request','body':b'','more_body':False}
async def send(message): pass
scope={'type':'http','method':'GET','path':'/v1/datasets','headers':[], 'query_string':b'',
    'scheme':'https','http_version':'1.1','server':('test',443),'client':('test',1),'root_path':''}
asyncio.run(app(scope,receive,send))
time.sleep(0.2)
print('NORMAL_EXIT',flush=True)
'''
    environment={k:v for k,v in os.environ.items() if not k.startswith(('RESEARCH_','GOOGLE_'))}
    result=subprocess.run([sys.executable,'-c',code,'hosted' if hosted else 'local'],
        cwd=Path(__file__).resolve().parents[1],env=environment,capture_output=True,text=True,timeout=5)
    assert result.returncode==(124 if hosted else 0),(result.stdout,result.stderr)
    assert 'BUDGET_STARTED' in result.stdout
    assert ('NORMAL_EXIT' in result.stdout) is not hosted
