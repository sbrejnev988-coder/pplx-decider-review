"""Parent acceptance: fresh task identity cannot reuse prior reviewer decision."""
import json
import httpx
import pytest
from conftest import answer_payload
from test_native_config import native_env

pytestmark = pytest.mark.parametrize('scenario', ['valid'])


@pytest.mark.parametrize('surface', ['main', 'sync'])
def test_new_task_same_goal_and_turn_has_distinct_cache_namespace(publication_env, surface):
    runtime = publication_env.runtime
    runtime.ctx.set_config('callback_budget_seconds', 2)
    runtime.ctx.set_config('timeout_seconds', 2)
    runtime.ctx.set_config('log_enabled', False)
    goal = 'Проверить компонент с одинаковой формулировкой цели'
    calls = []
    def reply(req):
        calls.append(1)
        opts = {} if len(calls) == 1 else {'completed': .4, 'reliable': .5, 'adverse': .7}
        return httpx.Response(200, json=answer_payload(json.loads(req.content), **opts))
    runtime.transport = httpx.MockTransport(reply)
    def invoke():
        if surface == 'main':
            return runtime.final_review('p', runtime.main_state('p'), 'Компонент проверен.')
        raw = json.dumps({'results': [{'status': 'completed', 'summary': 'Компонент проверен.', 'task_index': 0}]})
        result = runtime.transform_tool_result(tool_name='delegate_task', session_id='p', args={'tasks': [{'goal': goal}]}, result=raw)
        return json.loads(result)['pplx_review'][0]
    runtime.pre_llm_call(session_id='p', task_id='task-A', turn_id='same-turn', user_message=goal)
    assert invoke()['verdict'] == 'ACCEPT'
    assert invoke()['verdict'] == 'ACCEPT'
    assert len(calls) == 1, 'Same-task replay remains a cache hit'
    runtime.pre_llm_call(session_id='p', task_id='task-B', turn_id='same-turn', user_message=goal)
    fresh = invoke()
    assert len(calls) == 2, 'New task identity must trigger its own review'
    assert fresh['verified'] and fresh['verdict'] == 'RETRY'
