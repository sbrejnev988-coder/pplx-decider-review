"""Локальные регрессии Decisions; native core выбирается publication_env."""
import importlib
import json

import httpx
import pytest
from conftest import MODEL, answer_payload
from test_native_config import native_env  # существующая неизменённая native fixture


@pytest.fixture
def reviewed(publication_env):
    env = publication_env
    env.ctx.set_config('timeout_seconds', 2)
    env.ctx.set_config('callback_budget_seconds', 4)
    assert env.ctx.get_config('timeout_seconds') == 2
    return env


def transport_module(env):
    return importlib.import_module(type(env.runtime).__module__ + '.transport')


def invoke(env, mutate, main=False):
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(request)
        data = answer_payload(payload)
        mutate(data, main)
        return httpx.Response(200, json=data)
    env.runtime.transport = httpx.MockTransport(handler)
    review = env.runtime.review('Проверить свидетельства результата',
                                {'status': 'completed', 'summary': 'Только синтетические данные.'}, main=main)
    assert len(calls) == 1, 'Inference не повторяется'
    assert not env.runtime.inflight.locked()
    assert env.redactions if hasattr(env, 'redactions') else env.redacted
    return review


@pytest.mark.parametrize('scenario', ['valid'])
@pytest.mark.parametrize('main', [False, True], ids=['child', 'main'])
@pytest.mark.parametrize('contradiction', ['choice', 'score'])
def test_contradictory_typed_answer_is_unverified_inspect(reviewed, main, contradiction):
    def mutate(data, main):
        answer = data['answers']['next_action' if contradiction == 'choice' else
                                  ('overall_quality' if main else 'quality')]
        if contradiction == 'choice':
            answer['probabilities'] = {'принять': .05, 'проверить': .9, 'повторить': .05}
        else:
            answer['score'] = 3.5  # распределение исходной fixture имеет среднее 3.8
    review = invoke(reviewed, mutate, main)
    assert review['verdict'] == 'INSPECT'
    assert review['verified'] is False
    assert not review['answers'] and review['errors']


@pytest.mark.parametrize('scenario', ['valid'])
@pytest.mark.parametrize('main,case,want', [(False, 'fractional', 'INSPECT'),
                                           (True, 'tie', 'ACCEPT'),
                                           (False, 'rounded', 'ACCEPT')])
def test_fractional_ties_independent_confidence_and_local_rounding(reviewed, main, case, want):
    def mutate(data, main):
        data['model'] = MODEL + '-20261001'
        data.pop('id')
        quality = data['answers']['overall_quality' if main else 'quality']
        if case == 'fractional':
            score = 3.5010741098620537
            quality.update(score=score, confidence=.7505370549310268,
                           probabilities={'0': 0, '1': 0, '2': 0, '3': 4-score, '4': score-3})
        elif case == 'tie':
            data['answers']['next_action'].update(confidence=.9,
                probabilities={'принять': .5, 'проверить': .5, 'повторить': 0})
        else:
            quality['score'] = 3.804  # .001 + .001 * (5-1): локальный допуск округления
    review = invoke(reviewed, mutate, main)
    assert review['verified'] is True and review['verdict'] == want
    assert review['model'] == MODEL + '-20261001' and review['request_id'] is None
    assert review['answers']['next_action']['confidence'] == .9


@pytest.mark.parametrize('scenario', ['valid'])
def test_score_beyond_local_rounding_is_refused(reviewed):
    def mutate(data, main):
        data['answers']['quality']['score'] = 3.806
    review = invoke(reviewed, mutate)
    assert review['verdict'] == 'INSPECT' and review['verified'] is False


@pytest.mark.parametrize('scenario', ['valid'])
@pytest.mark.parametrize('returned_model,verified', [
    (MODEL, True), (MODEL + '-20261001', True),
    (MODEL + '-٢٠٢٦١٠٠١', False), (MODEL + '-２０２６１００１', False),
    (' ' + MODEL, False),
])
def test_model_suffix_requires_literal_ascii(reviewed, returned_model, verified):
    def mutate(data, main):
        data['model'] = returned_model
        # Недокументированные extra-поля не переопределяют Decisions contract.
        data.update(code='unrelated-extra', error_code='unrelated-extra')
    review = invoke(reviewed, mutate)
    assert review['verified'] is verified
    assert review['verdict'] == ('ACCEPT' if verified else 'INSPECT')
    if verified:
        assert review['model'] == returned_model
        assert 'code' not in review and 'error_code' not in review


class CountedDict(dict):
    def __init__(self, values, counter):
        super().__init__(values)
        self.counter = counter

    def items(self):
        for item in super().items():
            self.counter[0] += 1
            yield item


def projection_nodes(value):
    if isinstance(value, dict):
        return 1 + sum(1 + projection_nodes(v) for v in value.values())
    if isinstance(value, list):
        return 1 + sum(projection_nodes(v) for v in value)
    return 1


@pytest.mark.parametrize('scenario', ['valid'])
@pytest.mark.parametrize('shape', ['wide', 'branching', 'utf8'])
def test_projection_is_bounded_before_serialization(reviewed, shape, record_property):
    module = transport_module(reviewed)
    counter = [0]
    if shape == 'wide':
        evidence = CountedDict({f'field{i}': 'данные' for i in range(100)}, counter)
    elif shape == 'branching':
        evidence = 'данные'
        for level in range(5):
            evidence = CountedDict({f'field{i}': evidence for i in range(4)}, counter)
    else:
        evidence = CountedDict({f'field{i}': '界' * 6000 for i in range(32)}, counter)
    projected = module.scrub(evidence)
    serialized_bytes = len(json.dumps(projected, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    nodes = projection_nodes(projected)
    record_property('projection_items_visited', counter[0])
    record_property('projection_nodes', nodes)
    record_property('projection_serialized_utf8_bytes', serialized_bytes)
    assert counter[0] <= (32 if shape == 'wide' else 256)
    assert nodes <= 256
    assert serialized_bytes <= 16000
    if shape == 'wide':
        assert len(projected) == 32


@pytest.mark.parametrize('scenario', ['valid'])
def test_state_is_bounded_before_native_redaction(reviewed, monkeypatch, record_property):
    module = transport_module(reviewed)
    original_dumps = json.dumps
    measured = []
    def measured_dumps(value, **kwargs):
        result = original_dumps(value, **kwargs)
        if isinstance(value, dict) and 'evidence' in value and 'limitations' in value:
            measured.append(len(result.encode('utf-8')))
        return result
    monkeypatch.setattr(module.json, 'dumps', measured_dumps)
    evidence = {f'field{i}': '界' * 6000 for i in range(32)}
    state = module.safe_state('Ю' * 2000, evidence)
    record_property('full_state_serializations_utf8', json.dumps(measured))
    assert measured and max(measured) <= 16000
    assert len(original_dumps(state, ensure_ascii=False).encode('utf-8')) <= 20000
    assert reviewed.redactions if hasattr(reviewed, 'redactions') else reviewed.redacted


@pytest.mark.parametrize('scenario', ['valid'])
def test_unsupported_keys_never_invoke_custom_string(reviewed):
    rendered = []
    class UntrustedKey:
        def __str__(self):
            rendered.append(True)
            return 'arbitrary-object-key'
    evidence = {'summary': 'данные', UntrustedKey(): 'не передавать'}
    projected = transport_module(reviewed).scrub(evidence)
    assert not rendered
    assert projected == {'summary': 'данные'}


@pytest.mark.parametrize('scenario', ['valid'])
@pytest.mark.parametrize('original,expected', [
    ('до\ud800после', 'до\ufffdпосле'), ('до\udfffпосле', 'до\ufffdпосле'),
    ('до\ud83d\ude00после', 'до😀после'), ('е́界😀', 'е́界😀'),
], ids=['lone-high', 'lone-low', 'valid-pair', 'genuine-unicode'])
def test_surrogate_safe_wire_copy_preserves_original(reviewed, original, expected):
    evidence = {'summary': original, 'nested': {original: 'данные'}}
    before = json.dumps(evidence, ensure_ascii=True)
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json=answer_payload(payload))
    reviewed.runtime.transport = httpx.MockTransport(handler)
    review = reviewed.runtime.review('Цель ' + original, evidence)
    assert review['verified'] is True and len(requests) == 1
    assert requests[0]['state']['evidence']['summary'] == expected
    assert requests[0]['state']['goal'] == 'Цель ' + expected
    assert requests[0]['state']['evidence']['nested'] == {expected: 'данные'}
    assert json.dumps(evidence, ensure_ascii=True) == before
    assert evidence['summary'] == original
    assert reviewed.redactions if hasattr(reviewed, 'redactions') else reviewed.redacted


@pytest.mark.parametrize('scenario', ['valid'])
def test_real_bearer_password_api_key_input_is_redacted_control(reviewed):
    markers = ['syntheticBearer9876543210', 'syntheticPassword9876543210', 'syntheticApiKey9876543210']
    evidence = {'task_index': 0, 'status': 'completed', 'summary':
                'Authorization: Bearer ' + markers[0] + '\npassword=' + markers[1] + '\napi_key=' + markers[2]}
    original = {'results': [evidence]}
    encoded = json.dumps(original, ensure_ascii=False)
    assert all(marker in encoded for marker in markers), 'Oracle обязан содержать sentinel'
    assert 'Bearer ' + markers[0] in evidence['summary']
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=answer_payload(json.loads(request.content)))
    reviewed.runtime.transport = httpx.MockTransport(handler)
    output = reviewed.runtime.transform_tool_result(tool_name='delegate_task',
        args={'goal': 'Проверить неизменность исходного результата'}, result=encoded, session_id='control-parent')
    data = json.loads(output)
    assert data['results'] == original['results']
    assert json.dumps(original, ensure_ascii=False) == encoded
    assert len(requests) == 1 and data['pplx_review'][0]['verified'] is True
    assert reviewed.redactions if hasattr(reviewed, 'redactions') else reviewed.redacted
    assert all(marker not in requests[0].content.decode('utf-8') for marker in markers)


@pytest.mark.parametrize('scenario', ['valid'])
@pytest.mark.parametrize('phase', ['before', 'after'])
def test_deadline_checks_surround_safe_state_without_send(reviewed, monkeypatch, phase):
    from types import SimpleNamespace
    module = transport_module(reviewed)
    plugin = importlib.import_module(type(reviewed.runtime).__module__)
    now = [100.0]
    prepared, sent = [], []
    original_safe_state = module.safe_state
    def prepare(goal, evidence):
        prepared.append(True)
        state = original_safe_state(goal, evidence)
        now[0] = 102.0
        return state
    def handler(request):
        sent.append(True)
        return httpx.Response(200, json=answer_payload(json.loads(request.content)))
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(module, 'safe_state', prepare)
    review = module.request_once('Проверить свидетельства', {'summary': 'данные'}, plugin.questions(),
        'synthetic-owner-key', httpx.MockTransport(handler), 2, False, .8, .65,
        deadline=100.0 if phase == 'before' else 102.0)
    assert review['verdict'] == 'INSPECT' and review['verified'] is False
    assert len(prepared) == (phase == 'after') and not sent


@pytest.mark.parametrize('scenario', ['valid'])
def test_native_preparation_retains_single_worker_slot(reviewed, monkeypatch):
    import threading
    module = transport_module(reviewed)
    entered, release = threading.Event(), threading.Event()
    original_safe_state = module.safe_state
    requests, results = [], []
    def prepare(goal, evidence):
        entered.set()
        assert release.wait(3), 'Fixture должна освободить собственный worker'
        return original_safe_state(goal, evidence)
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=answer_payload(json.loads(request.content)))
    monkeypatch.setattr(module, 'safe_state', prepare)
    reviewed.runtime.transport = httpx.MockTransport(handler)
    import contextvars
    owner = contextvars.copy_context()
    caller = threading.Thread(target=lambda: owner.run(lambda: results.append(reviewed.runtime.review(
        'Проверить первый результат', {'summary': 'данные'}))))
    caller.start()
    try:
        assert entered.wait(2)
        assert reviewed.runtime.inflight.locked()
        second = reviewed.runtime.review('Проверить второй результат', {'summary': 'данные'})
        assert second['verified'] is False and not requests
    finally:
        release.set()
        caller.join(3)
    assert not caller.is_alive() and not reviewed.runtime.inflight.locked()
    assert len(results) == 1 and results[0]['verified'] is True and len(requests) == 1
