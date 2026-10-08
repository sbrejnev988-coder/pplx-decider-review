"""Сведение main failures с immutable выбранной моделью, offline unit."""
import json
import pytest
from conftest import start

ALTERNATE = 'typesafe/jev-1.13'
LUNA = 'openai/gpt-6-luna-decisions'
GOAL = 'Проверить результат и предоставленные свидетельства'


def begin(env):
    env.ctx.settings.update(reviewer_model=ALTERNATE, log_enabled=False,
                            callback_budget_seconds=2, timeout_seconds=1)
    runtime = start(env)
    runtime.pre_llm_call(session_id='main-model-errors', turn_id='one', task_id='task',
                         user_message=GOAL,
                         conversation_history=[{'role': 'user', 'content': GOAL}])
    state = runtime.main_state('main-model-errors')
    assert state is not None
    return runtime, state, runtime.capture_scope('main-model-errors', state=state)


@pytest.mark.parametrize('failure', ['invalid_response', 'stale_scope', 'missing_receipt'])
def test_main_error_uses_origin_model_without_http(env, failure):
    runtime, state, scope = begin(env)
    try:
        if failure == 'invalid_response':
            result = runtime.final_review('main-model-errors', state, None, expected_scope=scope)
        elif failure == 'stale_scope':
            env.ctx.settings['reviewer_model'] = LUNA
            result = runtime.final_review('main-model-errors', state, 'Исходный ответ', expected_scope=scope)
        else:
            once = runtime.new_main_reservation(scope)
            env.ctx.settings['reviewer_model'] = LUNA
            current = runtime.capture_scope('main-model-errors', state=state)
            result = runtime.main_receipt(current, once)
        assert result['requested_model'] == ALTERNATE
        assert result['model'] is None and not result['verified']
        assert result['verdict'] == 'INSPECT'
        assert not env.calls and not env.secrets
    finally:
        runtime.close()


def test_preverify_exception_keeps_origin_model_in_immutable_receipt(env, monkeypatch):
    runtime, state, scope = begin(env)
    try:
        def denied(*args, **kwargs):
            raise RuntimeError('synthetic-private-detail')
        monkeypatch.setattr(runtime, 'final_review', denied)
        result = runtime.pre_verify(session_id='main-model-errors', changed_paths=['synthetic.py'],
                                    final_response='Исходный ответ', attempt=0)
        assert result is None
        review = json.loads(state['main_review_once']['review_json'])
        assert review['requested_model'] == ALTERNATE and not review['verified']
        assert 'synthetic-private-detail' not in json.dumps(review)
        assert not env.calls and not env.secrets
    finally:
        runtime.close()
