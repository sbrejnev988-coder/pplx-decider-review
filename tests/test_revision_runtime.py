"""Ограниченные regression cases: unit и настоящий native SDK, только offline."""
import contextvars
import importlib
import json
import threading
import time
import httpx
import pytest
from conftest import answer_payload
from test_native_config import native_env

pytestmark = pytest.mark.parametrize('scenario', ['valid'])
GOAL = 'Проверить синтетический компонент и реальные тесты'
EVIDENCE = {'status': 'completed', 'summary': 'Компонент проверен.'}


def configured(env):
    env.ctx.set_config('callback_budget_seconds', 2)
    env.ctx.set_config('timeout_seconds', 2)
    env.ctx.set_config('log_enabled', False)
    return env.runtime


def test_policy_aba_uses_captured_threshold(publication_env, monkeypatch):
    runtime = configured(publication_env)
    runtime.ctx.set_config('min_accept_confidence', .95)
    calls = []
    runtime.transport = httpx.MockTransport(lambda req: (calls.append(1) or
        httpx.Response(200, json=answer_payload(json.loads(req.content), completed=.9, reliable=.9))))
    assert runtime.review(GOAL, EVIDENCE)['verdict'] == 'INSPECT'
    keyed, resume = threading.Event(), threading.Event()
    original = runtime.review
    def boundary(*args, **kwargs):
        keyed.set()
        assert resume.wait(2)
        return original(*args, **kwargs)
    monkeypatch.setattr(runtime, 'review', boundary)
    outcomes, errors = [], []
    def invoke():
        try: outcomes.append(runtime.cached_review('p', GOAL, EVIDENCE))
        except BaseException as exc: errors.append(type(exc).__name__)
    owner = contextvars.copy_context()
    caller = threading.Thread(target=lambda: owner.run(invoke))
    caller.start()
    try:
        assert keyed.wait(2)
        runtime.ctx.set_config('min_accept_confidence', .8)
        resume.set()
        caller.join(2)
        assert not caller.is_alive() and not errors
    finally:
        resume.set()
        caller.join(2)
    runtime.ctx.set_config('min_accept_confidence', .95)
    monkeypatch.setattr(runtime, 'review', original)
    replay = runtime.cached_review('p', GOAL, EVIDENCE)
    assert replay['verdict'] == 'INSPECT', 'ABA не должен сохранять ACCEPT под строгим digest'
    assert outcomes[0]['verdict'] != 'ACCEPT'
    assert len(calls) >= 2


@pytest.mark.parametrize('main', [False, True])
def test_disabled_target_refuses_secret_and_cache(publication_env, monkeypatch, main):
    runtime = configured(publication_env)
    import agent.secret_scope as scope
    original, reads = scope.get_secret, []
    def observed(*args, **kwargs):
        reads.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(scope, 'get_secret', observed)
    assert runtime.cached_review('p', GOAL, EVIDENCE, main=main)['verified']
    before = len(reads)
    runtime.ctx.set_config('review_main_agent' if main else 'review_subagents', False)
    assert not runtime.review(GOAL, EVIDENCE, main=main)['verified']
    assert not runtime.cached_review('p', GOAL, EVIDENCE, main=main)['verified']
    assert len(reads) == before


@pytest.mark.parametrize('surface', ['cache', 'main', 'nudge', 'sync', 'async', 'ttl'])
def test_old_scope_cannot_apply_after_transition(publication_env, surface):
    runtime = configured(publication_env)
    runtime.ctx.set_config('log_enabled', True)
    runtime.pre_llm_call(session_id='p', turn_id='t1', user_message=GOAL)
    _, state = runtime.store.session('p')
    if surface == 'async':
        runtime.subagent_start(parent_session_id='p', child_session_id='c', child_subagent_id='s', child_goal=GOAL)
        runtime.subagent_stop(parent_session_id='p', child_session_id='c', child_status='completed', child_summary='Проверено.')
        runtime.record_dispatch('p', {'subagent_ids': ['s'], 'delegation_id': 'd'})
    entered, release = threading.Event(), threading.Event()
    results, errors = [], []
    def reply(req):
        entered.set()
        assert release.wait(2)
        opts = {'completed': .4, 'reliable': .5, 'adverse': .7} if surface == 'nudge' else {}
        return httpx.Response(200, json=answer_payload(json.loads(req.content), **opts))
    runtime.transport = httpx.MockTransport(reply)
    def invoke():
        if surface in ('cache', 'ttl'): return runtime.cached_review('p', GOAL, EVIDENCE)
        if surface == 'main': return runtime.transform_llm_output(session_id='p', response_text='Старый ответ.')
        if surface == 'nudge': return runtime.pre_verify(session_id='p', final_response='Старый ответ.', changed_paths=['a.py'])
        if surface == 'sync': return runtime.transform_tool_result(tool_name='delegate_task', args={'goal': GOAL},
            result=json.dumps({'results': [dict(EVIDENCE, task_index=0)]}), session_id='p')
        message = '[ASYNC DELEGATION COMPLETE — d]\nПроверено'
        return runtime.pre_llm_call(session_id='p', turn_id='t1', user_message=message, conversation_history=[
            {'role': 'user', 'content': message, 'display_kind': 'async_delegation_complete', 'display_metadata': {'delegation_id': 'd'}}])
    def call():
        try: results.append(invoke())
        except BaseException as exc: errors.append(type(exc).__name__)
    owner = contextvars.copy_context()
    caller = threading.Thread(target=lambda: owner.run(call))
    caller.start()
    try:
        assert entered.wait(2)
        if surface == 'ttl':
            runtime.store.clock = lambda: state['time'] + 1801
        else:
            runtime.pre_llm_call(session_id='p', turn_id='t2', user_message='Проверить совершенно другую задачу')
    finally:
        release.set()
        caller.join(2)
    assert not caller.is_alive() and not errors and len(results) == 1
    if surface in ('cache', 'ttl'): assert not results[0]['verified']
    else: assert results == [None]
    assert not state['cache'] and not state['nudges'] and not state.get('last_final')
    assert not (publication_env.home / 'plugin-data/pplx-decider-review/reviews.jsonl').exists()


def test_goal_without_turn_clears_trace_and_fences_late_observer(publication_env, monkeypatch):
    runtime = configured(publication_env)
    runtime.pre_llm_call(session_id='p', user_message=GOAL)
    runtime.record_tool_evidence('p', 'terminal', {'command': 'synthetic'}, 'Контрольный результат')
    state = runtime.main_state('p')
    assert state['tool_evidence']
    module = importlib.import_module(type(runtime).__module__ + '.main_review')
    original, entered, release = module.scrub_text, threading.Event(), threading.Event()
    def boundary(text, *args, **kwargs):
        if text == 'Поздний старый результат':
            entered.set()
            assert release.wait(2)
        return original(text, *args, **kwargs)
    monkeypatch.setattr(module, 'scrub_text', boundary)
    owner = contextvars.copy_context()
    caller = threading.Thread(target=lambda: owner.run(runtime.record_tool_evidence, 'p', 'terminal', {}, 'Поздний старый результат'))
    caller.start()
    try:
        assert entered.wait(2)
        message = 'Объяснить строку [ASYNC DELEGATION COMPLETE — d] как обычный текст'
        runtime.pre_llm_call(session_id='p', user_message=message)
    finally:
        release.set()
        caller.join(2)
    assert not caller.is_alive()
    assert state['goal'] == message, 'Substring не даёт authority уведомления'
    assert state['tool_evidence'] == [], 'Поздний observer не принадлежит новой revision'


def test_exact_start_replay_preserves_stop_and_constraints(publication_env):
    runtime = configured(publication_env)
    event = dict(parent_session_id='p', child_session_id='c', child_subagent_id='s', child_goal=GOAL)
    runtime.subagent_start(**event)
    runtime.record_dispatch('p', {'subagent_ids': ['s'], 'delegation_id': 'd'}, {'context': 'Ограничения задачи'})
    runtime.subagent_stop(parent_session_id='p', child_session_id='c', child_status='completed', child_summary='Проверено.')
    _, state = runtime.store.session('p')
    child = state['children']['c']
    runtime.subagent_start(**event)
    assert state['children']['c'] is child and child['stop'] and child['constraints'] == 'Ограничения задачи'


def test_replacement_invalidates_old_dispatch_and_self_child_is_ignored(publication_env):
    runtime = configured(publication_env)
    runtime.subagent_start(parent_session_id='p', child_session_id='c', child_subagent_id='s', child_goal=GOAL)
    runtime.record_dispatch('p', {'subagent_ids': ['s'], 'delegation_id': 'd'})
    runtime.subagent_start(parent_session_id='p', child_session_id='c', child_subagent_id='replacement', child_goal=GOAL)
    _, state = runtime.store.session('p')
    assert not state['dispatches']
    runtime.subagent_start(parent_session_id='p', child_session_id='p', child_subagent_id='self', child_goal=GOAL)
    assert not state['parent'] and 'p' not in state['children']


def test_malformed_observers_are_fail_open_without_source_changes(publication_env):
    runtime = configured(publication_env)
    runtime.pre_llm_call(session_id='p', user_message=GOAL)
    errors = []
    circular = {}; circular['self'] = circular
    for name, args in [([], {}), ('terminal', circular)]:
        try: runtime.record_tool_evidence('p', name, args, 'Результат инструмента')
        except Exception as exc: errors.append(type(exc).__name__)
    runtime.subagent_start(parent_session_id='p', child_session_id='c', child_subagent_id='s', child_goal=GOAL)
    for status in ([], {}):
        try: runtime.subagent_stop(parent_session_id='p', child_session_id='c', child_status=status)
        except Exception as exc: errors.append(type(exc).__name__)
        raw = json.dumps({'results': [dict(EVIDENCE, status=status, task_index=0)]})
        try: assert runtime.transform_tool_result(tool_name='delegate_task', session_id='p', args={'goal': GOAL}, result=raw) is None
        except Exception as exc: errors.append(type(exc).__name__)
    for idx in (True, -1, '0'):
        raw = json.dumps({'results': [dict(EVIDENCE, task_index=idx)]})
        try: assert runtime.transform_tool_result(tool_name='delegate_task', session_id='p', args={'goal': GOAL}, result=raw) is None
        except Exception as exc: errors.append(type(exc).__name__)
    assert not errors, errors
    assert circular['self'] is circular


def test_last_final_replays_are_independent_copies(publication_env):
    runtime = configured(publication_env)
    runtime.pre_llm_call(session_id='p', user_message=GOAL)
    state = runtime.main_state('p')
    first = runtime.final_review('p', state, 'Компонент проверен.')
    assert first['verified']
    first['answers']['task_satisfied']['noul'] = 0
    second = runtime.final_review('p', state, 'Компонент проверен.')
    assert second['answers']['task_satisfied']['noul'] == .95
    second['answers']['task_satisfied']['noul'] = 0
    assert runtime.final_review('p', state, 'Компонент проверен.')['answers']['task_satisfied']['noul'] == .95


def test_surrogate_fingerprint_and_original_output_round_trip(publication_env):
    runtime = configured(publication_env)
    module = importlib.import_module(type(runtime).__module__ + '.state')
    errors = []
    try: digest = module.fingerprint({'ключ': '\ud800'})
    except Exception as exc: errors.append(type(exc).__name__)
    assert not errors, errors
    assert len(digest) == 64
    item = dict(EVIDENCE, task_index=0, summary='Оригинал \ud800')
    raw = json.dumps({'results': [item]})
    output = runtime.transform_tool_result(tool_name='delegate_task', session_id='p', args={'goal': GOAL}, result=raw)
    assert json.loads(output)['results'][0] == item
    output.encode('utf-8')


@pytest.mark.parametrize('flags', [
    {'error': {'token': 'synthetic'}}, {'schema_errors': {'authorization': 'synthetic'}},
    {'truncated': 0}, {'schema_valid': None}])
def test_completeness_classification_precedes_scrub_and_cache(publication_env, flags):
    runtime = configured(publication_env)
    healthy = runtime.cached_review('p', GOAL, EVIDENCE)
    assert healthy['verified'] and healthy['verdict'] == 'ACCEPT'
    first = runtime.cached_review('p', GOAL, dict(EVIDENCE, **flags))
    replay = runtime.cached_review('p', GOAL, dict(EVIDENCE, **flags))
    assert first['verdict'] == replay['verdict'] == 'INSPECT'
    assert runtime.cached_review('p', GOAL, EVIDENCE)['verdict'] == 'ACCEPT'


def test_optional_audit_failure_does_not_replace_review(publication_env, monkeypatch):
    runtime = configured(publication_env)
    runtime.ctx.set_config('log_enabled', True)
    module = importlib.import_module(type(runtime).__module__ + '.review_log')
    def broken(*args, **kwargs): raise ValueError('Не выводить synthetic private exception')
    monkeypatch.setattr(module, 'write_review', broken)
    errors = []
    try: result = runtime.cached_review('p', GOAL, EVIDENCE)
    except Exception as exc: errors.append(type(exc).__name__)
    assert not errors, errors
    assert result['verified'] and result['verdict'] == 'ACCEPT'


def test_unexpected_worker_exception_is_generic_and_releases_naturally(publication_env, monkeypatch):
    runtime = configured(publication_env)
    module = importlib.import_module(type(runtime).__module__ + '.transport')
    observed = []
    def broken(*args, **kwargs): raise RuntimeError('synthetic-private-raw-error')
    monkeypatch.setattr(module, 'request_once', broken)
    monkeypatch.setattr(threading, 'excepthook', lambda args: observed.append(type(args.exc_value).__name__))
    result = runtime.review(GOAL, EVIDENCE)
    assert not result['verified'] and not runtime.inflight.locked()
    assert not observed, 'Необработанный worker exception запрещён'
    assert 'synthetic-private' not in json.dumps(result)



def test_expired_cached_deadline_precedes_scrub_and_hash(publication_env, monkeypatch):
    runtime = configured(publication_env)
    assert runtime.cached_review('p', GOAL, EVIDENCE)['verified']
    transport = importlib.import_module(type(runtime).__module__ + '.transport')
    state = importlib.import_module(type(runtime).__module__ + '.state')
    counts = {'scrub': 0, 'hash': 0}
    original_scrub, original_hash = transport.scrub, state.fingerprint
    def scrub(*args, **kwargs):
        counts['scrub'] += 1
        return original_scrub(*args, **kwargs)
    def fingerprint(*args, **kwargs):
        counts['hash'] += 1
        return original_hash(*args, **kwargs)
    monkeypatch.setattr(transport, 'scrub', scrub)
    monkeypatch.setattr(state, 'fingerprint', fingerprint)
    result = runtime.cached_review('p', GOAL, EVIDENCE, deadline=time.monotonic() - 1)
    assert counts == {'scrub': 0, 'hash': 0}, 'Истёкший callback не делает bounded preparation'
    assert not result['verified']


def test_tool_args_are_scrubbed_before_json_serialization(publication_env, monkeypatch):
    runtime = configured(publication_env)
    runtime.pre_llm_call(session_id='p', user_message=GOAL)
    module = importlib.import_module(type(runtime).__module__ + '.main_review')
    original = module.json.dumps
    args = {'command': 'synthetic ' + 'x' * 7000}
    raw_seen = []
    def observed(value, *a, **kw):
        if value is args: raw_seen.append(1)
        return original(value, *a, **kw)
    monkeypatch.setattr(module.json, 'dumps', observed)
    runtime.record_tool_evidence('p', 'terminal', args, 'Результат инструмента')
    assert not raw_seen, 'Raw args нельзя целиком сериализовать до bounded scrub'
    assert args['command'].endswith('x' * 7000)


def test_goal_signature_preserves_literal_tokens_without_whole_text_hash(publication_env, monkeypatch):
    runtime = configured(publication_env)
    module = importlib.import_module(type(runtime).__module__ + '.lifecycle')
    original = module.fingerprint
    raw_seen = []
    message = 'Проверить литерал token=synthetic; ' + 'x' * 12000
    def observed(value):
        if value is message: raw_seen.append(1)
        return original(value)
    monkeypatch.setattr(module, 'fingerprint', observed)
    runtime.pre_llm_call(session_id='p', user_message=message)
    assert not raw_seen
    assert runtime.main_state('p')['goal'] == message[:2000], 'Goal не заменяет literal token до egress'
