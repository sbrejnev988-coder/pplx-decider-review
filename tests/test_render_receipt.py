"""Offline-регрессии truthful scope заметки immutable main receipt."""
import threading
import httpx
import pytest
from conftest import answer_payload, start

GOAL = 'Проверить компонент и сохранить реальные свидетельства'
FINAL = 'Компонент проверен: 2 passed. Ограничения сохранены.'
DRAFT_SCOPE = 'Область PPLX: исходный черновик (DRAFT), не текущий финальный ответ.'


@pytest.fixture
def renderer(env):
    env.ctx.settings.update(log_enabled=False, callback_budget_seconds=2, timeout_seconds=1)
    runtime = start(env)
    runtime.pre_llm_call(session_id='p', turn_id='turn-1', task_id='task-1',
                         user_message=GOAL)
    try:
        yield runtime
    finally:
        runtime.close()


def render(runtime, text=FINAL, **kwargs):
    return runtime.transform_llm_output(session_id='p', response_text=text, **kwargs)


def test_identical_fallback_final_keeps_exact_note_without_http(env, renderer):
    first = render(renderer)
    assert first.startswith(FINAL + '\n\n---\n')
    assert 'PPLX ACCEPT' in first and 'DRAFT' not in first
    once = renderer.main_state('p')['main_review_once']
    frozen = once['review_json']
    assert once['draft'] is False and len(env.calls) == 1
    for _ in range(3):
        repeated = render(renderer)
        assert repeated == first, 'Same fallback final must retain its truthful final-scoped note'
        assert once['review_json'] == frozen
        assert len(env.calls) == 1
    assert len(env.secrets) == 1


def adverse(request, payload):
    return httpx.Response(200, json=answer_payload(payload, completed=.4, reliable=.5, adverse=.7))


@pytest.mark.parametrize('outcome', ['retry', 'unavailable'])
def test_fallback_non_accept_receipt_is_also_idempotent(env, renderer, outcome):
    env.replies.append(adverse if outcome == 'retry' else httpx.ConnectError('synthetic failure'))
    first = render(renderer)
    assert ('PPLX RETRY' if outcome == 'retry' else 'заключение PPLX отсутствует') in first
    assert 'DRAFT' not in first and 'Ограничение' in first
    assert render(renderer) == first
    assert len(env.calls) == 1 and len(env.secrets) == 1


@pytest.mark.parametrize('retry', [False, True])
def test_same_candidate_preverify_receipt_remains_draft(env, renderer, retry):
    if retry:
        env.replies.append(adverse)
    nudge = renderer.pre_verify(session_id='p', final_response=FINAL, changed_paths=[], all_finals=True)
    assert (nudge is not None) is retry
    once = renderer.main_state('p')['main_review_once']
    frozen = once['review_json']
    assert once['draft'] is True
    first = render(renderer)
    assert first.startswith(FINAL + '\n\n---\n') and DRAFT_SCOPE in first
    assert 'verification_pass_status=unknown' in first
    assert render(renderer) == first and once['review_json'] == frozen
    assert renderer.pre_verify(session_id='p', final_response=FINAL, all_finals=True) is None
    assert len(env.calls) == 1


@pytest.mark.parametrize('change', ['response', 'tool_evidence'])
def test_fallback_receipt_does_not_assess_a_different_candidate(env, renderer, change):
    first = render(renderer)
    original = FINAL
    if change == 'response':
        original = 'Исправленный финал: прежнее неподтверждённое заявление снято.'
    else:
        renderer.record_tool_evidence('p', 'read_file', {'path': 'component.py'}, 'Новый trace.')
    scoped = render(renderer, original)
    assert scoped.startswith(original + '\n\n---\n') and DRAFT_SCOPE in scoped
    assert 'Повторная PPLX-оценка финала не выполнялась.' in scoped
    assert 'verification_pass_status=unknown' in scoped
    assert len(env.calls) == 1 and scoped != first


@pytest.mark.parametrize('status', ['requested', 'completed'])
def test_native_marker_keeps_even_matching_fallback_receipt_draft(env, renderer, status):
    first = render(renderer)
    frozen = renderer.main_state('p')['main_review_once']['review_json']
    scoped = render(renderer, verification_pass_status=status)
    assert DRAFT_SCOPE in scoped and 'verification_pass_status=' + status in scoped
    assert scoped != first and scoped.startswith(FINAL + '\n\n---\n')
    assert render(renderer, verification_pass_status=status) == scoped
    assert renderer.main_state('p')['main_review_once']['review_json'] == frozen
    assert len(env.calls) == 1 and len(env.secrets) == 1


@pytest.mark.parametrize('status', ['requested', 'completed'])
@pytest.mark.parametrize('lost', [False, True])
def test_native_marker_without_receipt_retains_no_http_tombstone(env, renderer, status, lost):
    if lost:
        render(renderer)
        renderer.main_state('p').pop('main_review_once')
    before = len(env.calls)
    scoped = render(renderer, verification_pass_status=status)
    assert DRAFT_SCOPE in scoped and 'заключение PPLX отсутствует' in scoped
    once = renderer.main_state('p')['main_review_once']
    assert once['http_started'] is True and once['review_json'] is None
    assert 'заключение PPLX отсутствует' in render(renderer)
    assert renderer.pre_verify(session_id='p', final_response=FINAL, all_finals=True) is None
    assert len(env.calls) == before


@pytest.mark.parametrize('fence', ['goal', 'policy', 'ttl', 'task'])
def test_invalid_receipt_cannot_replenish_same_turn_http(env, renderer, fence):
    render(renderer)
    once = renderer.main_state('p')['main_review_once']
    frozen = once['review_json']
    if fence in ('goal', 'task'):
        renderer.pre_llm_call(session_id='p', turn_id='turn-1',
            task_id='task-2' if fence == 'task' else 'task-1',
            user_message='Другая цель в том же ходе' if fence == 'goal' else GOAL)
    elif fence == 'policy':
        env.ctx.settings['retry_threshold'] = .6
    else:
        now = renderer.store.clock()
        renderer.store.clock = lambda: now + 301
    scoped = render(renderer)
    assert scoped.startswith(FINAL + '\n\n---\n')
    assert 'заключение PPLX отсутствует' in scoped and 'PPLX ACCEPT' not in scoped
    assert once['review_json'] == frozen
    assert renderer.pre_verify(session_id='p', final_response=FINAL, all_finals=True) is None
    assert len(env.calls) == 1 and len(env.secrets) == 1


@pytest.mark.parametrize('fence', ['policy', 'revision'])
def test_change_after_receipt_read_refuses_stale_application(env, renderer, monkeypatch, fence):
    render(renderer)
    original = renderer.main_receipt
    def raced(scope, once):
        review = original(scope, once)
        if fence == 'policy':
            env.ctx.settings['retry_threshold'] = .6
        else:
            renderer.pre_llm_call(session_id='p', turn_id='turn-1', task_id='task-2', user_message=GOAL)
        return review
    monkeypatch.setattr(renderer, 'main_receipt', raced)
    assert render(renderer) is None, 'Native original remains untouched when stale decoration is refused'
    assert len(env.calls) == 1


def test_mutated_public_cache_does_not_change_frozen_fallback_note(env, renderer):
    first = render(renderer)
    state = renderer.main_state('p')
    frozen = state['main_review_once']['review_json']
    public = renderer.final_review('p', state, FINAL)
    public['verdict'] = 'RETRY'
    public['answers']['task_satisfied']['noul'] = 0
    state['last_final']['review']['verdict'] = 'RETRY'
    state['cache'].clear()
    state.pop('last_final')
    assert render(renderer) == first
    assert state['main_review_once']['review_json'] == frozen and len(env.calls) == 1


def test_new_native_turn_replenishes_main_http_only(env, renderer):
    env.replies.append(adverse)
    assert 'PPLX RETRY' in render(renderer)
    renderer.pre_llm_call(session_id='p', turn_id='turn-2', task_id='task-1', user_message=GOAL)
    env.replies.append(adverse)
    first = render(renderer)
    assert 'PPLX RETRY' in first and 'DRAFT' not in first
    assert render(renderer) == first and len(env.calls) == 2


def test_advisory_keeps_existing_candidate_cache_and_no_nudge(env, renderer):
    env.ctx.settings['mode'] = 'advisory'
    assert renderer.pre_verify(session_id='p', final_response=FINAL, all_finals=True) is None
    first = render(renderer)
    assert render(renderer) == first and 'DRAFT' not in first and len(env.calls) == 1
    assert 'main_review_once' not in renderer.main_state('p')
    original = 'Другой advisory финал с сохранённым исходным текстом.'
    second = render(renderer, original)
    assert second.startswith(original + '\n\n---\n') and 'DRAFT' not in second
    assert len(env.calls) == 2 and 'main_review_once' not in renderer.main_state('p')


@pytest.mark.parametrize('primary', ['pre_verify', 'transform'])
def test_overlapping_main_hooks_keep_one_http_and_original_receipt_scope(env, renderer, primary):
    entered, release = threading.Event(), threading.Event()
    def delayed(request, payload):
        entered.set()
        assert release.wait(3), 'Synthetic HTTP fixture deadline'
        return httpx.Response(200, json=answer_payload(payload))
    env.replies.append(delayed)
    results = []
    def first():
        if primary == 'pre_verify':
            value = renderer.pre_verify(session_id='p', final_response=FINAL, all_finals=True)
        else:
            value = render(renderer)
        results.append(value)
    caller = threading.Thread(target=first)
    caller.start()
    try:
        assert entered.wait(1), 'First callback must reach controlled HTTP'
        if primary == 'pre_verify':
            pending = render(renderer, 'Исправленный кандидат пока receipt не готов.')
            assert DRAFT_SCOPE in pending and 'заключение PPLX отсутствует' in pending
        else:
            assert renderer.pre_verify(session_id='p', final_response=FINAL, all_finals=True) is None
        assert len(env.calls) == 1
    finally:
        release.set()
        caller.join(3)
        assert not caller.is_alive(), 'Fixture caller must finish naturally'
    assert len(results) == 1
    final = render(renderer)
    assert 'PPLX ACCEPT' in final and (DRAFT_SCOPE in final) is (primary == 'pre_verify')
    assert render(renderer) == final and len(env.calls) == 1
