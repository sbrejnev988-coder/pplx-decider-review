"""Основной агент: один native continue только с tracked edits; финал advisory."""
from .state import meaningful, fingerprint, bounded_put
from .transport import scrub_text
import json
import re


class MainReview:
    def record_tool_evidence(self, session_id, name, args, result):
        allowed = {'terminal', 'execute_code', 'write_file', 'patch', 'search_files', 'read_file', 'web_search', 'web_extract', 'delegate_task'}
        if name not in allowed or not isinstance(result, str) or not self.active():
            return
        state = self.main_state(session_id)
        if state is None:
            return
        arguments = json.dumps(args or {}, ensure_ascii=False, default=str)
        # Чтение credential-файлов никогда не становится evidence для внешнего reviewer.
        if len(arguments) > 50000 or re.search(r'(?i)(\.env\b|auth\.json|password|passwd|secret|api.?key|authorization|credential|token)', arguments):
            return
        sample = scrub_text(result)[:700]
        with self.store.lock:
            if self.closed:
                return
            state['tool_evidence'] = (state.get('tool_evidence', []) + [{'tool_name': name, 'result_excerpt': sample}])[-8:]

    def main_state(self, session_id):
        with self.store.lock:
            if not self.active():
                return None
            key, state = self.store.session(session_id)
            if state is None or state['parent'] or state.get('trivial') or not meaningful(state['goal']):
                return None
            if any(k[0] == key[0] and key[1] in s['children'] for k, s in self.store.sessions.items()):
                return None
            return state

    def final_review(self, session_id, state, response, paths=None):
        candidate = fingerprint([state['goal'], scrub_text(response), state.get('tool_evidence', []), self.config_tag()])
        with self.store.lock:
            if self.closed:
                from .protocol import unavailable
                return unavailable('Плагин выгружен; поздний ответ не применяется.')
            turn = state.get('turn', '')
            previous = state.get('last_final')
            ttl = self.setting('cache_ttl_seconds', 300)
            ttl = ttl if isinstance(ttl, (int, float)) and not isinstance(ttl, bool) and .02 <= ttl <= 1800 else 300
            if (previous and previous['candidate'] == candidate and previous['turn'] == turn
                    and self.store.clock() - previous['time'] < ttl and (paths is None or paths == previous['paths'])):
                return previous['review']
        evidence = {'final_response': response, 'changed_paths': paths or [], 'tool_trace': state.get('tool_evidence', [])}
        review = self.cached_review(session_id, state['goal'], evidence, main=True, deadline=self.deadline(), identity=turn)
        with self.store.lock:
            if self.closed:
                from .protocol import unavailable
                return unavailable('Плагин выгружен; поздний ответ не применяется.')
            state['last_final'] = {'candidate': candidate, 'review': review, 'paths': paths or [],
                                    'time': self.store.clock(), 'turn': turn}
            return review

    def pre_verify(self, session_id='', changed_paths=None, final_response='', attempt=0, **kwargs):
        if self.setting('mode', 'bounded') != 'bounded' or type(attempt) is not int or attempt >= 1 or attempt < 0:
            return None
        if not isinstance(changed_paths, list) or not any(isinstance(p, str) and p.strip() for p in changed_paths):
            return None
        state = self.main_state(session_id)
        if state is None or not isinstance(final_response, str) or not final_response.strip():
            return None
        task = fingerprint(state['goal'])
        with self.store.lock:
            if state['nudges'].get(task, 0) >= 1:
                return None
        review = self.final_review(session_id, state, final_response, changed_paths[:32])
        if not review['verified'] or review['verdict'] == 'ACCEPT':
            return None
        with self.store.lock:
            if self.closed or state['nudges'].get(task, 0) >= 1:
                return None
            bounded_put(state['nudges'], task, 1)
            return {'action': 'continue', 'message': self.review_note(review) + '\nSol: проверь tracked edits и реальные тесты; устрани неподтверждённые заявления. Это единственное продолжение от плагина по этой цели.'}

    def transform_llm_output(self, session_id='', response_text='', **kwargs):
        state = self.main_state(session_id)
        if state is None or not isinstance(response_text, str) or not response_text.strip():
            return None
        if response_text.strip().casefold() in {'пожалуйста.', 'спасибо!', 'понятно.', 'ок', 'хорошо.'}:
            return None
        review = self.final_review(session_id, state, response_text)
        note = self.review_note(review)
        if not review['verified'] or review['verdict'] != 'ACCEPT':
            note += '\nОграничение: общий final-transform не возобновляет агентный цикл; фактическая проверка/доработка не выполнена этим плагином.'
        with self.store.lock:
            return None if self.closed else response_text + '\n\n---\n' + note
