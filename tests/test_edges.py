import json
import time
from conftest import start
from test_main import begin
from test_async import launch, stop, delivery


def run_child(env, summary='готово'):
    return env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args={'goal': 'Проверить компонент'},
        result=json.dumps({'results': [{'status': 'completed', 'summary': summary}]}), session_id='p')


def test_route_change_never_reuses_cached_verified_answer(env):
    start(env); assert json.loads(run_child(env))['pplx_review'][0]['verified']
    before = len(env.secrets)
    env.ctx.settings['endpoint'] = 'https://evil.example'
    result = json.loads(run_child(env))['pplx_review'][0]
    assert not result['verified']
    assert len(env.calls) == 1 and len(env.secrets) == before


def test_embedded_quoted_password_key_and_environment_text_filtered(env):
    start(env)
    summary = 'Тесты выполнены.\n{"password": "quotedSecretABCDEFG", "api_key": "fakeKeyABCDEFG"}\nexport CUSTOM_NAME=envQuotedSecret'
    run_child(env, summary)
    body = env.calls[0][0].content.decode()
    for secret in ('quotedSecretABCDEFG', 'fakeKeyABCDEFG', 'envQuotedSecret'): assert secret not in body


def test_main_same_text_new_turn_changed_files_does_not_reuse_old_review(env):
    start(env); begin(env)
    hook = env.ctx.hooks['pre_verify']
    hook(session_id='p', attempt=0, changed_paths=['C:/a.py'], final_response='Готово; тесты прошли.')
    env.ctx.hooks['pre_llm_call'](session_id='p', task_id='task-main', turn_id='next-turn', user_message='Реализовать компонент и проверить тестами')
    hook(session_id='p', attempt=0, changed_paths=['C:/b.py'], final_response='Готово; тесты прошли.')
    assert len(env.calls) == 2
    assert env.calls[-1][1]['state']['evidence']['changed_paths'] == ['C:/b.py']


def test_cached_final_expires_independent_of_active_session(env):
    start(env); begin(env); env.ctx.settings['cache_ttl_seconds'] = .03
    hook = env.ctx.hooks['transform_llm_output']
    hook(session_id='p', response_text='Компонент готов; тесты прошли.')
    time.sleep(.05)
    hook(session_id='p', response_text='Компонент готов; тесты прошли.')
    assert len(env.calls) == 2


def test_child_stop_expiry_not_refreshed_by_parent_activity(env):
    start(env); env.ctx.settings['state_ttl_seconds'] = .05
    launch(env); stop(env)
    for _ in range(5):
        time.sleep(.02); begin(env)
    assert delivery(env) is None
    assert not env.calls


def test_main_low_metric_even_strong_quality_cannot_accept(env):
    import httpx
    from conftest import answer_payload
    start(env); begin(env)
    def reply(req, payload):
        d = answer_payload(payload, completed=.5, reliable=.9, adverse=.01)
        d['answers']['overall_quality']['confidence'] = 1
        d['answers']['overall_quality']['score'] = 4
        return httpx.Response(200, json=d)
    env.replies.append(reply)
    text = env.ctx.hooks['transform_llm_output'](session_id='p', response_text='Компонент частично выполнен.')
    assert 'PPLX RETRY' in text


def test_error_log_has_no_raw_http_body_credentials_or_prompt(env):
    import httpx
    start(env)
    env.replies.append(lambda req, p: httpx.Response(429, text='RAW_SECRET_BODY'))
    run_child(env, 'RAW_PRIVATE_EVIDENCE')
    path = env.home[0] / 'plugin-data/pplx-decider-review/reviews.jsonl'
    text = path.read_text(encoding='utf-8')
    assert 'RAW_SECRET_BODY' not in text and 'RAW_PRIVATE_EVIDENCE' not in text and 'synthetic-owner-key' not in text
    assert 'HTTP 429' in text
