"""Native regression: безопасный scoped ключ модели на реальных PluginContext/validator."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest
from conftest import MODEL, ROOT, answer_payload

MODEL_KEY = 'reviewer_model'
ENDPOINT = 'https://openrouter.ai/api/alpha/decisions'
UNSUPPORTED_MODEL = 'synthetic-unsupported-reviewer'
HOOKS = {'transform_tool_result', 'pre_llm_call', 'subagent_start', 'subagent_stop', 'pre_verify', 'transform_llm_output'}


@pytest.fixture
def native_env(monkeypatch, tmp_path, record_property, scenario):
    # Никаких fake PluginContext, validator или config readers: настоящие native modules.
    import hermes_cli.plugins as native
    import hermes_cli.plugins_state as native_state
    import hermes_constants
    import agent.secret_scope as scope
    import agent.redact as redact
    core = Path(os.environ['PPLX_TEST_NATIVE_CORE']).resolve()
    assert Path(native.__file__).resolve() == core / 'hermes_cli/plugins.py'
    assert Path(native_state.__file__).resolve() == core / 'hermes_cli/plugins_state.py'
    assert Path(hermes_constants.__file__).resolve() == core / 'hermes_constants.py'
    assert Path(scope.__file__).resolve() == core / 'agent/secret_scope.py'
    assert Path(redact.__file__).resolve() == core / 'agent/redact.py'
    home = tmp_path / 'native-owner'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    settings = {
        'enabled': True, 'provider': 'openrouter', 'endpoint': ENDPOINT, 'language': 'ru',
        'review_subagents': True, 'review_main_agent': True, 'mode': 'bounded',
        'max_review_retries': 1, 'min_accept_confidence': .8, 'retry_threshold': .65,
        'fail_open_on_api_error': True, 'log_enabled': True,
        'callback_budget_seconds': 2, 'timeout_seconds': 1,
    }
    if scenario != 'default':
        settings[MODEL_KEY] = UNSUPPORTED_MODEL if scenario == 'tampered' else MODEL
    # Глобальная модель и соседний плагин намеренно отличаются: они не являются fallback.
    config = {
        'model': {'default': 'synthetic-main-do-not-use', 'provider': 'synthetic'},
        'plugins': {'entries': {
            'decision-review': {'settings': settings},
            'synthetic-neighbor': {'settings': {MODEL_KEY: UNSUPPORTED_MODEL}},
        }},
    }
    (home / 'config.yaml').write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
    assert hermes_constants.get_hermes_home().resolve() == home.resolve()
    production = Path(os.environ.get('PPLX_TEST_PRODUCTION_ROOT', str(ROOT))).resolve()
    manifest = native.parse_manifest_file(production / 'plugin.yaml', production, 'user', '')
    assert manifest is not None and manifest.name == 'decision-review'
    manager = native.PluginManager(scope_key=str(home))
    ctx = native.PluginContext(manifest, manager)
    assert type(ctx) is native.PluginContext
    assert native.PluginContext.get_config.__module__ == 'hermes_cli.plugins'
    assert native._plugin_relative_segments is native_state._plugin_relative_segments
    assert native_state._PLUGIN_SETTING_RESERVED_ROOTS == frozenset({'model', 'plugins', 'security', 'settings'})
    assert native_state._plugin_relative_segments(MODEL_KEY) == (MODEL_KEY,)
    assert ctx.get_config(MODEL_KEY, MODEL) == settings.get(MODEL_KEY, MODEL)
    # Negative control остаётся отрицательным: core policy не ослаблена ради плагина.
    for forbidden in ('model', 'plugins', 'security', 'settings', 'plugins.entries.synthetic-neighbor.settings.reviewer_model', '../reviewer_model'):
        with pytest.raises(ValueError, match='plugin-relative'):
            ctx.get_config(forbidden, None)
    spec = importlib.util.spec_from_file_location('decision_review_native_config_regression', production / '__init__.py', submodule_search_locations=[str(production)])
    plugin = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, plugin)
    spec.loader.exec_module(plugin)
    record_property('production_source', str(production))
    record_property('production_init_sha256', hashlib.sha256((production / '__init__.py').read_bytes()).hexdigest())
    record_property('native_context_source', str(Path(native.__file__).resolve()))
    record_property('native_validator_source', str(Path(native_state.__file__).resolve()))
    record_property('model_setting_key', MODEL_KEY)
    secret_reads, calls, redactions = [], [], []
    original_get_secret = scope.get_secret
    original_redact = redact.redact_for_egress
    token = scope.set_secret_scope({'OPENROUTER_API_KEY': 'synthetic-owner-key'}, profile_home=str(home))

    def observed_secret(name, default=None):
        secret_reads.append(name)
        return original_get_secret(name, default)

    def observed_redact(text):
        redactions.append(len(text))
        return original_redact(text)

    def reply(request):
        payload = json.loads(request.content)
        calls.append({'method': request.method, 'url': str(request.url), 'model': payload['model']})
        assert request.method == 'POST' and str(request.url) == ENDPOINT
        assert payload['model'] == MODEL
        return httpx.Response(200, json=answer_payload(payload))

    monkeypatch.setattr(scope, 'get_secret', observed_secret)
    monkeypatch.setattr(redact, 'redact_for_egress', observed_redact)
    runtime = plugin.register(ctx)
    runtime.transport = httpx.MockTransport(reply)
    assert set(manager._hooks) == HOOKS
    assert all(manager._hooks[name] == [getattr(runtime, name)] for name in HOOKS)
    assert not manager._plugin_tool_names and not manager._middleware
    record_property('registered_hooks', len(manager._hooks))
    try:
        yield SimpleNamespace(runtime=runtime, ctx=ctx, manifest=manifest, manager=manager,
                              home=home, calls=calls, secret_reads=secret_reads, redactions=redactions,
                              validator=native_state._plugin_relative_segments)
    finally:
        manager.unload(manifest)
        scope.reset_secret_scope(token)
        assert runtime.closed and not manager._hooks


@pytest.mark.parametrize('surface', ['main', 'subagent'])
@pytest.mark.parametrize('scenario', ['valid', 'default', 'tampered', 'cache_change'])
def test_native_scoped_reviewer_model_drives_hook(native_env, surface, scenario, record_property):
    env = native_env
    session_id = 'native-parent'
    original_final = 'Синтетический результат сохранён; контрольная строка возвращена.'
    original_child = {'status': 'completed', 'results': [
        {'task_index': 0, 'status': 'completed', 'summary': original_final,
         'exit_reason': 'completed', 'truncated': False, 'schema_valid': True}],
        'synthetic_metadata': {'unchanged': True}}
    if surface == 'main':
        env.manager._hooks['pre_llm_call'][0](session_id=session_id, turn_id='native-turn', user_message='Проверить возврат синтетической контрольной строки')

        def invoke():
            output = env.manager._hooks['transform_llm_output'][0](session_id=session_id, response_text=original_final)
            assert output.startswith(original_final + '\n\n---\n'), 'Исходный main final изменён'
            state = env.runtime.main_state(session_id)
            scope = env.runtime.capture_scope(session_id, state=state)
            return env.runtime.main_receipt(scope, state.get('main_review_once')), output
    else:
        def invoke():
            output = env.manager._hooks['transform_tool_result'][0](tool_name='delegate_task', session_id=session_id,
                args={'goal': 'Проверить возврат синтетической контрольной строки'}, result=json.dumps(original_child, ensure_ascii=False))
            data = json.loads(output)
            assert {key: data[key] for key in original_child} == original_child, 'Исходный дочерний результат изменён'
            return data['pplx_review'][0], output

    # На неизменённой baseline эта настоящая зарегистрированная callback падает
    # именно в native _plugin_relative_segments('model'), ещё до Decisions и audit.
    review, output = invoke()
    if scenario == 'tampered':
        assert review['verified'] is False and review['verdict'] == 'INSPECT'
        assert not env.secret_reads and not env.calls and not env.redactions
    else:
        assert review['verified'] is True and review['verdict'] == 'ACCEPT'
        assert env.secret_reads == ['OPENROUTER_API_KEY'] and len(env.calls) == 1
        assert env.redactions and env.calls[0]['model'] == MODEL
        assert len(review['answers']) == 7
        if scenario == 'cache_change':
            initial_review = review
            audit_before = (env.home / 'plugin-data/decision-review/reviews.jsonl').read_bytes()
            before = env.runtime.config_tag()
            # Реальная public scoped запись; не monkeypatch reader и не full/global read.
            env.ctx.set_config(MODEL_KEY, UNSUPPORTED_MODEL)
            assert env.ctx.get_config(MODEL_KEY, None) == UNSUPPORTED_MODEL
            assert env.runtime.config_tag() != before
            review, output = invoke()
            assert review['verified'] is False and review['verdict'] == 'INSPECT'
            if surface == 'main':
                assert 'DECISIONS INSPECT' in output and 'DECISIONS ACCEPT' not in output
                assert (env.home / 'plugin-data/decision-review/reviews.jsonl').read_bytes() == audit_before
                assert env.runtime.main_state(session_id)['last_final']['review'] == initial_review
            assert env.secret_reads == ['OPENROUTER_API_KEY'] and len(env.calls) == 1, 'Отказ должен предшествовать scoped key/transport и не использовать старый ACCEPT cache'
    audit_path = env.home / 'plugin-data/decision-review/reviews.jsonl'
    rows = [json.loads(line) for line in audit_path.read_text(encoding='utf-8').splitlines()]
    audited_review = initial_review if scenario == 'cache_change' and surface == 'main' else review
    assert rows[-1]['policy_decision'] == audited_review['verdict']
    assert rows[-1]['verified'] is audited_review['verified']
    assert rows[-1]['target_type'] == ('main' if surface == 'main' else 'subagent')
    assert rows[-1]['requested_model'] == MODEL
    assert original_final not in audit_path.read_text(encoding='utf-8')
    assert env.manifest.version == '0.2.0'
    assert 'model' not in env.manifest.config_schema
    assert env.manifest.config_schema[MODEL_KEY]['default'] == MODEL
    for key in env.manifest.config_schema:
        assert env.validator(key), 'Каждый declared key должен быть native plugin-relative'
    assert env.runtime.setting('max_review_retries', 1) == 1
    record_property('decisions_requests', len(env.calls))
    record_property('audit_rows', len(rows))
    record_property('verified', review['verified'])
    record_property('verdict', review['verdict'])
