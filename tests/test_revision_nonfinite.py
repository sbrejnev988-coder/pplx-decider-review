"""Refuse non-finite tool JSON before optional review; keep native original."""
import json
import httpx
import pytest
from conftest import answer_payload
from test_native_config import native_env

pytestmark = pytest.mark.parametrize('scenario', ['valid'])
GOAL = 'Проверить синтетический результат по свидетельствам'


def prepare(publication_env):
    runtime = publication_env.runtime
    runtime.ctx.set_config('callback_budget_seconds', 2)
    runtime.ctx.set_config('timeout_seconds', 2)
    runtime.ctx.set_config('log_enabled', False)
    calls = []
    def reply(request):
        calls.append(1)
        return httpx.Response(200, json=answer_payload(json.loads(request.content)))
    runtime.transport = httpx.MockTransport(reply)
    return runtime, calls


@pytest.mark.parametrize('literal', ['NaN', 'Infinity', '-Infinity', '1e999', '-1e999'])
def test_nonfinite_tool_json_refuses_without_http_or_native_mutation(publication_env, literal):
    runtime, calls = prepare(publication_env)
    raw = ('{"results":[{"status":"completed","summary":"Проверено.",'
           '"error":{"detail":' + literal + '},"task_index":0}]}')
    original = raw
    try:
        transformed = runtime.transform_tool_result(tool_name='delegate_task', session_id='p',
                                                     args={'goal': GOAL}, result=raw)
    except Exception as exc:
        pytest.fail('Optional callback raised ' + type(exc).__name__)
    assert transformed is None, 'Malformed tool JSON must keep the native original'
    assert raw == original and literal in raw
    assert calls == [] and not runtime.inflight.locked()


def test_finite_tool_json_and_literal_nan_text_still_work(publication_env):
    runtime, calls = prepare(publication_env)
    row = {'status':'completed', 'summary':'Строка NaN, не числовая константа.',
           'duration_ms': 3.5, 'task_index':0}
    raw = json.dumps({'results':[row]}, ensure_ascii=False, allow_nan=False)
    transformed = runtime.transform_tool_result(tool_name='delegate_task', session_id='p',
                                                args={'goal':GOAL}, result=raw)
    output = json.loads(transformed)
    assert output['results'] == [row]
    assert output['pplx_review'][0]['verified'] and output['pplx_review'][0]['verdict']=='ACCEPT'
    assert len(calls)==1 and not runtime.inflight.locked()


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_python_evidence_projects_only_to_safe_marker(publication_env, value):
    runtime, _ = prepare(publication_env)
    from importlib import import_module
    transport = import_module(runtime.__class__.__module__ + '.transport')
    source = {'duration': value, 'finite': 1.5, 'label': 'NaN'}
    projected = transport.scrub(source)
    assert projected == {'duration': '[НЕПОДДЕРЖИВАЕМЫЕ ДАННЫЕ]', 'finite': 1.5, 'label': 'NaN'}
    json.dumps(projected, allow_nan=False)
    assert source['duration'] is value and source['finite'] == 1.5


def test_nonfinite_native_child_trace_cannot_turn_error_into_accept(publication_env):
    runtime, calls = prepare(publication_env)
    runtime.pre_llm_call(session_id='p', user_message=GOAL)
    raw_error = {'detail': float('nan')}
    runtime.subagent_start(parent_session_id='p', child_session_id='c',
                           child_subagent_id='s', child_goal=GOAL)
    dispatch = {'status': 'dispatched', 'delegation_id': 'd', 'subagent_ids': ['s']}
    assert runtime.transform_tool_result(tool_name='delegate_task', session_id='p',
                                         args={'goal': GOAL}, result=json.dumps(dispatch)) is None
    runtime.subagent_stop(parent_session_id='p', child_session_id='c',
                          child_status='error', child_summary='Не удалось проверить.',
                          tool_call_history=[{'tool_name': 'synthetic', 'result': raw_error}])
    message = '[ASYNC DELEGATION BATCH COMPLETE — d]\nЗавершено'
    row = {'role': 'user', 'content': message, 'display_kind': 'async_delegation_complete',
           'display_metadata': {'delegation_id': 'd'}}
    injected = runtime.pre_llm_call(session_id='p', user_message=message,
                                    conversation_history=[row], turn_id='completion')
    assert 'PPLX INSPECT' in injected['context'] and 'PPLX ACCEPT' not in injected['context']
    assert len(calls) == 1 and raw_error['detail'] != raw_error['detail']
    assert not runtime.inflight.locked()
