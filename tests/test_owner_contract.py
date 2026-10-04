"""Проверки явных требований владельца, найденных при parent-review."""
import json
from datetime import datetime
import httpx
from conftest import start, answer_payload


def test_audit_has_required_metadata_without_result_text(env):
    runtime = start(env)
    request_id = 'gen-dec-1791093987-vtadaobu3kZ8EiW3B2f2'
    def reply(request, payload):
        data = answer_payload(payload)
        data['id'] = request_id
        return httpx.Response(200, json=data)
    env.replies.append(reply)
    review = runtime.cached_review('parent', 'Проверить тестовую строку', {'status': 'completed', 'summary': 'PRIVATE-SYNTHETIC-RESULT'})
    assert review['verified']
    path = env.home[0] / 'plugin-data/pplx-decider-review/reviews.jsonl'
    row = json.loads(path.read_text(encoding='utf-8').strip())
    assert {'timestamp', 'target_type', 'session_id', 'model', 'request_id', 'probabilities', 'policy_decision', 'latency_ms', 'error_category'} <= row.keys()
    assert datetime.fromisoformat(row['timestamp']).tzinfo is not None
    assert row['target_type'] == 'subagent'
    assert row['model'] == env.p.MODEL
    assert row['request_id'] == request_id
    assert row['policy_decision'] == review['verdict']
    assert type(row['latency_ms']) is int and row['latency_ms'] >= 0
    assert row['error_category'] is None
    assert 'PRIVATE-SYNTHETIC-RESULT' not in path.read_text(encoding='utf-8')


def test_subagent_request_preserves_explicit_task_constraints(env):
    start(env)
    env.ctx.hooks['transform_tool_result'](
        tool_name='delegate_task', session_id='parent',
        args={'tasks': [{'goal': 'Подтвердить тестовую строку', 'context': 'Только вернуть строку; не использовать инструменты.'}]},
        result=json.dumps({'results': [{'status': 'completed', 'summary': 'Тестовая строка'}]}))
    assert len(env.calls) == 1
    assert 'не использовать инструменты' in json.dumps(env.calls[0][1]['state'], ensure_ascii=False)


def test_async_request_preserves_recorded_task_constraints(env):
    from test_async import stop, delivery
    start(env)
    env.ctx.hooks['subagent_start'](parent_session_id='p', child_session_id='child0', child_subagent_id='sub0', child_goal='Проверить компонент 0')
    receipt = {'status': 'dispatched', 'delegation_id': 'deleg-native', 'subagent_ids': ['sub0']}
    env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', session_id='p',
        args={'tasks': [{'goal': 'Проверить компонент 0', 'context': 'Не менять файлы и не запускать команды.'}]}, result=json.dumps(receipt))
    stop(env)
    assert delivery(env) is not None
    assert 'Не менять файлы' in json.dumps(env.calls[0][1]['state'], ensure_ascii=False)


def test_explicit_spawn_action_is_reviewed(env):
    start(env)
    result = env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', session_id='parent',
        args={'action': 'spawn', 'goal': 'Подтвердить тестовую строку'},
        result=json.dumps({'results': [{'status': 'completed', 'summary': 'Тестовая строка'}]}))
    assert result is not None and len(env.calls) == 1


def test_main_review_receives_bounded_tool_evidence_without_per_tool_inference(env):
    start(env)
    env.ctx.hooks['pre_llm_call'](session_id='p', turn_id='t1', user_message='Проверить существование синтетического файла')
    assert env.ctx.hooks['transform_tool_result'](tool_name='terminal', session_id='p', args={'command': 'synthetic-safe-probe'},
        result=json.dumps({'exit_code': 0, 'output': 'VERIFIED-SYNTHETIC-EVIDENCE'})) is None
    assert not env.calls
    env.ctx.hooks['transform_llm_output'](session_id='p', response_text='Проверка выполнена.')
    assert len(env.calls) == 1
    assert 'VERIFIED-SYNTHETIC-EVIDENCE' in json.dumps(env.calls[0][1]['state'], ensure_ascii=False)


def test_main_evidence_does_not_include_secret_file_contents(env):
    start(env)
    env.ctx.hooks['pre_llm_call'](session_id='p', turn_id='t1', user_message='Проверить существование файла настроек')
    env.ctx.hooks['transform_tool_result'](tool_name='read_file', session_id='p', args={'path': 'C:/profile/.env'}, result='PRIVATE-DOTENV-CONTENT')
    env.ctx.hooks['transform_llm_output'](session_id='p', response_text='Файл настроек найден.')
    assert 'PRIVATE-DOTENV-CONTENT' not in json.dumps(env.calls[0][1]['state'], ensure_ascii=False)


def test_async_unknown_completeness_cannot_accept_and_audit_matches(env):
    from test_async import launch, stop, delivery
    start(env)
    launch(env)
    stop(env)
    context = delivery(env)['context']
    assert 'INSPECT' in context and 'ACCEPT' not in context
    row = json.loads((env.home[0] / 'plugin-data/pplx-decider-review/reviews.jsonl').read_text(encoding='utf-8').strip())
    assert row['policy_decision'] == 'INSPECT'
    assert row['verified'] is True
    assert row['probabilities']['goal_completed']['noul'] == 0.95
    assert 'полноты' in row['reason']
