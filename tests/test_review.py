import json
from conftest import start, MODEL


def test_sync_native_result_keeps_every_field_and_reviews_through_decisions(env):
    start(env)
    original = {'results': [{'task_index': 0, 'status': 'completed', 'summary': 'Создан файл; pytest: 2 passed.',
                             'tool_trace': [{'tool_name': 'terminal', 'status': 'success'}], 'exit_reason': 'completed'}],
                'total_duration_seconds': 1.2, 'live_transcripts': ['C:/synthetic/a.txt']}
    output = env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args={'goal': 'Создать файл и проверить тестами'},
                                                   result=json.dumps(original), session_id='parent')
    updated = json.loads(output)
    assert {k: updated[k] for k in original} == original
    review = updated['pplx_review'][0]
    assert review['verdict'] == 'ACCEPT'
    assert review['verified'] is True
    assert review['model'] == MODEL
    assert review['request_id'] == 'synthetic-response'
    assert review['provider'] == 'Perplexity'
    assert review['usage']['input_tokens'] == 10
    assert len(env.calls) == 1
    request, payload = env.calls[0]
    assert str(request.url) == 'https://openrouter.ai/api/alpha/decisions'
    assert payload['model'] == MODEL
    assert payload['state']['goal'] == 'Создать файл и проверить тестами'
    assert 'goal_completed' in payload['questions']
    assert payload['questions']['goal_completed']['criteria'].keys() == {'true', 'false'}
    assert env.redacted
