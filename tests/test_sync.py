import json
import pytest
from conftest import start

@pytest.mark.parametrize('result,args', [
    ('не JSON', {'goal': 'Проверить файл'}),
    ('[]', {'goal': 'Проверить файл'}),
    ('{"results": null}', {'goal': 'Проверить файл'}),
    ('{"results": [null, 3]}', {'goal': 'Проверить файл'}),
    ('{"status":"dispatched","delegation_id":"d","goals":["цель"]}', {'goal': 'Проверить файл'}),
    ('{"status":"running","summary":"ещё работает"}', {'goal': 'Проверить файл'}),
    ('{"results":[{"status":"running","summary":"работает"}]}', {'goal': 'Проверить файл'}),
    ('{"results":[{"status":"completed","summary":"готово"}]}', {'action': 'list', 'goal': 'Проверить файл'}),
    ('{"results":[{"status":"completed","summary":"готово"}]}', {'action': 'stop', 'goal': 'Проверить файл'}),
    ('{"results":[{"status":"completed","summary":"готово"}]}', {}),
    ({'results': []}, {'goal': 'Проверить файл'}),
])
def test_nonmeaningful_control_invalid_and_nonstring_skip_network(env, result, args):
    start(env)
    assert env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args=args, result=result, session_id='p') is None
    assert not env.calls and not env.secrets


def test_one_request_per_child_correct_task_goals_and_original_preservation(env):
    start(env)
    native = {'results': [{'task_index': 1, 'status': 'completed', 'summary': 'B готово', 'extra': {'keep': True}},
                          {'task_index': 0, 'status': 'completed', 'summary': 'A готово'}], 'opaque': 'сохранить'}
    output = env.ctx.hooks['transform_tool_result'](tool_name='delegate_task',
        args={'tasks': [{'goal': 'Проверить компонент A'}, {'goal': 'Проверить компонент B'}]}, result=json.dumps(native), session_id='p')
    updated = json.loads(output)
    assert {k: updated[k] for k in native} == native
    assert len(updated['pplx_review']) == 2
    assert [p['state']['goal'] for _, p in env.calls] == ['Проверить компонент B', 'Проверить компонент A']


def test_identical_sync_completion_deduplicated_but_sessions_and_profiles_isolated(env):
    start(env)
    native = json.dumps({'results': [{'status': 'completed', 'summary': 'Тесты: 2 passed.'}]})
    def run(sid):
        return env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args={'goal': 'Проверить компонент'}, result=native, session_id=sid)
    assert run('p') == run('p')
    assert len(env.calls) == 1
    run('other'); assert len(env.calls) == 2
    env.home[0] = env.home[0].parent / 'synthetic-profile-b'
    run('p'); assert len(env.calls) == 3
    assert env.secrets[-1][0] == str(env.home[0])

@pytest.mark.parametrize('status', ['error', 'timeout', 'interrupted', 'max_iterations', 'partial'])
def test_native_failure_cannot_become_accept_even_optimistic_model(env, status):
    start(env)
    item = {'status': status, 'error': 'Не удалось проверить файл', 'summary': None}
    output = env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args={'goal': 'Проверить компонент'}, result=json.dumps({'results': [item]}), session_id='p')
    assert json.loads(output)['results'][0] == item
    assert json.loads(output)['pplx_review'][0]['verdict'] != 'ACCEPT'
    assert len(env.calls) == 1

@pytest.mark.parametrize('setting', ['enabled', 'review_subagents'])
def test_disable_child_review_has_no_network(env, setting):
    env.ctx.settings[setting] = False
    start(env)
    output = env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args={'goal': 'Проверить компонент'}, result='{"results":[{"status":"completed","summary":"готово"}]}', session_id='p')
    assert output is None and not env.calls


def test_existing_review_field_not_overwritten(env):
    start(env)
    native = {'results': [{'status': 'completed', 'summary': 'готово'}], 'pplx_review': {'untrusted_existing': True}}
    output = env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args={'goal': 'Проверить компонент'}, result=json.dumps(native), session_id='p')
    updated = json.loads(output)
    assert updated['original'] == native
    assert updated['pplx_review'][0]['verified']
