import contextvars
import json
import sys
import threading
import time
import httpx
import pytest
from conftest import start, answer_payload
from test_policy import child_review

@pytest.mark.parametrize('field,value', [('endpoint', 'https://evil.example/decisions'), ('endpoint', 'https://openrouter.ai/api/alpha/decisions/'),
                                        ('provider', 'perplexity'), ('reviewer_model', 'synthetic-invalid-reviewer')])
def test_altered_route_refused_before_key_resolution(env, field, value):
    env.ctx.settings[field] = value
    review = child_review(env)
    assert not review['verified']
    assert not env.secrets and not env.calls

@pytest.mark.parametrize('failure', ['no_key', 'scope_error', 'timeout', '429', 'redirect', 'bad_json', 'huge', 'redact_failure'])
def test_failure_is_fail_open_unverified_without_retry_or_raw_error(env, failure, monkeypatch):
    if failure == 'no_key': monkeypatch.setattr(sys.modules['agent.secret_scope'], 'get_secret', lambda *a: '')
    elif failure == 'scope_error':
        def denied(*a): raise RuntimeError('synthetic-PRIVATE')
        monkeypatch.setattr(sys.modules['agent.secret_scope'], 'get_secret', denied)
    elif failure == 'timeout': env.replies.append(httpx.ReadTimeout('synthetic-PRIVATE'))
    elif failure == '429': env.replies.append(lambda req, p: httpx.Response(429, json={'error': {'message': 'synthetic-PRIVATE'}}))
    elif failure == 'redirect': env.replies.append(lambda req, p: httpx.Response(302, headers={'location': 'https://evil.example'}))
    elif failure == 'bad_json': env.replies.append(lambda req, p: httpx.Response(200, text='synthetic-PRIVATE'))
    elif failure == 'huge': env.replies.append(lambda req, p: httpx.Response(200, text='x' * 300000))
    elif failure == 'redact_failure':
        def denied(*a): raise RuntimeError('synthetic-PRIVATE')
        monkeypatch.setattr(sys.modules['agent.redact'], 'redact_for_egress', denied)
    review = child_review(env)
    assert review['verdict'] == 'INSPECT' and not review['verified']
    assert review['model'] is None and review['request_id'] is None
    assert review['errors']
    assert 'synthetic-PRIVATE' not in json.dumps(review)
    assert len(env.calls) <= 1


def test_sensitive_structures_bearer_password_jwt_env_never_egress(env):
    start(env)
    secrets = ['supersecretPASSWORD', 'sk-test-ABC1234567890', 'fakeBearer9876543210', 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzb21lIn0.signatureABC', 'envSecret7890']
    evidence = {'task_index': 0, 'status': 'completed',
                'summary': 'pytest passed\npassword=supersecretPASSWORD\napi_key=sk-test-ABC1234567890\nAuthorization: Bearer fakeBearer9876543210\n' + secrets[3] + '\nCUSTOM_SECRET=envSecret7890',
                'password': secrets[0], 'env_contents': 'TOKEN=' + secrets[1],
                'tool_trace': [{'tool_name': 'terminal', 'args': {'api_key': secrets[1]}, 'status': 'success'}]}
    output = env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args={'goal': 'Проверить файл'},
                                                   result=json.dumps({'results': [evidence]}), session_id='p')
    assert json.loads(output)['results'][0] == evidence
    assert env.redacted and len(env.calls) == 1
    body = env.calls[0][0].content.decode()
    for secret in secrets: assert secret not in body
    assert len(body) < 25000


def test_request_state_bounded_before_egress(env):
    start(env)
    env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args={'goal': 'Проверить файл'},
                                         result=json.dumps({'results': [{'status': 'completed', 'summary': 'x' * 1000000}]}), session_id='p')
    assert len(env.calls[0][0].content) < 30000


def test_worker_copies_context_and_holds_single_slot_after_timeout(env):
    owner = contextvars.ContextVar('synthetic_owner'); owner.set('owner-a')
    entered = threading.Event(); release = threading.Event(); observed = []
    def slow(req, payload):
        observed.append(owner.get())
        entered.set(); release.wait(2)
        return httpx.Response(200, json=answer_payload(payload))
    env.replies.append(slow)
    env.ctx.settings['callback_budget_seconds'] = .05
    runtime = start(env)
    begin = time.monotonic()
    first = runtime.review('Проверить файл', {'summary': 'готово'})
    assert entered.is_set()
    assert time.monotonic() - begin < .5
    assert not first['verified']
    second = runtime.review('Другая цель', {'summary': 'готово'})
    assert not second['verified'] and len(env.calls) == 1
    assert observed == ['owner-a']
    release.set()
    deadline = time.monotonic() + 1
    while runtime.inflight.locked() and time.monotonic() < deadline: time.sleep(.005)
    assert not runtime.inflight.locked()
    assert runtime.review('После завершения', {'summary': 'готово'})['verified']
    assert len(env.calls) == 2
