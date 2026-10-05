"""Основной агент: один native continue только с tracked edits; финал advisory."""
from .state import meaningful, fingerprint, bounded_put
from .transport import scrub_text, scrub
import json
import re


class MainReview:
    def record_tool_evidence(self, session_id, name, args, result):
        allowed = {'terminal', 'execute_code', 'write_file', 'patch', 'search_files', 'read_file', 'web_search', 'web_extract', 'delegate_task'}
        if not isinstance(name, str) or name not in allowed or not isinstance(result, str) or not self.active():
            return
        with self.store.lock:
            state = self.main_state(session_id)
            scope = self.capture_scope(session_id, state=state) if state is not None else None
            if scope is None:
                return
        # Conservative bounded credential admission before scrub removes field names.
        remaining = [128]
        pattern = r'(?i)(\.env\b|auth\.json|password|passwd|secret|api.?key|authorization|credential|token)'
        def safe_args(value, depth=0):
            remaining[0] -= 1
            if remaining[0] < 0 or depth > 5:
                return False
            if isinstance(value, str):
                return len(value) <= 6000 and not re.search(pattern, value)
            if isinstance(value, dict):
                return len(value) <= 32 and all(isinstance(k, str) and len(k) <= 80
                    and not re.search(pattern, k) and safe_args(v, depth + 1) for k, v in value.items())
            if isinstance(value, (list, tuple)):
                return len(value) <= 32 and all(safe_args(v, depth + 1) for v in value)
            return value is None or type(value) in (bool, int, float)
        try:
            if not safe_args(args or {}):
                return
            arguments = json.dumps(scrub(args or {}), ensure_ascii=True, allow_nan=False)
        except Exception:
            return
        # Чтение credential-файлов никогда не становится evidence для внешнего reviewer.
        if len(arguments) > 50000 or re.search(r'(?i)(\.env\b|auth\.json|password|passwd|secret|api.?key|authorization|credential|token)', arguments):
            return
        sample = scrub_text(result)[:700]
        with self.store.lock:
            if not self.scope_current(scope):
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

    def final_review(self, session_id, state, response, paths=None, expected_scope=None):
        from .protocol import unavailable
        with self.store.lock:
            scope = expected_scope if expected_scope is not None else self.capture_scope(session_id, state=state)
            if not self.scope_current(scope) or scope[1] is not state:
                return unavailable('Изменилась сессия, задача либо конфигурация reviewer.')
            goal = state['goal']
            trace = list(state.get('tool_evidence', []))
            candidate = fingerprint([goal, scrub_text(response), trace, scope[7].tag])
            turn = state.get('turn', '')
            previous = state.get('last_final')
            ttl = scope[7].cache_ttl
            if (previous and previous['candidate'] == candidate and previous['turn'] == turn
                    and self.store.clock() - previous['time'] < ttl and (paths is None or paths == previous['paths'])):
                return json.loads(json.dumps(previous['review']))
        evidence = {'final_response': response, 'changed_paths': paths or [], 'tool_trace': trace}
        review = self.cached_review(session_id, goal, evidence, main=True, deadline=self.deadline(), identity=turn, expected_scope=scope)
        with self.store.lock:
            if not self.scope_current(scope):
                return unavailable('Изменилась сессия, задача либо конфигурация reviewer.')
            state['last_final'] = {'candidate': candidate, 'review': json.loads(json.dumps(review)), 'paths': list(paths or []),
                                    'time': self.store.clock(), 'turn': turn}
            return review

    def pre_verify(self, session_id='', changed_paths=None, final_response='', attempt=0, **kwargs):
        if self.setting('mode', 'bounded') != 'bounded' or type(attempt) is not int or attempt >= 1 or attempt < 0:
            return None
        if not isinstance(changed_paths, list) or not any(isinstance(p, str) and p.strip() for p in changed_paths):
            return None
        with self.store.lock:
            state = self.main_state(session_id)
            scope = self.capture_scope(session_id, state=state) if state is not None else None
            if scope is None or not isinstance(final_response, str) or not final_response.strip():
                return None
            task = fingerprint(state['goal'])
            if state['nudges'].get(task, 0) >= 1:
                return None
        review = self.final_review(session_id, state, final_response, changed_paths[:32], expected_scope=scope)
        if not review['verified'] or review['verdict'] == 'ACCEPT':
            return None
        with self.store.lock:
            if not self.scope_current(scope) or state['nudges'].get(task, 0) >= 1:
                return None
            bounded_put(state['nudges'], task, 1)
            return {'action': 'continue', 'message': self.review_note(review) + '\nSol: проверь tracked edits и реальные тесты; устрани неподтверждённые заявления. Это единственное продолжение от плагина по этой цели.'}

    def transform_llm_output(self, session_id='', response_text='', **kwargs):
        with self.store.lock:
            state = self.main_state(session_id)
            scope = self.capture_scope(session_id, state=state) if state is not None else None
            if scope is None or not isinstance(response_text, str) or not response_text.strip():
                return None
        if response_text.strip().casefold() in {'пожалуйста.', 'спасибо!', 'понятно.', 'ок', 'хорошо.'}:
            return None
        review = self.final_review(session_id, state, response_text, expected_scope=scope)
        note = self.review_note(review)
        if not review['verified'] or review['verdict'] != 'ACCEPT':
            note += '\nОграничение: общий final-transform не возобновляет агентный цикл; фактическая проверка/доработка не выполнена этим плагином.'
        with self.store.lock:
            return None if not self.scope_current(scope) else response_text + '\n\n---\n' + note
