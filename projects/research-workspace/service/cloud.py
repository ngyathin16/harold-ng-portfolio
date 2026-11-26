"""Small, bounded Google REST adapters using the attached service identity."""
import base64
import copy
import hashlib
import json
import logging
import re

import httpx

RESOURCE = r'projects/[a-z0-9-]+/locations/[a-z0-9-]+/'


def access_token(client):
    response = client.get('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',
                          headers={'Metadata-Flavor': 'Google'})
    response.raise_for_status()
    return response.json()['access_token']


def wake(settings, run_id, generation, *, charge=None):
    """Best effort after commit; reconciliation recovers every failed wakeup."""
    try:
        if not re.fullmatch(RESOURCE + r'queues/[a-zA-Z0-9_-]+', settings.task_queue):
            raise ValueError('Invalid task queue resource')
        if not settings.dispatcher_url.startswith('https://') or not settings.task_service_account.endswith('.iam.gserviceaccount.com'):
            raise ValueError('Invalid authenticated dispatcher destination')
        body = json.dumps({'run_id': run_id, 'generation': generation}, separators=(',', ':')).encode()
        name = hashlib.sha256(body).hexdigest()
        with httpx.Client(timeout=0.5, trust_env=False) as client:
            if charge is not None:
                charge()
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
    launch_protocol = 'token-v1'

    def __init__(self, settings, client=None, charge=None):
        if not re.fullmatch(RESOURCE + r'jobs/[a-z][a-z0-9-]*', settings.dispatch_job):
            raise ValueError('Configure one allowlisted Cloud Run Job')
        self.job = settings.dispatch_job
        if len(self.job.rsplit('/', 1)[1]) + 32 >= 63:
            raise ValueError('Job name leaves insufficient room for the execution token')
        self.client = client or httpx.Client(timeout=5, trust_env=False)
        self.charge = charge

    def request(self, method, path, **kwargs):
        if self.charge is not None:
            self.charge()
        headers = dict(kwargs.pop('headers', {}))
        headers['Authorization'] = 'Bearer ' + access_token(self.client)
        response = self.client.request(method, 'https://run.googleapis.com/v2/' + path,
            headers=headers, **kwargs)
        return response

    def launch(self, reservation):
        spec = reservation.get('launch_spec')
        if reservation.get('launch_protocol') != self.launch_protocol or not spec:
            raise ValueError('A durable conditional launch specification is required')
        self.validate_spec(reservation, spec)
        body = json.dumps(spec['body'], ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(body) > 60000:
            raise ValueError('Conditional Job request exceeds the byte allowance')
        response = self.request('PATCH', self.job, content=body, headers={'Content-Type': 'application/json'})
        # A stale etag is expected after a lost successful acknowledgement.
        # Even a later permission/budget rejection cannot disprove that success.
        if response.status_code == 409:
            return None
        response.raise_for_status()
        return response.json()['name']

    def observe(self, reservation):
        if reservation.get('launch_protocol') == self.launch_protocol:
            return self.observe_token(reservation)
        return self.observe_legacy(reservation)

    @staticmethod
    def binding(reservation):
        return {'RESEARCH_RUN_ID': reservation['run_id'],
                'RESEARCH_GENERATION': str(reservation['generation']),
                'RESEARCH_RESERVATION_ID': reservation['id']}

    def execution_name(self, reservation):
        if not re.fullmatch(r'[0-9a-f]{32}', reservation['id']):
            raise ValueError('Invalid launch reservation identity')
        return self.job + '/executions/' + self.job.rsplit('/', 1)[1] + '-' + reservation['id']

    @staticmethod
    def job_body(value):
        fields = ('name', 'labels', 'annotations', 'client', 'clientVersion',
                  'launchStage', 'binaryAuthorization', 'template')
        return {key: copy.deepcopy(value[key]) for key in fields if key in value}

    @staticmethod
    def fingerprint(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                        ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    def read_job(self):
        response = self.request('GET', self.job)
        response.raise_for_status()
        value = response.json()
        if value.get('name') != self.job or not value.get('uid') or not value.get('etag'):
            raise ValueError('Cloud Job identity or conditional version is unavailable')
        if not str(value.get('generation', '')).isdigit():
            raise ValueError('Cloud Job generation is unavailable')
        return value

    @staticmethod
    def validate_template(body):
        template = body.get('template', {})
        task = template.get('template', {})
        containers = task.get('containers', [])
        if template.get('taskCount') != 1 or template.get('parallelism') != 1 or len(containers) != 1:
            raise ValueError('Search Job must have exactly one task and container')
        container = containers[0]
        limits = container.get('resources', {}).get('limits', {})
        if (task.get('maxRetries', 0) != 0 or task.get('timeout') != '300s'
                or limits.get('cpu') != '2' or limits.get('memory') not in ('2Gi', '2048Mi')
                or container.get('command') != ['python', '-m', 'service.worker']
                or container.get('args') != ['--bound']
                or not re.search(r'@sha256:[0-9a-f]{64}$', container.get('image', ''))):
            raise ValueError('Search Job differs from the qualified execution bounds')

    def prepare(self, reservation):
        self.execution_name(reservation)
        value = self.read_job()
        if value.get('reconciling'):
            raise RuntimeError('Wait for the previous Job mutation to reconcile')
        original = self.job_body(value)
        self.validate_template(original)
        body = copy.deepcopy(original)
        container = body['template']['template']['containers'][0]
        binding = self.binding(reservation)
        container['env'] = [env for env in container.get('env', []) if env.get('name') not in binding]
        container['env'].extend({'name': name, 'value': value} for name, value in binding.items())
        body.update(etag=value['etag'], startExecutionToken=reservation['id'])
        spec = {'body': body, 'uid': value['uid'], 'generation': str(value['generation']),
                'previous_token': value.get('startExecutionToken') or value.get('runExecutionToken'),
                'previous_sha256': self.fingerprint(original)}
        self.validate_spec(reservation, spec)
        return spec

    def validate_spec(self, reservation, spec):
        body = spec.get('body', {})
        if (body.get('name') != self.job or not body.get('etag')
                or body.get('startExecutionToken') != reservation['id']
                or 'runExecutionToken' in body or not spec.get('uid')
                or not str(spec.get('generation', '')).isdigit()
                or len(json.dumps(spec, ensure_ascii=False).encode()) > 60000):
            raise ValueError('Invalid durable launch specification')
        self.execution_name(reservation)
        self.validate_template(body)
        self.validate_binding(body['template']['template'], reservation)

    def validate_binding(self, template, reservation):
        environments = [env for container in template.get('containers', []) for env in container.get('env', [])]
        for name, value in self.binding(reservation).items():
            values = [env.get('value') for env in environments if env.get('name') == name]
            if values != [value]:
                raise ValueError('Execution does not match its reserved search')

    def observe_token(self, reservation):
        name = self.execution_name(reservation)
        spec = reservation.get('launch_spec')
        if spec:
            self.validate_spec(reservation, spec)
            value = self.read_job()
            token = value.get('startExecutionToken') or value.get('runExecutionToken')
            if (token != reservation['id'] and value['uid'] == spec['uid'] and str(value['generation']) == spec['generation']
                    and token == spec['previous_token']
                    and self.fingerprint(self.job_body(value)) == spec['previous_sha256']):
                # The persisted prior desired state authorizes this conditional
                # replay without reading an execution that does not exist yet.
                retry_spec = copy.deepcopy(spec)
                retry_spec['body']['etag'] = value['etag']
                return {'state': 'uncertain', 'retry_spec': retry_spec}
        response = self.request('GET', name)
        if response.status_code == 404:
            return {'state': 'uncertain'}
        response.raise_for_status()
        value = response.json()
        if value.get('name') != name:
            raise ValueError('Unexpected deterministic execution identity')
        self.validate_binding(value.get('template', {}), reservation)
        return self.execution_status(value)

    def observe_legacy(self, reservation):
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
