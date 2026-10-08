"""Регрессии выбора модели: synthetic owner/MockTransport, не native/live proof."""
import contextvars
from dataclasses import FrozenInstanceError
from importlib import import_module
import json
import sys
import threading

import httpx
import pytest
from conftest import answer_payload, start

LUNA = 'openai/gpt-6-luna-decisions'
ALTERNATE = 'typesafe/jev-1.13'
ENDPOINT = 'https://openrouter.ai/api/alpha/decisions'
GOAL = 'Проверить компонент и предоставленные свидетельства'
EVIDENCE = {'status': 'completed', 'summary': 'Компонент проверен.'}


def reply_for(payload, model=None, **kwargs):
    reply = answer_payload(payload, **kwargs)
    reply['model'] = payload['model'] if model is None else model
    reply['provider'] = 'synthetic-provider'
    return reply


@pytest.fixture
def model_env(env):
    # Не наследуем старую PPLX-модель фикстуры; проверяем production default.
    env.ctx.settings.pop('reviewer_model', None)
    env.ctx.settings.update(log_enabled=False, callback_budget_seconds=2, timeout_seconds=2)
    env.worker_names = []
    def handler(request):
        payload = json.loads(request.content)
        env.calls.append((request, payload))
        env.worker_names.append(threading.current_thread().name)
        if env.replies:
            response = env.replies.pop(0)
            if isinstance(response, Exception):
                raise response
            if callable(response):
                return response(request, payload)
            return httpx.Response(200, json=response)
        return httpx.Response(200, json=reply_for(payload))
    env.transport = httpx.MockTransport(handler)
    env.runtime = start(env)
    try:
        yield env
    finally:
        env.runtime.close()


def test_default_luna_wire_and_neutral_worker(model_env):
    runtime = model_env.runtime
    protocol = import_module(model_env.p.__name__ + '.protocol')
    assert model_env.p.MODEL == protocol.MODEL == LUNA
    assert runtime.snapshot_policy().model == LUNA
    review = runtime.review(GOAL, EVIDENCE)
    assert review['verified'] and review['verdict'] == 'ACCEPT'
    assert review['requested_model'] == review['model'] == LUNA
    assert len(model_env.calls) == 1
    request, payload = model_env.calls[0]
    assert request.method == 'POST' and str(request.url) == ENDPOINT
    assert payload['model'] == LUNA
    assert model_env.worker_names == ['decision-review']


@pytest.mark.parametrize('suffix', ['', '-20261008'])
def test_alternate_model_wire_and_exact_validator(model_env, suffix):
    model_env.ctx.settings['reviewer_model'] = ALTERNATE
    model_env.replies.append(lambda req, payload: httpx.Response(
        200, json=reply_for(payload, model=ALTERNATE + suffix)))
    review = model_env.runtime.review(GOAL, EVIDENCE)
    assert review['verified'] and review['requested_model'] == ALTERNATE
    assert review['model'] == ALTERNATE + suffix
    assert model_env.calls[0][1]['model'] == ALTERNATE
    protocol = import_module(model_env.p.__name__ + '.protocol')
    parsed = protocol.validate(reply_for(model_env.calls[0][1], model=ALTERNATE + suffix),
                               model_env.p.questions(), expected_model=ALTERNATE)
    assert parsed['model'] == ALTERNATE + suffix


@pytest.mark.parametrize('requested,returned', [
    (LUNA, 'perplexity/pplx-decider-v1.1-27b'),
    (LUNA, LUNA + '-other-20261008'),
    (LUNA, LUNA + '-20261008x'),
    (LUNA, LUNA + '-٢٠٢٦١٠٠٨'),
    (ALTERNATE, 'typesafe/jev-1x13-20261008'),
    (ALTERNATE, LUNA),
])
def test_foreign_or_prefix_similar_response_is_inspect(model_env, requested, returned):
    model_env.ctx.settings['reviewer_model'] = requested
    model_env.replies.append(lambda req, payload: httpx.Response(
        200, json=reply_for(payload, model=returned)))
    review = model_env.runtime.review(GOAL, EVIDENCE)
    assert review['verdict'] == 'INSPECT' and not review['verified']
    assert review['requested_model'] == requested and review['model'] is None
    assert len(model_env.calls) == 1, 'Чужая модель не разрешает fallback/retry'


@pytest.mark.parametrize('invalid', [
    None, True, '', 'openai', '/luna', '../luna', 'openai//luna',
    'openai/gpt:free', 'openai@evil/luna', 'openai/luna?x=1',
    'openai/luna#x', 'openai/luna\n', 'openai/луна', 'a/' + 'x' * 127,
])
def test_invalid_model_reads_no_key_and_sends_no_http(model_env, invalid):
    model_env.ctx.settings['reviewer_model'] = invalid
    policy = model_env.runtime.snapshot_policy()
    assert not policy.valid and policy.model is None
    for review in (model_env.runtime.review(GOAL, EVIDENCE),
                   model_env.runtime.cached_review('models', GOAL, EVIDENCE)):
        assert review['verdict'] == 'INSPECT' and not review['verified']
        assert review['requested_model'] is None, 'Некорректный slug не превращается в default'
    assert not model_env.secrets and not model_env.calls


def test_slug_limit_is_literal_ascii_without_normalization(model_env):
    protocol = import_module(model_env.p.__name__ + '.protocol')
    maximum = 'a/' + 'b' * 126
    assert len(maximum) == protocol.MAX_MODEL_LENGTH == 128
    assert protocol.valid_model_identifier(maximum)
    assert not protocol.valid_model_identifier(maximum + 'b')
    assert protocol.valid_model_identifier('Provider/model-v1.2_3')
    for value in (' openai/luna', 'openai/luna ', 'openai/\tluna',
                  'https://openrouter.ai/luna', 'openai\\luna'):
        assert not protocol.valid_model_identifier(value)


def test_direct_transport_has_luna_default_and_rejects_invalid_before_egress(model_env, monkeypatch):
    transport = import_module(model_env.p.__name__ + '.transport')
    args = (GOAL, EVIDENCE, model_env.p.questions(), 'synthetic-key',
            model_env.transport, 2, False, .8, .65)
    review = transport.request_once(*args)
    assert review['verified'] and review['requested_model'] == LUNA
    assert model_env.calls[0][1]['model'] == LUNA
    def denied(*args, **kwargs):
        raise AssertionError('Невалидный slug не должен готовить egress')
    monkeypatch.setattr(transport, 'safe_state', denied)
    rejected = transport.request_once(*args, model='openai/luna?x=1')
    assert not rejected['verified'] and rejected['requested_model'] is None
    assert len(model_env.calls) == 1


@pytest.mark.parametrize('failure', [
    'missing_key', 'scoped_error', 'disabled', 'closed', 'http_429',
    'redirect', 'timeout', 'bad_json', 'worker_error', 'worker_start',
])
def test_errors_keep_requested_alternate_without_retry(model_env, monkeypatch, failure):
    runtime = model_env.runtime
    model_env.ctx.settings['reviewer_model'] = ALTERNATE
    secret_scope = sys.modules['agent.secret_scope']
    def broken(*args, **kwargs):
        raise RuntimeError('synthetic-private-error')
    if failure == 'missing_key':
        monkeypatch.setattr(secret_scope, 'get_secret', lambda *args: '')
    elif failure == 'scoped_error':
        monkeypatch.setattr(secret_scope, 'get_secret', broken)
    elif failure == 'disabled':
        model_env.ctx.settings['enabled'] = False
    elif failure == 'closed':
        runtime.close()
    elif failure == 'http_429':
        model_env.replies.append(lambda req, payload: httpx.Response(429))
    elif failure == 'redirect':
        model_env.replies.append(lambda req, payload: httpx.Response(
            302, headers={'Location': 'https://foreign.example/decisions'}))
    elif failure == 'timeout':
        model_env.replies.append(httpx.ReadTimeout('synthetic-private-error'))
    elif failure == 'bad_json':
        model_env.replies.append(lambda req, payload: httpx.Response(200, text='synthetic-private-error'))
    elif failure == 'worker_error':
        monkeypatch.setattr(import_module(model_env.p.__name__ + '.transport'), 'request_once', broken)
    elif failure == 'worker_start':
        monkeypatch.setattr(threading.Thread, 'start', broken)
    review = runtime.review(GOAL, EVIDENCE)
    assert not review['verified'] and review['verdict'] == 'INSPECT'
    assert review['requested_model'] == ALTERNATE and review['model'] is None
    assert 'synthetic-private-error' not in json.dumps(review)
    assert len(model_env.calls) == (1 if failure in ('http_429', 'redirect', 'timeout', 'bad_json') else 0)
    assert not runtime.inflight.locked()


def test_snapshot_is_immutable_and_stale_route_is_refused_before_key(model_env):
    runtime = model_env.runtime
    policy = runtime.snapshot_policy()
    scope = runtime.capture_scope('models', policy=policy)
    with pytest.raises(FrozenInstanceError):
        policy.model = ALTERNATE
    model_env.ctx.settings['reviewer_model'] = ALTERNATE
    assert policy.model == LUNA and runtime.snapshot_policy().tag != policy.tag
    assert not runtime.scope_current(scope)
    for review in (runtime.review(GOAL, EVIDENCE, policy=policy),
                   runtime.review(GOAL, EVIDENCE, expected_scope=scope)):
        assert not review['verified'] and review['requested_model'] == LUNA
    assert not model_env.secrets and not model_env.calls


def test_model_change_during_scoped_key_read_never_dispatches(model_env, monkeypatch):
    original = sys.modules['agent.secret_scope'].get_secret
    def changed(*args, **kwargs):
        model_env.ctx.settings['reviewer_model'] = ALTERNATE
        return original(*args, **kwargs)
    monkeypatch.setattr(sys.modules['agent.secret_scope'], 'get_secret', changed)
    review = model_env.runtime.review(GOAL, EVIDENCE)
    assert not review['verified'] and review['requested_model'] == LUNA
    assert len(model_env.secrets) == 1 and not model_env.calls


def test_cache_namespace_and_old_scope_do_not_replay_foreign_model(model_env):
    runtime = model_env.runtime
    old_scope = runtime.capture_scope('models')
    first = runtime.cached_review('models', GOAL, EVIDENCE)
    assert first['verified'] and first['requested_model'] == LUNA
    assert runtime.cached_review('models', GOAL, EVIDENCE)['verified']
    assert len(model_env.calls) == 1
    model_env.ctx.settings['reviewer_model'] = ALTERNATE
    stale = runtime.cached_review('models', GOAL, EVIDENCE, expected_scope=old_scope)
    assert not stale['verified'] and stale['requested_model'] == LUNA
    assert len(model_env.calls) == 1
    alternate = runtime.cached_review('models', GOAL, EVIDENCE)
    assert alternate['verified'] and alternate['requested_model'] == alternate['model'] == ALTERNATE
    assert runtime.cached_review('models', GOAL, EVIDENCE)['requested_model'] == ALTERNATE
    assert [payload['model'] for _, payload in model_env.calls] == [LUNA, ALTERNATE]


def test_dispatch_seam_keeps_snapshot_model_for_wire_and_validator(model_env, monkeypatch):
    transport = import_module(model_env.p.__name__ + '.transport')
    original, inner = transport.request_once, []
    def boundary(*args, **kwargs):
        assert kwargs['model'] == LUNA
        model_env.ctx.settings['reviewer_model'] = ALTERNATE
        review = original(*args, **kwargs)
        inner.append(review)
        return review
    monkeypatch.setattr(transport, 'request_once', boundary)
    outer = model_env.runtime.review(GOAL, EVIDENCE)
    assert inner[0]['verified'] and inner[0]['requested_model'] == inner[0]['model'] == LUNA
    assert model_env.calls[0][1]['model'] == LUNA
    assert not outer['verified'] and outer['requested_model'] == LUNA


@pytest.mark.parametrize('surface', ['cache', 'sync', 'main', 'nudge'])
def test_model_change_during_response_cannot_cache_or_apply(model_env, surface):
    runtime = model_env.runtime
    runtime.pre_llm_call(session_id='models', turn_id='t1', user_message=GOAL)
    _, state = runtime.store.session('models')
    def changed(req, payload):
        model_env.ctx.settings['reviewer_model'] = ALTERNATE
        return httpx.Response(200, json=reply_for(payload))
    model_env.replies.append(changed)
    original = json.dumps({'results': [dict(EVIDENCE, task_index=0)]})
    if surface == 'cache':
        result = runtime.cached_review('models', GOAL, EVIDENCE)
        assert not result['verified'] and result['requested_model'] == LUNA
    elif surface == 'sync':
        assert runtime.transform_tool_result(tool_name='delegate_task', args={'goal': GOAL},
                                             result=original, session_id='models') is None
        assert json.loads(original)['results'][0] == dict(EVIDENCE, task_index=0)
    elif surface == 'main':
        assert runtime.transform_llm_output(session_id='models', response_text='Исходный финальный ответ.') is None
    else:
        assert runtime.pre_verify(session_id='models', final_response='Исходный черновик.',
                                  changed_paths=['synthetic.py']) is None
    assert len(model_env.calls) == 1 and model_env.calls[0][1]['model'] == LUNA
    assert not state['cache'] and not state['nudges'] and not state.get('last_final')


def test_route_change_does_not_release_inflight_slot(model_env):
    runtime = model_env.runtime
    entered, release = threading.Event(), threading.Event()
    results, errors = [], []
    def slow(req, payload):
        entered.set()
        assert release.wait(3)
        return httpx.Response(200, json=reply_for(payload))
    model_env.replies.append(slow)
    def invoke():
        try:
            results.append(runtime.review(GOAL, EVIDENCE))
        except BaseException as exc:
            errors.append(type(exc).__name__)
    owner = contextvars.copy_context()
    caller = threading.Thread(target=lambda: owner.run(invoke))
    caller.start()
    try:
        assert entered.wait(1)
        model_env.ctx.settings['reviewer_model'] = ALTERNATE
        busy = runtime.review(GOAL, EVIDENCE)
        assert not busy['verified'] and busy['requested_model'] == ALTERNATE
        assert runtime.inflight.locked() and len(model_env.calls) == 1
    finally:
        release.set()
        caller.join(3)
    assert not caller.is_alive() and not errors and not runtime.inflight.locked()
    assert len(results) == 1 and not results[0]['verified']
    assert results[0]['requested_model'] == LUNA


def test_model_change_does_not_replenish_native_main_turn(model_env):
    runtime = model_env.runtime
    model_env.ctx.settings['reviewer_model'] = ALTERNATE
    runtime.pre_llm_call(session_id='models', turn_id='t1', user_message=GOAL)
    model_env.replies.append(lambda req, payload: httpx.Response(
        200, json=reply_for(payload, completed=.4, reliable=.5, adverse=.7)))
    first = runtime.pre_verify(session_id='models', final_response='Первый черновик.', changed_paths=['synthetic.py'])
    assert first is not None and first['action'] == 'continue'
    model_env.ctx.settings['reviewer_model'] = LUNA
    assert runtime.pre_verify(session_id='models', final_response='Другой черновик.', changed_paths=['synthetic.py']) is None
    original = 'Исходный финальный ответ.'
    decorated = runtime.transform_llm_output(session_id='models', response_text=original,
                                             verification_pass_status='completed')
    assert decorated.startswith(original)
    assert [payload['model'] for _, payload in model_env.calls] == [ALTERNATE]
