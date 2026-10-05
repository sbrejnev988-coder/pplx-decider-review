import json
import sys
import httpx
import pytest
from conftest import start, answer_payload
from test_async import launch

MAIN_KEYS = {'task_satisfied', 'important_requirement_missed', 'claims_supported', 'internal_contradiction', 'needs_revision', 'overall_quality', 'next_action'}


def begin(env, message='Реализовать компонент и проверить тестами', sid='p', parent=''):
    return env.ctx.hooks['pre_llm_call'](session_id=sid, task_id='task-main', turn_id='turn-main',
                                        user_message=message, conversation_history=[{'role': 'user', 'content': message}], parent_session_id=parent)


def adverse_reply(req, payload):
    return httpx.Response(200, json=answer_payload(payload, completed=.4, reliable=.5, adverse=.7))


def test_main_preverify_real_edits_one_continue_with_ru_probability_context(env):
    start(env); begin(env); env.replies.append(adverse_reply)
    result = env.ctx.hooks['pre_verify'](session_id='p', final_response='Компонент готов; тесты не запускались.', changed_paths=['C:/synthetic/a.py'], attempt=0)
    assert result['action'] == 'continue'
    assert 'Sol' in result['message'] and 'вероятность' in result['message']
    assert len(env.calls) == 1
    assert set(env.calls[0][1]['questions']) == MAIN_KEYS
    second = env.ctx.hooks['pre_verify'](session_id='p', final_response='Теперь готово, но всё ещё без тестов.', changed_paths=['C:/synthetic/a.py'], attempt=0)
    assert second is None
    third = env.ctx.hooks['pre_verify'](session_id='p', final_response='Готово.', changed_paths=['C:/synthetic/a.py'], attempt=1)
    assert third is None and len(env.calls) == 1


def test_no_tracked_edits_no_continue_but_general_final_still_advisory(env):
    start(env); begin(env); env.replies.append(adverse_reply)
    assert env.ctx.hooks['pre_verify'](session_id='p', final_response='Готово.', changed_paths=[], attempt=0) is None
    assert not env.calls
    original = 'Компонент готов. Исходные свидетельства сохранены.'
    text = env.ctx.hooks['transform_llm_output'](session_id='p', response_text=original)
    assert text.startswith(original)
    assert 'PPLX' in text and 'Ограничение' in text
    assert isinstance(text, str) and len(env.calls) == 1


def test_main_review_fail_open_preserves_final_adds_ru_warning_no_fake_continue(env, monkeypatch):
    start(env); begin(env)
    monkeypatch.setattr(sys.modules['agent.secret_scope'], 'get_secret', lambda *a: '')
    assert env.ctx.hooks['pre_verify'](session_id='p', final_response='Готово.', changed_paths=['C:/a.py'], attempt=0) is None
    original = 'Готово. Оригинальное заявление не переписано.'
    output = env.ctx.hooks['transform_llm_output'](session_id='p', response_text=original)
    assert output.startswith(original) and 'заключение PPLX отсутствует' in output
    assert not env.calls

@pytest.mark.parametrize('method', ['pre_verify', 'transform_llm_output'])
def test_child_final_skip_by_start_provenance_even_without_parent_payload(env, method):
    start(env); launch(env)
    begin(env, sid='child0')
    kw = {'changed_paths': ['C:/a.py'], 'final_response': 'Готово.', 'attempt': 0} if method == 'pre_verify' else {'response_text': 'Готово.'}
    assert env.ctx.hooks[method](session_id='child0', **kw) is None
    assert not env.calls


def test_child_final_skip_by_native_parent_payload_without_start(env):
    start(env); begin(env, sid='child0', parent='p')
    assert env.ctx.hooks['transform_llm_output'](session_id='child0', response_text='Готово.') is None
    assert not env.calls

@pytest.mark.parametrize('message', ['', 'Спасибо!', 'ок', 'Понятно.'])
def test_trivial_closer_no_meaningful_goal_skipped(env, message):
    start(env); begin(env); begin(env, message)
    assert env.ctx.hooks['transform_llm_output'](session_id='p', response_text='Пожалуйста.') is None
    assert not env.calls


def test_disabled_main_and_advisory_mode_do_not_force_continue(env):
    start(env); begin(env)
    env.ctx.settings['mode'] = 'advisory'
    assert env.ctx.hooks['pre_verify'](session_id='p', final_response='Готово.', changed_paths=['C:/a.py'], attempt=0) is None
    assert not env.calls
    env.ctx.settings['review_main_agent'] = False
    assert env.ctx.hooks['transform_llm_output'](session_id='p', response_text='Готово.') is None
    assert not env.calls


def test_same_parent_goal_retry_recommendation_cap_across_changed_evidence(env):
    start(env)
    def run(summary):
        env.replies.append(adverse_reply)
        output = env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args={'goal': 'Проверить компонент'},
            result=json.dumps({'results': [{'status': 'completed', 'summary': summary}]}), session_id='p')
        return json.loads(output)['pplx_review'][0]
    first = run('Первая попытка не выполнила цель')
    assert first['verdict'] == 'RETRY' and 'Sol' in first['recommendation']
    second = run('Вторая попытка также не выполнила цель')
    assert second['verdict'] == 'INSPECT' and 'лимит' in second['reason']
    assert len(env.calls) == 2


def test_main_final_reuses_same_candidate_preverify_review(env):
    start(env); begin(env)
    text = 'Компонент готов, pytest: 2 passed.'
    assert env.ctx.hooks['pre_verify'](session_id='p', final_response=text, changed_paths=['C:/a.py'], attempt=0) is None
    output = env.ctx.hooks['transform_llm_output'](session_id='p', response_text=text)
    assert output.startswith(text)
    assert len(env.calls) == 1
