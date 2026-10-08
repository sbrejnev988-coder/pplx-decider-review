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


def test_cached_final_expires_independent_of_active_session(env, monkeypatch):
    runtime = start(env)
    env.ctx.settings.update(cache_ttl_seconds=.03, callback_budget_seconds=2, timeout_seconds=1)
    now = [time.monotonic()]
    monkeypatch.setattr(runtime.store, 'clock', lambda: now[0])
    begin(env)
    hook = env.ctx.hooks['transform_llm_output']
    original = 'Компонент готов; тесты прошли.'
    first = hook(session_id='p', response_text=original)
    assert first.startswith(original) and 'DECISIONS ACCEPT' in first
    assert 'DECISIONS ACCEPT' in hook(session_id='p', response_text=original)
    assert len(env.calls) == 1
    state = runtime.main_state('p')

    # Receipt TTL expires even while this session stays active; it is not an HTTP refill.
    now[0] += .05
    begin(env)
    assert runtime.main_state('p') is state
    expired = hook(session_id='p', response_text=original)
    assert expired.startswith(original) and 'DECISIONS INSPECT' in expired
    assert 'DECISIONS ACCEPT' not in expired and len(env.calls) == 1

    env.ctx.hooks['pre_llm_call'](session_id='p', task_id='task-main', turn_id='ttl-next-turn',
                                user_message='Реализовать компонент и проверить тестами')
    fresh = hook(session_id='p', response_text=original)
    assert fresh.startswith(original) and 'DECISIONS ACCEPT' in fresh
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
        # A maximal score requires a coherent point-mass distribution.
        d['answers']['overall_quality']['probabilities'] = {str(i): float(i == 4) for i in range(5)}
        return httpx.Response(200, json=d)
    env.replies.append(reply)
    text = env.ctx.hooks['transform_llm_output'](session_id='p', response_text='Компонент частично выполнен.')
    assert 'DECISIONS RETRY' in text


def test_error_log_has_no_raw_http_body_credentials_or_prompt(env):
    import httpx
    start(env)
    env.replies.append(lambda req, p: httpx.Response(429, text='RAW_SECRET_BODY'))
    run_child(env, 'RAW_PRIVATE_EVIDENCE')
    path = env.home[0] / 'plugin-data/decision-review/reviews.jsonl'
    text = path.read_text(encoding='utf-8')
    assert 'RAW_SECRET_BODY' not in text and 'RAW_PRIVATE_EVIDENCE' not in text and 'synthetic-owner-key' not in text
    assert 'HTTP 429' in text
