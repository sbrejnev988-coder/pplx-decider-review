"""Полная локальная identity ответа не равна lossy egress projection."""
import importlib
import threading
import pytest
from conftest import start

GOAL = 'Проверить компонент и сохранить реальные свидетельства'
DRAFT_SCOPE = 'DECISIONS: исходный черновик (DRAFT), не текущий финальный ответ.'

@pytest.fixture
def exact_renderer(env):
    env.ctx.settings.update(log_enabled=False, callback_budget_seconds=2, timeout_seconds=1)
    runtime = start(env)
    runtime.pre_llm_call(session_id='p', turn_id='turn-1', task_id='task-1', user_message=GOAL)
    try:
        yield runtime
    finally:
        runtime.close()

@pytest.mark.parametrize('equivalence', ['suffix', 'redaction', 'surrogate'])
def test_lossy_equivalent_changed_response_is_not_current_final(env, exact_renderer, equivalence):
    if equivalence == 'suffix':
        a = 'С' * 6000 + '\nОшибок нет.'
        b = 'С' * 6000 + '\nНайдена ошибка.'
    elif equivalence == 'redaction':
        a = 'Результат с Bearer syntheticBearerTokenAAAAAAAA'
        b = 'Результат с Bearer syntheticBearerTokenBBBBBBBB'
    else:
        a, b = 'Результат: \ud800', 'Результат: \ud801'
    transport = importlib.import_module(env.p.__name__ + '.transport')
    assert a != b and transport.scrub_text(a) == transport.scrub_text(b)
    first = exact_renderer.transform_llm_output(session_id='p', response_text=a)
    assert first.startswith(a + '\n\n---\n') and 'DECISIONS ACCEPT' in first and 'DRAFT' not in first
    once = exact_renderer.main_state('p')['main_review_once']
    receipt = once['review_json']
    assert exact_renderer.transform_llm_output(session_id='p', response_text=a) == first
    second = exact_renderer.transform_llm_output(session_id='p', response_text=b)
    assert second.startswith(b + '\n\n---\n') and DRAFT_SCOPE in second
    assert 'Повторная Decisions-оценка финала не выполнялась.' in second
    assert once['review_json'] == receipt and len(env.calls) == len(env.secrets) == 1
    wire = ''.join(request.content.decode('utf-8') for request, _payload in env.calls)
    assert 'syntheticBearerTokenAAAAAAAA' not in wire and 'syntheticBearerTokenBBBBBBBB' not in wire
    assert exact_renderer.transform_llm_output(session_id='p', response_text=a) == first


@pytest.mark.parametrize('mode', ['bounded', 'advisory'])
@pytest.mark.parametrize('repeat', [False, True])
def test_late_evidence_after_candidate_check_refuses_stale_decoration(env, exact_renderer, monkeypatch, mode, repeat):
    runtime = exact_renderer
    env.ctx.settings['mode'] = mode
    text = 'Проверка компонента завершена; исходный ответ сохранён.'
    if repeat:
        first = runtime.transform_llm_output(session_id='p', response_text=text)
        assert 'DECISIONS ACCEPT' in first and 'DRAFT' not in first
    state = runtime.main_state('p')
    scope = runtime.capture_scope('p', state=state)
    revision = state['revision']
    original = runtime.review_note
    events, failures = [], []
    def raced(review, *args, **kwargs):
        def mutate():
            try:
                runtime.record_tool_evidence('p', 'read_file', {'path': 'component.py'}, 'Новые свидетельства после candidate check.')
                events.append('evidence_written')
            except BaseException as error:
                failures.append(type(error).__name__)
        thread = threading.Thread(target=mutate, name='renderer-return-seam')
        thread.start()
        thread.join(2)
        assert not thread.is_alive() and not failures and events == ['evidence_written']
        assert state['revision'] == revision and runtime.scope_current(scope)
        return original(review, *args, **kwargs)
    monkeypatch.setattr(runtime, 'review_note', raced)
    output = runtime.transform_llm_output(session_id='p', response_text=text)
    assert output is None, 'A changed trace must not receive stale final-scoped ACCEPT at the application seam'
    assert len(env.calls) == len(env.secrets) == 1
    assert state['tool_evidence'][-1]['tool_name'] == 'read_file'
    if mode == 'bounded':
        once = state['main_review_once']
        assert once['http_started'] and 'ACCEPT' in once['review_json']
