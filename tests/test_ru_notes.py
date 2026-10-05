import httpx
from conftest import start, answer_payload
from test_main import begin
from test_async import launch, stop, delivery


def test_main_adverse_probability_is_explained_in_russian_not_fact_claim(env):
    start(env); begin(env)
    def reply(req, payload):
        data = answer_payload(payload)
        data['answers']['internal_contradiction']['noul'] = .9
        return httpx.Response(200, json=data)
    env.replies.append(reply)
    result = env.ctx.hooks['pre_verify'](session_id='p', final_response='Готово; проверка противоречива.', changed_paths=['C:/a'], attempt=0)
    assert result['action'] == 'continue'
    assert 'наличия противоречий — 90,0%' in result['message']
    assert 'вероятность' in result['message']


def test_async_review_notes_correlate_child_identity_and_bound_context(env):
    start(env); launch(env, 2); stop(env, 0); stop(env, 1)
    output = delivery(env)['context']
    assert 'child0' in output and 'child1' in output
    assert len(output) <= 16000
