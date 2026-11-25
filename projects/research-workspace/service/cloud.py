"""Small, bounded Google REST adapters using the attached service identity."""
import base64
import hashlib
import json
import logging
import re

import httpx

from .dispatch import LaunchRejected


RESOURCE = r'projects/[a-z0-9-]+/locations/[a-z0-9-]+/'


def access_token(client):
    response = client.get('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',
                          headers={'Metadata-Flavor': 'Google'})
    response.raise_for_status()
    return response.json()['access_token']


def wake(settings, run_id, generation):
    """Best effort after commit; reconciliation recovers every failed wakeup."""
    try:
        if not re.fullmatch(RESOURCE + r'queues/[a-zA-Z0-9_-]+', settings.task_queue):
            raise ValueError('Invalid task queue resource')
        if not settings.dispatcher_url.startswith('https://') or not settings.task_service_account.endswith('.iam.gserviceaccount.com'):
            raise ValueError('Invalid authenticated dispatcher destination')
        body = json.dumps({'run_id': run_id, 'generation': generation}, separators=(',', ':')).encode()
        name = hashlib.sha256(body).hexdigest()
        with httpx.Client(timeout=0.5, trust_env=False) as client:
            token = access_token(client)
            response = client.post('https://cloudtasks.googleapis.com/v2/' + settings.task_queue + '/tasks',
                headers={'Authorization': 'Bearer ' + token}, json={'task': {
                    'name': settings.task_queue + '/tasks/' + name,
                    'dispatchDeadline': '180s',
                    'httpRequest': {'httpMethod': 'POST', 'url': settings.dispatcher_url.rstrip('/') + '/dispatch',
                        'headers': {'Content-Type': 'application/json'}, 'body': base64.b64encode(body).decode(),
                        'oidcToken': {'serviceAccountEmail': settings.task_service_account,
                                      'audience': settings.dispatcher_url.rstrip('/')}}}})
            if response.status_code != 409:
                response.raise_for_status()
        return True
    except Exception:
        logging.warning('Dispatch wakeup deferred run=%s generation=%s', run_id, generation)
        return False


class CloudRun:
    def __init__(self, settings, client=None):
        if not re.fullmatch(RESOURCE + r'jobs/[a-z][a-z0-9-]*', settings.dispatch_job):
            raise ValueError('Configure one allowlisted Cloud Run Job')
        self.job = settings.dispatch_job
        self.client = client or httpx.Client(timeout=5, trust_env=False)

    def request(self, method, path, **kwargs):
        response = self.client.request(method, 'https://run.googleapis.com/v2/' + path,
            headers={'Authorization': 'Bearer ' + access_token(self.client)}, **kwargs)
        return response

    def launch(self, reservation):
        response = self.request('POST', self.job + ':run', json={'overrides': {
            'taskCount': 1, 'containerOverrides': [{'env': [
                {'name': 'RESEARCH_RUN_ID', 'value': reservation['run_id']},
                {'name': 'RESEARCH_GENERATION', 'value': str(reservation['generation'])},
                {'name': 'RESEARCH_RESERVATION_ID', 'value': reservation['id']},
            ]}]}})
        if response.status_code in (400, 401, 403, 404, 409, 429):
            raise LaunchRejected('Cloud Run rejected the launch')
        response.raise_for_status()
        return response.json()['name']

    def observe(self, reservation):
        execution = reservation.get('execution_name')
        operation = reservation.get('operation_name')
        if not execution and operation:
            prefix = self.job.split('/jobs/')[0] + '/operations/'
            if not operation.startswith(prefix) or not re.fullmatch(r'[a-zA-Z0-9_-]+', operation[len(prefix):]):
                raise ValueError('Unexpected cloud operation resource')
            response = self.request('GET', operation)
            response.raise_for_status()
            value = response.json()
            execution = value.get('response', {}).get('name') or value.get('metadata', {}).get('name')
            # Operation errors may follow creation of an execution. Only execution
            # discovery can prove terminal status; an absent name remains uncertain.
        if execution:
            prefix = self.job + '/executions/'
            if not execution.startswith(prefix) or not re.fullmatch(r'[a-zA-Z0-9_-]+', execution[len(prefix):]):
                raise ValueError('Unexpected cloud execution resource')
            response = self.request('GET', execution)
            response.raise_for_status()
            return self.execution_status(response.json())
        page_token = None
        for _ in range(5):
            params = {'pageSize': 100, 'showDeleted': 'true'}
            if page_token:
                params['pageToken'] = page_token
            response = self.request('GET', self.job + '/executions', params=params)
            response.raise_for_status()
            value = response.json()
            for item in value.get('executions', []):
                environments = [env for container in item.get('template', {}).get('containers', []) for env in container.get('env', [])]
                if any(env.get('name') == 'RESEARCH_RESERVATION_ID' and env.get('value') == reservation['id'] for env in environments):
                    return self.execution_status(item)
            page_token = value.get('nextPageToken')
            if not page_token:
                break
        # No listing result is not evidence that the launch never happened.
        return {'state': 'uncertain'}

    @staticmethod
    def execution_status(value):
        return {'state': 'terminal' if value.get('completionTime') else 'running', 'execution_name': value['name']}

    def close(self):
        self.client.close()
