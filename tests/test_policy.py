import copy
import json
import pytest
from conftest import start, answer_payload, MODEL


def child_review(env, reply=None):
    start(env)
    if reply is not None:
        env.replies.append(reply)
    native = {'results': [{'task_index': 0, 'status': 'completed', 'summary': 'pytest: 2 passed.'}]}
    output = env.ctx.hooks['transform_tool_result'](tool_name='delegate_task', args={'goal': 'Проверить реализацию тестами'},
                                                   result=json.dumps(native), session_id='parent')
    return json.loads(output)['pplx_review'][0]

@pytest.mark.parametrize('completed,reliable,adverse,choice,want', [
    (.8, .8, .349, 'принять', 'ACCEPT'),
    (.799, .9, .1, 'принять', 'INSPECT'),
    (.9, .799, .1, 'принять', 'INSPECT'),
    (.9, .9, .35, 'принять', 'INSPECT'),
    (.65, .9, .1, 'принять', 'INSPECT'),
    (.649, .99, .01, 'принять', 'RETRY'),
    (.99, .649, .01, 'принять', 'RETRY'),
    (.99, .99, .65, 'принять', 'RETRY'),
    (.99, .99, .64, 'принять', 'INSPECT'),
    (.99, .99, .01, 'проверить', 'INSPECT'),
    (.99, .99, .01, 'повторить', 'INSPECT'),
])
def test_metrics_control_verdict_not_optimistic_choice(env, completed, reliable, adverse, choice, want):
    def reply(req, payload):
        import httpx
        return httpx.Response(200, json=answer_payload(payload, completed, reliable, adverse, choice))
    result = child_review(env, reply)
    assert result['verdict'] == want
    assert result['verified']

@pytest.mark.parametrize('mutation', [
    'model_missing', 'model_other', 'model_prefix', 'model_date_suffix', 'answers_missing', 'question_missing',
    'noul_bool', 'noul_nan', 'noul_range', 'wrong_type', 'choice_missing_confidence', 'choice_unknown',
    'choice_partial_distribution', 'choice_bad_sum', 'choice_infinite', 'score_range', 'score_nan',
    'score_confidence_missing', 'score_partial_distribution', 'score_bad_sum', 'usage_missing', 'usage_invalid', 'error_envelope',
])
def test_malformed_contract_is_unverified_inspect(env, mutation):
    def reply(req, payload):
        import httpx
        d = answer_payload(payload); a = d['answers']
        if mutation == 'model_missing': d.pop('model')
        elif mutation == 'model_other': d['model'] = 'typesafe/jev-1.13'
        elif mutation == 'model_prefix': d['model'] = MODEL + '-something'
        elif mutation == 'model_date_suffix': d['model'] = MODEL + '-20261001-extra'
        elif mutation == 'answers_missing': d.pop('answers')
        elif mutation == 'question_missing': a.pop('result_reliable')
        elif mutation == 'noul_bool': a['goal_completed']['noul'] = True
        elif mutation == 'noul_nan': a['goal_completed']['noul'] = 'NaN'
        elif mutation == 'noul_range': a['goal_completed']['noul'] = 1.01
        elif mutation == 'wrong_type': a['goal_completed']['type'] = 'choice'
        elif mutation == 'choice_missing_confidence': a['next_action'].pop('confidence')
        elif mutation == 'choice_unknown': a['next_action']['choice'] = 'разрешить'
        elif mutation == 'choice_partial_distribution': a['next_action']['probabilities'].pop('повторить')
        elif mutation == 'choice_bad_sum': a['next_action']['probabilities']['принять'] = .5
        elif mutation == 'choice_infinite': a['next_action']['confidence'] = 'Infinity'
        elif mutation == 'score_range': a['quality']['score'] = 4.01
        elif mutation == 'score_nan': a['quality']['score'] = 'NaN'
        elif mutation == 'score_confidence_missing': a['quality'].pop('confidence')
        elif mutation == 'score_partial_distribution': a['quality']['probabilities'].pop('4')
        elif mutation == 'score_bad_sum': a['quality']['probabilities']['4'] = .5
        elif mutation == 'usage_missing': d.pop('usage')
        elif mutation == 'usage_invalid': d['usage']['input_tokens'] = -1
        elif mutation == 'error_envelope': d['error'] = {'message': 'synthetic-SECRET'}
        return httpx.Response(200, json=d)
    review = child_review(env, reply)
    assert review['verdict'] == 'INSPECT'
    assert review['verified'] is False
    assert review['errors']
    assert 'synthetic-SECRET' not in json.dumps(review)


def test_documented_snapshot_fractional_score_and_absent_id(env):
    def reply(req, payload):
        import httpx
        d = answer_payload(payload); d['model'] = MODEL + '-20261001'; d.pop('id')
        d['answers']['quality']['score'] = 3.5010741098620537
        d['answers']['quality']['confidence'] = .7505370549310268
        return httpx.Response(200, json=d)
    review = child_review(env, reply)
    assert review['model'] == MODEL + '-20261001'
    assert review['request_id'] is None
    assert review['verdict'] == 'INSPECT'
    assert review['verified']


def test_low_choice_confidence_downgrades(env):
    def reply(req, payload):
        import httpx
        d = answer_payload(payload); d['answers']['next_action']['confidence'] = .4
        return httpx.Response(200, json=d)
    assert child_review(env, reply)['verdict'] == 'INSPECT'
