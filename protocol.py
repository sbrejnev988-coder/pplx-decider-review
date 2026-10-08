"""Строгая валидация Decisions и детерминированная политика. Без I/O."""
import math
import re

MODEL = 'openai/gpt-6-luna-decisions'
ENDPOINT = 'https://openrouter.ai/api/alpha/decisions'
MAX_MODEL_LENGTH = 128
_MODEL_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*')


def valid_model_identifier(value):
    # Literal provider/model, без нормализации, URL-синтаксиса и сетевого каталога.
    return (isinstance(value, str) and len(value) <= MAX_MODEL_LENGTH
            and _MODEL_IDENTIFIER.fullmatch(value) is not None)


def number(value, low=0, high=1):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Некорректное число в ответе Decisions.')
    return value


def validate(data, questions, expected_model=MODEL):
    if not valid_model_identifier(expected_model):
        raise ValueError('Некорректный идентификатор запрошенной модели Decisions.')
    if not isinstance(data, dict) or 'error' in data:
        raise ValueError('Некорректный ответ Decisions.')
    model = data.get('model')
    if not isinstance(model, str) or (model != expected_model and re.fullmatch(re.escape(expected_model) + r'-[0-9]{8}', model) is None):
        raise ValueError('Фактическая модель не соответствует запрошенной.')
    answers = data.get('answers')
    if not isinstance(answers, dict):
        raise ValueError('Отсутствуют типизированные ответы.')
    clean = {}
    for key, q in questions.items():
        a = answers.get(key)
        if not isinstance(a, dict) or a.get('type') != q['type']:
            raise ValueError('Неполный либо несовместимый тип ответа.')
        typ = q['type']
        if typ == 'noul':
            clean[key] = {'type': typ, 'noul': number(a.get('noul'))}
            continue
        confidence = number(a.get('confidence'))
        options = set(q['criteria']) if typ == 'choice' else {str(i) for i in range(len(q['criteria']))}
        probs = a.get('probabilities')
        if not isinstance(probs, dict) or set(probs) != options:
            raise ValueError('Неполное распределение вероятностей.')
        probabilities = {o: number(probs[o]) for o in options}
        if abs(math.fsum(probabilities.values()) - 1) > .001:
            raise ValueError('Неверная сумма вероятностей.')
        if typ == 'choice':
            value = a.get('choice')
            if not isinstance(value, str) or value not in options:
                raise ValueError('Неизвестный вариант решения.')
            if probabilities[value] < max(probabilities.values()):
                raise ValueError('Выбранный вариант не является победителем распределения.')
        else:
            value = number(a.get('score'), 0, len(options) - 1)
            expected = math.fsum(int(o) * p for o, p in probabilities.items())
            # Локальная политика округления, не гарантия точности провайдера.
            tolerance = .001 + .001 * (len(options) - 1)
            if abs(value - expected) > tolerance:
                raise ValueError('Score противоречит среднему распределения.')
        clean[key] = {'type': typ, typ: value, 'confidence': confidence, 'probabilities': probabilities}
    usage = data.get('usage')
    if not isinstance(usage, dict):
        raise ValueError('Отсутствует usage.')
    kept_usage = {}
    for key in ('input_tokens', 'output_tokens'):
        value = usage.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError('Некорректный usage.')
        kept_usage[key] = value
    if 'cost' in usage:
        kept_usage['cost'] = number(usage['cost'], 0, 1e12)
    # Непроверенные дополнительные поля провайдера не передаются в журнал/контекст.
    identifier = data.get('id')
    provider = data.get('provider')
    # Optional string: отсутствие допустимо, но тип и локальный cap проверяются явно.
    if 'provider' in data and (not isinstance(provider, str) or len(provider) > 128):
        raise ValueError('Некорректный provider либо превышен лимит metadata Decisions.')
    return {'model': model, 'request_id': identifier if isinstance(identifier, str) and len(identifier) <= 128 else None,
            'provider': provider,
            'usage': kept_usage, 'answers': clean}


def policy(answers, main=False, accept=.8, retry=.65):
    completed = answers['task_satisfied' if main else 'goal_completed']['noul']
    reliable = answers['claims_supported' if main else 'result_reliable']['noul']
    adverse_keys = ('important_requirement_missed', 'internal_contradiction', 'needs_revision') if main else ('unsupported_success_claim', 'important_requirement_missed', 'contradictions_present')
    adverse = max(answers[k]['noul'] for k in adverse_keys)
    if completed < retry or reliable < retry or adverse >= retry:
        verdict = 'RETRY'
    elif completed >= accept and reliable >= accept and adverse < .35:
        verdict = 'ACCEPT'
    else:
        verdict = 'INSPECT'
    # Choice и confidence могут только убрать ACCEPT, никогда не отменить противоречащие метрики.
    quality = answers['overall_quality' if main else 'quality']
    if verdict == 'ACCEPT' and (answers['next_action']['choice'] != 'принять' or answers['next_action']['confidence'] < accept or quality['confidence'] < accept or quality['score'] < 3):
        verdict = 'INSPECT'
    return verdict


def unavailable(reason, requested_model=MODEL):
    # Невалидная настройка не превращается в fallback и не выводится как URL/секрет.
    requested_model = requested_model if valid_model_identifier(requested_model) else None
    return {'verdict': 'INSPECT', 'verified': False, 'requested_model': requested_model, 'model': None,
            'request_id': None, 'provider': None, 'usage': None, 'answers': {}, 'errors': [reason],
            'reason': 'Decision Review недоступен или неполон; результат не подтверждён reviewer.'}
