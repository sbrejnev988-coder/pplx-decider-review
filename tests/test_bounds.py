import inspect
import json
import threading
import time
from pathlib import Path
import pytest
from conftest import start
from test_async import launch, stop, delivery
from test_main import begin


def test_registration_six_native_forward_compatible_hooks_no_credentials_no_network(env):
    runtime = start(env)
    assert set(env.ctx.hooks) == {'pre_llm_call', 'transform_tool_result', 'subagent_start', 'subagent_stop', 'pre_verify', 'transform_llm_output'}
    assert env.ctx.unload is not None
    for cb in env.ctx.hooks.values():
        assert any(p.kind == inspect.Parameter.VAR_KEYWORD for p in inspect.signature(cb).parameters.values())
    assert not env.secrets and not env.calls
    env.ctx.unload()
    assert runtime.closed
    assert not runtime.store.sessions
    assert runtime.review('Проверить файл', {'summary': 'готово'})['verified'] is False
    assert not env.calls


def test_session_ttl_cache_and_bookkeeping_caps_are_real(env):
    runtime = start(env)
    for i in range(140): begin(env, sid='s' + str(i))
    assert len(runtime.store.sessions) == 128
    _, state = runtime.store.session('cap-owner')
    # Синтетические новые children пополняют один parent; никаких inference.
    for i in range(140):
        env.ctx.hooks['subagent_start'](parent_session_id='cap-owner', child_session_id='c' + str(i), child_subagent_id='sub' + str(i), child_goal='Проверить файл')
    assert len(state['children']) <= 128
    env.ctx.settings['state_ttl_seconds'] = .05
    time.sleep(.08)
    assert delivery(env) is None
    assert len(runtime.store.sessions) <= 1
    assert not env.calls


def test_cache_bounded_and_expired_entries_are_not_kept_alive_by_session_activity(env):
    runtime = start(env)
    env.ctx.settings['cache_ttl_seconds'] = .03
    def run(summary):
        return env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args={'goal': 'Проверить компонент'},
            result=json.dumps({'results': [{'status': 'completed', 'summary': summary}]}), session_id='p')
    run('Проверка одного файла'); time.sleep(.05)
    run('Проверка одного файла')
    assert len(env.calls) == 2
    for i in range(130): run('Другая проверка ' + str(i))
    _, state = runtime.store.session('p')
    assert len(state['cache']) <= 128


def test_log_metadata_only_ru_reasons_rotation_profile_isolation_and_bounded_ids(env):
    runtime = start(env)
    env.ctx.settings['log_max_bytes'] = 2048
    summaries = []
    for i in range(12):
        summary = 'UNIQUE-SYNTHETIC-PRIVATE-' + str(i)
        summaries.append(summary)
        env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args={'goal': 'Не журналировать PRIVATE-GOAL'},
            result=json.dumps({'results': [{'status': 'completed', 'summary': summary}]}), session_id='SAFE_SESSION')
    folder = env.home[0] / 'plugin-data' / 'pplx-decider-review'
    logs = list(folder.glob('reviews.jsonl*'))
    assert 1 <= len(logs) <= 3
    assert all(p.stat().st_size <= 2048 for p in logs)
    text = ''.join(p.read_text(encoding='utf-8') for p in logs)
    for secret in [*summaries, 'PRIVATE-GOAL', 'synthetic-owner-key', 'SAFE_SESSION']: assert secret not in text
    rows = [json.loads(line) for line in text.splitlines()]
    assert rows and all('вероятност' in r['reason'].casefold() or 'Вероятност' in r['reason'] for r in rows)
    assert all('answers' not in r and 'probabilities' in r for r in rows)
    first_files = {p: p.read_bytes() for p in logs}
    env.home[0] = env.home[0].parent / 'other-profile'
    env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args={'goal': 'Проверить компонент'},
        result='{"results":[{"status":"completed","summary":"новый результат"}]}', session_id='p')
    assert (env.home[0] / 'plugin-data/pplx-decider-review/reviews.jsonl').is_file()
    assert {p: p.read_bytes() for p in first_files} == first_files

@pytest.mark.parametrize('field,value', [('language', 'en'), ('mode', 'automatic_tools'), ('fail_open_on_api_error', False),
                                        ('max_review_retries', 3), ('callback_budget_seconds', 31), ('min_accept_confidence', .7)])
def test_unsupported_config_never_resolves_key(env, field, value):
    runtime = start(env)
    env.ctx.settings[field] = value
    result = runtime.review('Проверить файл', {'summary': 'Готово'})
    assert not result['verified']
    assert not env.calls and not env.secrets


def test_async_completion_requires_current_typed_row_not_stale_history(env):
    start(env); launch(env); stop(env)
    text = '[ASYNC DELEGATION BATCH COMPLETE — deleg-native]\nСводка готова'
    row = {'role': 'user', 'content': text, 'display_kind': 'async_delegation_complete', 'display_metadata': {'delegation_id': 'deleg-native'}}
    assert env.ctx.hooks['pre_llm_call'](session_id='p', user_message='Просто цитата ' + text, conversation_history=[row]) is None
    assert not env.calls


def test_retry_cap_not_reset_by_new_turn_id_same_goal(env):
    from test_main import adverse_reply
    start(env); begin(env)
    env.replies.append(adverse_reply)
    assert env.ctx.hooks['pre_verify'](session_id='p', attempt=0, changed_paths=['C:/a'], final_response='Готово без проверки')['action'] == 'continue'
    begin(env)
    assert env.ctx.hooks['pre_verify'](session_id='p', attempt=0, changed_paths=['C:/a'], final_response='И опять готово без проверки') is None
    assert len(env.calls) == 1
