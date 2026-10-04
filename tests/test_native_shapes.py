import json
from conftest import start
from test_async import launch, stop, delivery


def test_native_completed_with_budget_truncation_cannot_become_accept(env):
    start(env)
    native = {'results': [{'task_index': 0, 'status': 'completed', 'summary': 'Частичный результат',
                           'exit_reason': 'max_iterations', 'truncated': True, 'schema_valid': False, 'schema_errors': ['field required']}]}
    output = env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args={'goal': 'Полностью проверить компонент'}, result=json.dumps(native), session_id='p')
    review = json.loads(output)['pplx_review'][0]
    assert review['verdict'] != 'ACCEPT'
    assert env.calls[0][1]['state']['evidence']['truncated'] is True
    assert env.calls[0][1]['state']['evidence']['schema_valid'] is False
    assert json.loads(output)['results'] == native['results']


def test_identical_evidence_two_meaningful_children_get_one_request_each(env):
    start(env)
    native = {'results': [{'task_index': i, 'status': 'completed', 'summary': 'pytest: 2 passed.'} for i in (0, 1)]}
    args = {'tasks': [{'goal': 'Проверить компонент'}, {'goal': 'Проверить компонент'}]}
    output = env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args=args, result=json.dumps(native), session_id='p')
    assert len(json.loads(output)['pplx_review']) == 2 and len(env.calls) == 2
    env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args=args, result=json.dumps(native), session_id='p')
    assert len(env.calls) == 2


def test_identical_async_children_do_not_collapse_into_one_paid_review(env):
    start(env)
    # Native child identities независимы даже при одинаковой цели/summary.
    for i in (0, 1):
        env.ctx.hooks['subagent_start'](parent_session_id='p', child_session_id='child' + str(i), child_subagent_id='sub' + str(i), child_goal='Проверить компонент')
    env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args={'goal': 'Проверить компонент'},
        result=json.dumps({'status': 'dispatched', 'delegation_id': 'deleg-native', 'subagent_ids': ['sub0', 'sub1']}), session_id='p')
    stop(env, 0); stop(env, 1)
    assert delivery(env) is not None
    assert len(env.calls) == 2
