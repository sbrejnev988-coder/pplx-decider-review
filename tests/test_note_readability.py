"""Сообщения Decision Review понятны человеку и не превращают оценки в доказанные ошибки."""
import httpx
import json
from importlib import import_module
from conftest import start, answer_payload
from test_main import begin


def test_retry_note_explains_probabilities_without_claiming_proven_defects(env):
    runtime = start(env)
    begin(env, 'Опубликовать проверенный проект на GitHub')
    env.ctx.settings['mode'] = 'advisory'

    def reply(request, payload):
        data = answer_payload(payload, completed=.256, reliable=.981)
        data['answers']['important_requirement_missed']['noul'] = .672
        data['answers']['internal_contradiction']['noul'] = .845
        data['answers']['needs_revision']['noul'] = .761
        return httpx.Response(200, json=data)

    env.replies.append(reply)
    original = 'Проект опубликован; удалённый SHA и CI проверены.'
    try:
        output = runtime.transform_llm_output(session_id='p', response_text=original)
        assert output.startswith(original + '\n\n---\n')
        note = output.split('\n\n---\n', 1)[1]
        assert note.startswith('DECISIONS RETRY: рекомендуется перепроверка.\n')
        assert 'По оценке Decision Review, вероятность:' in note
        for line in (
            '- полного выполнения задачи — 25,6%;',
            '- подтверждённости заявлений — 98,1%;',
            '- пропуска важного требования — 67,2%;',
            '- наличия противоречий — 84,5%;',
            '- необходимости доработки — 76,1%.',
        ):
            assert line in note
        assert 'Sol:' not in note
        assert 'Основной агент:' not in note
        assert 'Это рекомендация проверяющей модели, а не доказательство правильности результата.' in note
        assert 'Вероятностная оценка не заменяет фактическую проверку' in note
        assert 'Ограничение: эта заметка не запускает новый цикл работы.' in note
        assert 'Фактическую проверку и исправления плагин не выполняет.' in note
        assert 'выявленные пробелы' not in note
        assert 'final-transform' not in note
        assert len(env.calls) == 1
    finally:
        runtime.close()


def test_child_retry_recommendation_requires_actual_verification(env):
    runtime = start(env)
    try:
        env.replies.append(lambda request, payload: httpx.Response(
            200, json=answer_payload(payload, completed=.4, reliable=.9, adverse=.7)))
        original = {'results': [{'status': 'completed', 'summary': 'Проверка закончена; доказательства в исходном результате.'}]}
        output = runtime.transform_tool_result(
            tool_name='delegate_task', args={'goal': 'Проверить изменения проекта'},
            result=json.dumps(original, ensure_ascii=False), session_id='p')
        transformed = json.loads(output)
        review = transformed['pplx_review'][0]
        assert transformed['results'] == original['results']
        assert review['verdict'] == 'RETRY'
        assert 'Если подтвердятся недочёты, исправь их.' in review['recommendation']
        assert review['recommendation'].startswith('Основной агент:') and 'Sol' not in review['recommendation']
        assert 'не более одного раза' in review['recommendation']
        assert 'автоматически запускать субагента нельзя' in review['recommendation']
        assert len(env.calls) == 1
    finally:
        runtime.close()


def test_unavailable_note_has_no_invented_probabilities(env):
    runtime = start(env)
    try:
        review = import_module(env.p.__name__ + '.protocol').unavailable('Истёк срок ожидания reviewer.')
        before = json.loads(json.dumps(review))
        note = runtime.review_note(review)
        assert note.startswith('DECISIONS INSPECT: оценку получить не удалось; заключение Decision Review отсутствует.\n')
        assert 'проверка не подтверждена' not in note
        assert 'Истёк срок ожидания reviewer.' in note
        assert '%' not in note and review == before
        assert not env.calls
    finally:
        runtime.close()
