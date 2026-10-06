"""Behavior-регрессии: unit owner по умолчанию, явный native SDK; только offline streams."""
import hashlib
import importlib
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import httpx
import pytest
from conftest import answer_payload
from test_native_config import native_env  # fixture discovery; SDK import только при явном opt-in

pytestmark = pytest.mark.parametrize('scenario', ['valid'])
GOAL = 'Проверить компонент и реальные тесты'
SUMMARY = 'Компонент проверен: 2 passed.'


def sync(runtime):
    return runtime.transform_tool_result(tool_name='delegate_task', session_id='p',
        args={'goal': GOAL}, result=json.dumps({'results': [
            {'task_index': 0, 'status': 'completed', 'summary': SUMMARY}]}))


def launch(runtime, cid):
    runtime.subagent_start(parent_session_id='p', parent_turn_id='t1',
        child_session_id=cid, child_subagent_id='sub-' + cid, child_goal=GOAL)
    runtime.subagent_stop(parent_session_id='p', child_session_id=cid,
        child_status='completed', child_summary=SUMMARY)


def test_sync_fresh_starts_separate_identical_receipts(publication_env):
    env = publication_env
    launch(env.runtime, 'child-a')
    first = sync(env.runtime)
    assert first == sync(env.runtime)
    assert len(env.calls) == 1
    launch(env.runtime, 'child-b')
    sync(env.runtime)
    assert len(env.calls) == 2, 'Новый lifecycle start не должен получать старый review task_index=0'
    sync(env.runtime)
    assert len(env.calls) == 2, 'Повтор доставки без нового start должен переиспользовать review'


def test_main_cache_fenced_by_recorded_turn_without_retry_reset(publication_env):
    env = publication_env
    runtime = env.runtime
    def begin(turn):
        runtime.pre_llm_call(session_id='p', turn_id=turn, user_message=GOAL)
    begin('t1')
    first = runtime.transform_llm_output(session_id='p', response_text=SUMMARY)
    assert first == runtime.transform_llm_output(session_id='p', response_text=SUMMARY)
    assert len(env.calls) == 1
    begin('t2')
    runtime.transform_llm_output(session_id='p', response_text=SUMMARY)
    assert len(env.calls) == 2, 'Общий cache не должен переносить review между recorded turns'
    assert len(runtime.main_state('p')['cache']) == 2
    def adverse(request):
        payload = json.loads(request.content)
        env.calls.append((request, payload))
        return httpx.Response(200, json=answer_payload(payload, completed=.4, reliable=.5, adverse=.7))
    runtime.transport = httpx.MockTransport(adverse)
    begin('t3')
    _, state = runtime.store.session('p')
    a = runtime.final_review('p', state, 'Готово без тестов.')
    assert a['verdict'] == 'RETRY' and len(env.calls) == 3
    assert runtime.final_review('p', state, 'Готово без тестов.') == a
    assert len(env.calls) == 3, 'Повтор финала не пополняет бюджет одного native turn'
    child = runtime.cached_review('p', GOAL, {'summary': SUMMARY})
    assert child['verdict'] == 'RETRY' and len(state['retries']) == 2
    assert len(env.calls) == 4
    begin('t4')
    b = runtime.final_review('p', state, 'Готово без тестов.')
    assert b['verdict'] == 'RETRY' and len(env.calls) == 5 and len(state['retries']) == 3
    assert runtime.final_review('p', state, 'Готово без тестов.') == b
    assert len(env.calls) == 5, 'Новый turn допускает ровно один новый main review'
    child_again = runtime.cached_review('p', GOAL, {'summary': SUMMARY})
    assert child_again['verdict'] == 'INSPECT' and len(state['retries']) == 3
    assert len(env.calls) == 6, 'Main turn reset не сбрасывает прежний goal-cap дочерних рекомендаций'


class HTTPBomb(httpx.SyncByteStream):
    def __init__(self):
        self.reads = 0
        self.closed = threading.Event()
    def __iter__(self):
        import gzip
        self.reads += 1
        yield gzip.compress(b'x' * 262144)
    def close(self):
        self.closed.set()


def test_encoded_body_refused_before_decoder(publication_env):
    runtime = publication_env.runtime
    bomb = HTTPBomb()
    requests = []
    def reply(request):
        requests.append(request)
        return httpx.Response(200, headers={'Content-Encoding': 'gzip'}, stream=bomb)
    runtime.transport = httpx.MockTransport(reply)
    review = runtime.review(GOAL, {'status': 'completed', 'summary': SUMMARY})
    assert not review['verified'] and review['verdict'] == 'INSPECT'
    assert bomb.reads == 0, 'Encoded body должен быть отклонён до HTTPX decoder/stream read'
    assert bomb.closed.is_set()
    assert len(requests) == 1 and requests[0].headers['Accept-Encoding'] == 'identity'
    class Raw(httpx.SyncByteStream):
        def __iter__(self):
            yield b'x' * 131072
            yield b'x'
    runtime.transport = httpx.MockTransport(lambda req: httpx.Response(200, stream=Raw()))
    assert not runtime.review(GOAL, {'summary': SUMMARY})['verified']


class ObservedSlot:
    def __init__(self):
        self.lock = threading.Lock()
        self.finished = threading.Event()
    def acquire(self, blocking=False):
        return self.lock.acquire(blocking=blocking)
    def release(self):
        self.lock.release()
        self.finished.set()
    def locked(self):
        return self.lock.locked()


@pytest.mark.parametrize('resume_after_deadline', [True, False], ids=['expired', 'predeadline-control'])
def test_raw_deadline_stops_drip_without_releasing_blocked_worker(
        publication_env, monkeypatch, record_property, resume_after_deadline):
    runtime = publication_env.runtime
    runtime.ctx.set_config('callback_budget_seconds', .05)
    runtime.ctx.set_config('timeout_seconds', .5)
    assert runtime.setting('callback_budget_seconds', 25) == .05
    assert runtime.setting('timeout_seconds', 10) == .5
    package = importlib.import_module(type(runtime).__module__)
    transport = importlib.import_module(type(runtime).__module__ + '.transport')
    original = transport.request_once
    origin = Path(transport.__file__).resolve()
    assert origin == Path(runtime.review.__func__.__globals__['__file__']).resolve().with_name('transport.py')
    assert Path(original.__code__.co_filename).resolve() == origin
    record_property('transport_module', original.__module__)
    record_property('transport_source', str(origin))
    record_property('transport_sha256', hashlib.sha256(origin.read_bytes()).hexdigest())
    record_property('effective_budget', runtime.setting('callback_budget_seconds', 25))
    record_property('effective_timeout', runtime.setting('timeout_seconds', 10))
    record_property('real_monotonic_clock', json.dumps(vars(time.get_clock_info('monotonic')), sort_keys=True))
    # Event.wait uses real elapsed time; a coarse monotonic clock need not yet have
    # reached end when the caller returns. Control the logical boundary explicitly,
    # only in the two production modules, never in global time or native core.
    clock = SimpleNamespace(value=100.0)
    samples, worker_results = [], []
    def observed_clock():
        value = clock.value
        samples.append({'deadline_check_at': value})
        return value
    clock_seam = SimpleNamespace(monotonic=observed_clock)
    monkeypatch.setattr(package, 'time', clock_seam)
    monkeypatch.setattr(transport, 'time', clock_seam)
    def observed(*args, **kwargs):
        samples.append({'transport_start': clock.value, 'deadline': kwargs['deadline'],
                        'timeout': args[5], 'budget': runtime.setting('callback_budget_seconds', 25)})
        result = original(*args, **kwargs)
        worker_results.append(result)
        return result
    monkeypatch.setattr(transport, 'request_once', observed)
    runtime.inflight = ObservedSlot()
    entered, release, extra, closed = (threading.Event() for _ in range(4))
    requests = []
    class Drip(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{'
            entered.set()
            assert release.wait(2), 'Fixture release отсутствует'
            yield body[1:]
            extra.set()
        def close(self):
            closed.set()
    def reply(request):
        nonlocal body
        requests.append(request)
        body = json.dumps(answer_payload(json.loads(request.content))).encode()
        return httpx.Response(200, stream=Drip())
    body = b''
    runtime.transport = httpx.MockTransport(reply)
    try:
        first = runtime.review(GOAL, {'summary': SUMMARY})
        assert entered.is_set() and not first['verified'] and first['verdict'] == 'INSPECT'
        assert runtime.inflight.locked(), 'Caller timeout не освобождает живой worker'
        second = runtime.review(GOAL, {'summary': SUMMARY})
        assert not second['verified'] and len(requests) == 1
    finally:
        clock.value = 100.05 if resume_after_deadline else 100.049
        samples.append({'release_at': clock.value, 'extra_before_release': extra.is_set()})
        release.set()
        assert runtime.inflight.finished.wait(2)
        record_property('deadline_diagnostic', json.dumps(samples, sort_keys=True))
        record_property('resume_after_deadline', resume_after_deadline)
    if resume_after_deadline:
        assert not extra.is_set(), 'Raw stream продолжил чтение после callback deadline'
        assert len(worker_results) == 1 and not worker_results[0]['verified']
    else:
        # Positive counterexample to the old timing assumption: caller INSPECT is
        # not itself proof the worker's monotonic deadline has elapsed.
        assert extra.is_set() and len(worker_results) == 1 and worker_results[0]['verified']
    assert closed.is_set() and not runtime.inflight.locked()
    assert not runtime.store.sessions, 'Поздний direct review не применяется к retained state'


@pytest.mark.parametrize('surface', ['review', 'cache', 'sync', 'main', 'nudge', 'async'])
def test_unload_discards_late_review_and_all_application(publication_env, surface):
    import contextvars
    runtime = publication_env.runtime
    entered, release = threading.Event(), threading.Event()
    result, errors = [], []
    runtime.inflight = ObservedSlot()
    def reply(request):
        entered.set()
        assert release.wait(2), 'Fixture release отсутствует'
        payload = json.loads(request.content)
        options = {'completed': .4, 'reliable': .5, 'adverse': .7} if surface == 'nudge' else {}
        return httpx.Response(200, json=answer_payload(payload, **options))
    runtime.transport = httpx.MockTransport(reply)
    runtime.pre_llm_call(session_id='p', turn_id='t1', user_message=GOAL)
    _, state = runtime.store.session('p')
    if surface == 'async':
        launch(runtime, 'child-async')
        runtime.record_dispatch('p', {'subagent_ids': ['sub-child-async'], 'delegation_id': 'delivery-a'})
    def invoke():
        if surface == 'review': return runtime.review(GOAL, {'summary': SUMMARY})
        if surface == 'cache': return runtime.cached_review('p', GOAL, {'status': 'completed', 'summary': SUMMARY})
        if surface == 'sync': return sync(runtime)
        if surface == 'main': return runtime.transform_llm_output(session_id='p', response_text=SUMMARY)
        if surface == 'nudge': return runtime.pre_verify(session_id='p', final_response=SUMMARY, changed_paths=['component.py'])
        message = '[ASYNC DELEGATION COMPLETE — delivery-a]\nГотово'
        return runtime.pre_llm_call(session_id='p', turn_id='t1', user_message=message,
            conversation_history=[{'role': 'user', 'content': message,
                'display_kind': 'async_delegation_complete', 'display_metadata': {'delegation_id': 'delivery-a'}}])
    def call():
        try: result.append(invoke())
        except BaseException as exc: errors.append(exc)
    owner = contextvars.copy_context()
    caller = threading.Thread(target=lambda: owner.run(call))
    caller.start()
    try:
        assert entered.wait(1), 'Запрос не достиг транспорта'
        runtime.close()
        assert runtime.closed and not runtime.store.sessions
        assert runtime.inflight.locked(), 'Unload не должен отпускать фактически живой worker'
    finally:
        release.set()
        caller.join(2)
        assert runtime.inflight.finished.wait(2)
    assert not caller.is_alive() and not errors
    if surface in ('review', 'cache'):
        assert result and not result[0]['verified'], 'После unload late verified ответ запрещён'
    else:
        assert result == [None], 'После unload нельзя применять advisory или nudge'
    assert not state['cache'] and not state['nudges'], 'После unload нельзя обновлять retained state'
    assert not runtime.store.sessions
    assert not (publication_env.home / 'plugin-data/pplx-decider-review/reviews.jsonl').exists(), 'После unload audit write запрещён'
