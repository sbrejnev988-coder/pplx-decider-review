"""Main: один native pre_verify feedback; финальная заметка честно scoped."""
from .state import meaningful, fingerprint, bounded_put
from .transport import scrub_text, scrub
import hashlib
import json
import re


def _response_identity(text):
    """Полная локальная identity; raw text/digest не добавляются в egress."""
    digest = hashlib.sha256()
    for offset in range(0, len(text), 4096):
        digest.update(text[offset:offset + 4096].encode('utf-8', 'surrogatepass'))
    return digest.hexdigest()


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

    def new_main_reservation(self, scope, draft=True):
        return {'key': (scope[0], scope[3]), 'scope': scope, 'review_json': None,
                'requested': False, 'http_started': False, 'draft': draft}

    def main_receipt(self, scope, once):
        from .protocol import unavailable
        with self.store.lock:
            if (once is not None and once['key'] == (scope[0], scope[3])
                    and self.scope_current(scope) and self.scope_current(once['scope'])
                    and once['review_json'] is not None
                    and 0 <= self.store.clock() - once['time'] < scope[7].cache_ttl):
                return json.loads(once['review_json'])
            return unavailable('Receipt исходного черновика отсутствует, ещё не готов либо потерял актуальный scope; повторный PPLX-запрос не выполняется.')

    def admit_main_request(self, scope, reservation):
        # Called under the same lock at the worker's actual request point.
        # A released transport lock or changed candidate never replenishes this slot.
        with self.store.lock:
            once = scope[1].get('main_review_once')
            if (reservation is None or once is not reservation or not self.scope_current(scope)
                    or once['key'] != (scope[0], scope[3]) or not self.scope_current(once['scope'])
                    or once['http_started']):
                return False
            once['http_started'] = True
            return True

    def final_review(self, session_id, state, response, paths=None, expected_scope=None, main_reservation=None):
        from .protocol import unavailable
        if not isinstance(response, str):
            return unavailable('Некорректный тип финального ответа.')
        response_identity = _response_identity(response)
        with self.store.lock:
            scope = expected_scope if expected_scope is not None else self.capture_scope(session_id, state=state)
            if not self.scope_current(scope) or scope[1] is not state:
                return unavailable('Изменилась сессия, задача либо конфигурация reviewer.')
            goal = state['goal']
            trace = list(state.get('tool_evidence', []))
            candidate = fingerprint([goal, response_identity, trace, scope[7].tag])
            once = state.get('main_review_once')
            policy = scope[7]
            if once is not None and once is not main_reservation:
                return self.main_receipt(scope, once)
            if policy.valid and policy.mode == 'bounded' and policy.enabled and policy.main_enabled:
                if once is None:
                    if main_reservation is not None:
                        return unavailable('Утрачен слот исходного черновика; повторный PPLX-запрос не выполняется.')
                    once = self.new_main_reservation(scope, draft=False)
                    state['main_review_once'] = once
                once['candidate'] = candidate
            turn = state.get('turn', '')
            previous = state.get('last_final')
            ttl = scope[7].cache_ttl
            review = None
            if (previous and previous['candidate'] == candidate and previous['turn'] == turn
                    and self.store.clock() - previous['time'] < ttl and (paths is None or paths == previous['paths'])):
                review = json.loads(json.dumps(previous['review']))
        evidence = {'final_response': response, 'changed_paths': paths or [], 'tool_trace': trace}
        if review is None:
            review = self.cached_review(session_id, goal, evidence, main=True, deadline=self.deadline(),
                                        identity=turn, expected_scope=scope, main_reservation=once)
        with self.store.lock:
            if not self.scope_current(scope):
                return unavailable('Изменилась сессия, задача либо конфигурация reviewer.')
            state['last_final'] = {'candidate': candidate, 'review': json.loads(json.dumps(review)), 'paths': list(paths or []),
                                    'time': self.store.clock(), 'turn': turn}
            if once is not None and state.get('main_review_once') is once and once['review_json'] is None:
                receipt = json.dumps(review, ensure_ascii=True, allow_nan=False)
                if len(receipt) > 16000:
                    receipt = json.dumps(unavailable('Превышен лимит receipt исходного черновика.'))
                once['review_json'], once['time'] = receipt, self.store.clock()
                review = json.loads(receipt)
            return review

    def pre_verify(self, session_id='', changed_paths=None, final_response='', attempt=0, all_finals=False, **kwargs):
        from .protocol import unavailable
        if type(attempt) is not int or attempt != 0:
            return None
        tracked = isinstance(changed_paths, list) and any(isinstance(p, str) and p.strip() for p in changed_paths)
        paths = [p[:2000] for p in changed_paths[:32] if isinstance(p, str) and p.strip()] if tracked else []
        # Only native exact bool opt-in broadens the legacy tracked-path gate.
        if all_finals is not True and not tracked:
            return None
        with self.store.lock:
            state = self.main_state(session_id)
            scope = self.capture_scope(session_id, state=state) if state is not None else None
            if scope is None or not isinstance(final_response, str) or not final_response.strip():
                return None
            policy = scope[7]
            if not policy.valid or policy.mode != 'bounded' or not policy.enabled or not policy.main_enabled:
                return None
            # Admission is per owner/session/native turn, NOT per goal/candidate.
            # Reserve BEFORE HTTP so a concurrent callback cannot consume the nudge.
            if state.get('main_review_once') is not None:
                return None
            once = self.new_main_reservation(scope)
            state['main_review_once'] = once
        try:
            review = self.final_review(session_id, state, final_response, paths, expected_scope=scope, main_reservation=once)
            receipt = json.dumps(review, ensure_ascii=True, allow_nan=False)
            if len(receipt) > 16000:
                receipt = json.dumps(unavailable('Превышен лимит receipt исходного черновика.'))
        except Exception:
            receipt = json.dumps(unavailable('Оценка исходного черновика недоступна.'))
        with self.store.lock:
            if not self.scope_current(scope) or state.get('main_review_once') is not once:
                return None
            # Immutable bounded JSON has no aliases to cache/public review/caller objects.
            if once['review_json'] is None:
                once['review_json'], once['time'] = receipt, self.store.clock()
            review = json.loads(once['review_json'])
            if not review['verified'] or review['verdict'] not in ('RETRY', 'INSPECT'):
                return None
            once['requested'] = True  # Delivery requested, NEVER proof Sol completed a pass.
            return {'action': 'continue', 'message':
                    'Область PPLX: исходный черновик (DRAFT).\n' + self.review_note(review, guidance=False)
                    + '\nSol: самостоятельно сверь факты, требования и реальные свидетельства. '
                      'Исправь только подтверждённые ошибки; неподтверждённые замечания явно обозначь как неподтверждённые. '
                      'Это единственный проход проверки в этом native ходе; автоматически запускать субагентов нельзя. '
                      'Вероятности PPLX — оценка, не истина и не разрешение на действия.'}

    def transform_llm_output(self, session_id='', response_text='', verification_pass_status=None, **kwargs):
        from .protocol import unavailable
        if not isinstance(response_text, str) or not response_text.strip():
            return None
        response_identity = _response_identity(response_text)
        with self.store.lock:
            state = self.main_state(session_id)
            scope = self.capture_scope(session_id, state=state) if state is not None else None
            if scope is None or not isinstance(response_text, str) or not response_text.strip():
                return None
            once = state.get('main_review_once')
            native_pass = verification_pass_status in ('requested', 'completed')
            if native_pass and once is None:
                # Retain a no-HTTP tombstone even if the immutable receipt was lost.
                once = self.new_main_reservation(scope)
                once['http_started'] = True
                state['main_review_once'] = once
            # A fallback final receipt does not become a draft merely by reuse.
            draft_scope = native_pass or (once is not None and (
                once['draft'] or once.get('candidate') != fingerprint([
                    state['goal'], response_identity, list(state.get('tool_evidence', [])), scope[7].tag])))
            review = None
            if draft_scope:
                # A native pass marker also forbids another request after receipt loss.
                # A config/goal/TTL/unload fence can invalidate a receipt, never re-review it.
                review = self.main_receipt(scope, once)
        if response_text.strip().casefold() in {'пожалуйста.', 'спасибо!', 'понятно.', 'ок', 'хорошо.'}:
            return None
        application_candidate = None
        if not draft_scope:
            review = self.final_review(session_id, state, response_text, expected_scope=scope)
            with self.store.lock:
                once = state.get('main_review_once')
                candidate = fingerprint([state['goal'], response_identity, list(state.get('tool_evidence', [])), scope[7].tag])
                application_candidate = (once.get('candidate') if once is not None
                                         else (state.get('last_final') or {}).get('candidate'))
                if once is not None and (once['draft'] or once.get('candidate') != candidate):
                    draft_scope = True
                    review = self.main_receipt(scope, once)
        note = ('Область PPLX: исходный черновик (DRAFT), не текущий финальный ответ.\n' if draft_scope else '')
        note += self.review_note(review, guidance=False)
        if draft_scope:
            note += '\nПовторная PPLX-оценка финала не выполнялась.'
            if verification_pass_status == 'completed':
                note += '\nverification_pass_status=completed: native core сообщил о завершённом проходе Sol; это не доказывает исправление каждого замечания.'
            elif verification_pass_status == 'requested':
                note += '\nverification_pass_status=requested: проход Sol запрошен; завершение не подтверждено.'
            else:
                note += '\nverification_pass_status=unknown: native подтверждение завершения прохода Sol отсутствует.'
        if not review['verified'] or review['verdict'] != 'ACCEPT':
            note += '\nОграничение: эта заметка не запускает новый цикл работы. Фактическую проверку и исправления плагин не выполняет.'
        with self.store.lock:
            if not self.scope_current(scope):
                return None
            # Evidence callbacks share this lock. Recheck their exact identity
            # at application, not only before formatting the decoration.
            if not draft_scope and application_candidate != fingerprint([
                    state['goal'], response_identity, list(state.get('tool_evidence', [])), scope[7].tag]):
                return None
            return response_text + '\n\n---\n' + note
