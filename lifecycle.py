"""Native lifecycle provenance: observers без сети и без разрешения действий."""
import re
from .state import identifier, meaningful, bounded_put, TERMINAL, fingerprint
from .transport import scrub, scrub_text

COMPLETE = re.compile(r'^\[ASYNC DELEGATION (?:BATCH )?COMPLETE — ([A-Za-z0-9_.:-]{1,128})\]')


class Lifecycle:
    def subagent_start(self, parent_session_id='', parent_turn_id='', child_session_id='', child_subagent_id='', child_goal='', child_role='', **kwargs):
        if not self.active(child=True):
            return None
        cid, subid = identifier(child_session_id), identifier(child_subagent_id)
        with self.store.lock:
            if self.closed:
                return None
            _, state = self.store.session(parent_session_id)
            if state is None or not cid or not subid or cid == identifier(parent_session_id):
                return None
            _, child = self.store.session(cid)
            child['parent'] = identifier(parent_session_id)
            previous = state['children'].get(cid)
            if previous is not None and previous['subid'] == subid:
                return None
            if previous is not None:
                for did in [did for did, dispatch in state['dispatches'].items() if cid in dispatch['children']]:
                    state['dispatches'].pop(did, None)
            if previous is None or previous['subid'] != subid:
                # Parent start-generation fence, NOT an exact sync receipt/child mapping.
                # Native sync results contain no child IDs; unrelated starts also invalidate.
                state['sync_generation'] = (state.get('sync_generation', 0) + 1) % (1 << 64)
                if state['sync_generation'] == 0:
                    state['cache'].clear()
            bounded_put(state['children'], cid, {'time': self.store.clock(), 'subid': subid, 'goal': scrub_text(child_goal, 2000) if isinstance(child_goal, str) else '',
                                                'launch_turn': identifier(parent_turn_id), 'role': scrub_text(str(child_role), 80), 'stop': None})
        return None

    def subagent_stop(self, parent_session_id='', child_session_id='', child_summary=None, child_status='', tool_call_history=None, duration_ms=None, **kwargs):
        if not self.active(child=True):
            return None
        with self.store.lock:
            if self.closed:
                return None
            _, state = self.store.session(parent_session_id)
            if state is None:
                return None
            child = state['children'].get(identifier(child_session_id))
            if child is not None and isinstance(child_status, str) and child_status in TERMINAL:
                child['stop'] = {'status': child_status, 'summary': scrub_text(child_summary) if isinstance(child_summary, str) else None,
                                  'tool_call_history': scrub(tool_call_history) if isinstance(tool_call_history, list) else [],
                                  'duration_ms': duration_ms if type(duration_ms) is int and 0 <= duration_ms <= 86400000 else None}
        return None

    def task_constraints(self, args, idx):
        values = [args.get('context')]
        tasks = args.get('tasks')
        if isinstance(tasks, list) and type(idx) is int and 0 <= idx < len(tasks) and isinstance(tasks[idx], dict):
            values.append(tasks[idx].get('context'))
        return '\n'.join(scrub_text(v, 2000) for v in values if isinstance(v, str))[:4000]

    def record_dispatch(self, session_id, data, args=None):
        args = args if isinstance(args, dict) else {}
        ids = data.get('subagent_ids')
        with self.store.lock:
            if self.closed:
                return
            _, state = self.store.session(session_id)
            if state is None or not isinstance(ids, list):
                return
            by_subid = {c['subid']: cid for cid, c in state['children'].items()}
            for idx, subid in enumerate(ids[:128]):
                cid = by_subid.get(subid) if isinstance(subid, str) else None
                if cid:
                    state['children'][cid]['constraints'] = self.task_constraints(args, idx)
            units = data.get('units')
            if not isinstance(units, list) or not units:
                units = [{'delegation_id': data.get('delegation_id'), 'task_indexes': list(range(min(len(ids), 128)))}]
            for unit in units[:128]:
                if not isinstance(unit, dict):
                    continue
                did = identifier(unit.get('delegation_id'))
                indexes = unit.get('task_indexes')
                if not did or not isinstance(indexes, list) or not indexes or len(indexes) > 128:
                    continue
                linked = [by_subid.get(ids[i]) for i in indexes if type(i) is int and 0 <= i < len(ids) and isinstance(ids[i], str)]
                if len(linked) != len(indexes) or not all(linked) or len(set(linked)) != len(linked):
                    continue
                if did not in state['dispatches']:
                    bounded_put(state['dispatches'], did, {'time': self.store.clock(), 'children': linked, 'delivered': False})

    def pre_llm_call(self, session_id='', user_message='', conversation_history=None, parent_session_id='', task_id='', turn_id='', **kwargs):
        if not self.active() and not self.active(child=True):
            return None
        with self.store.lock:
            if self.closed or ('key' in locals() and not self.store.current(key, state)):
                return None
            key, state = self.store.session(session_id)
            if state is None:
                return None
        rows = conversation_history if isinstance(conversation_history, list) else []
        last_user = next((r for r in reversed(rows[-128:]) if isinstance(r, dict) and r.get('role') == 'user'), {})
        # Метаданные обязаны относиться к текущему сообщению, не старой delivery row.
        typed = last_user.get('content') == user_message and last_user.get('display_kind') == 'async_delegation_complete'
        is_notification = typed and isinstance(user_message, str)
        with self.store.lock:
            if self.closed or ('key' in locals() and not self.store.current(key, state)):
                return None
            if identifier(parent_session_id):
                state['parent'] = identifier(parent_session_id)
            if not is_notification:
                # Bounded signature; middle-only changes beyond these samples remain unknown.
                signature = fingerprint([len(user_message), user_message[:2000], user_message[-256:]]) if isinstance(user_message, str) else ''
                turn, task = identifier(turn_id), identifier(task_id)
                trivial = not meaningful(user_message)
                if state.get('turn') != turn:
                    # Reset only main-turn admission, not the existing child-goal retry cap.
                    state.pop('main_review_once', None)
                if (state.get('turn') != turn or state.get('task') != task
                        or state.get('goal_signature') != signature or state.get('trivial') != trivial):
                    state['revision'] = state.get('revision', 0) + 1
                    state['tool_evidence'] = []
                    state.pop('last_final', None)
                state.update(turn=turn, task=task, trivial=trivial, goal_signature=signature)
                if not trivial:
                    state['goal'] = user_message[:2000]
        if not self.active(child=True) or not typed or not isinstance(user_message, str):
            return None
        match = COMPLETE.match(user_message)
        metadata = last_user.get('display_metadata')
        did = identifier(metadata.get('delegation_id')) if isinstance(metadata, dict) else ''
        if not match or not did or did != match.group(1):
            return None
        with self.store.lock:
            if self.closed or ('key' in locals() and not self.store.current(key, state)):
                return None
            dispatch = state['dispatches'].get(did)
            if dispatch is None or dispatch['delivered']:
                return None
            children = [state['children'].get(cid) for cid in dispatch['children']]
            if not children or any(c is None or c['stop'] is None for c in children):
                return None
            # Не используем строки notification или агрегированные display counts как доказательство результата.
            candidates = [(cid, c['goal'], dict(c['stop'], constraints=c.get('constraints', ''))) for cid, c in zip(dispatch['children'], children)
                          if meaningful(c['goal']) and (c['stop'].get('summary') or c['stop']['status'] != 'completed')]
            if not candidates:
                return None
            scope = self.capture_scope(session_id, state=state)
            if scope is None:
                return None
            dispatch['delivered'] = True
        end = self.deadline()
        summaries = []
        for cid, goal, stop in candidates:
            review = self.cached_review(session_id, goal, stop, deadline=end, identity=cid, allow_accept=False, expected_scope=scope)
            summaries.append('Дочерняя сессия ' + cid + ': ' + self.review_note(review, child=True))
        context = 'PPLX: отдельные проверки завершённых дочерних результатов. Исходные статусы и свидетельства не заменены.\n' + '\n'.join(summaries)
        if len(context) > 16000:
            context = context[:15900] + '\nОграничение: контекст reviewer сокращён до локального лимита; исходные результаты сохранены.'
        with self.store.lock:
            return None if not self.scope_current(scope) else {'context': context}

    def review_note(self, review, child=False, guidance=True):
        marker = review['verdict']
        if not review['verified']:
            return ('PPLX INSPECT: оценку получить не удалось; заключение PPLX отсутствует.\n'
                    + ' '.join(review['errors']))
        answers = review['answers']
        statuses = {'RETRY': 'рекомендуется перепроверка',
                    'INSPECT': 'нужна дополнительная проверка',
                    'ACCEPT': 'проверяющая модель рекомендует принять результат'}
        labels = [
            ('goal_completed' if child else 'task_satisfied',
             'полного выполнения подзадачи' if child else 'полного выполнения задачи'),
            ('result_reliable' if child else 'claims_supported',
             'надёжности результата подзадачи' if child else 'подтверждённости заявлений'),
            ('important_requirement_missed', 'пропуска важного требования'),
            ('contradictions_present' if child else 'internal_contradiction', 'наличия противоречий'),
            ('unsupported_success_claim' if child else 'needs_revision',
             'неподтверждённого заявления об успехе' if child else 'необходимости доработки'),
        ]
        note = f"PPLX {marker}: {statuses[marker]}.\nПо оценке PPLX, вероятность:\n"
        for index, (key, label) in enumerate(labels):
            value = f"{answers[key]['noul'] * 100:.1f}".replace('.', ',')
            punctuation = '.' if index == len(labels) - 1 else ';'
            note += f"- {label} — {value}%{punctuation}\n"
        if guidance and marker == 'RETRY':
            note += ('Sol: сначала сверь требования с фактическими результатами. '
                     'Если подтвердятся недочёты, исправь их. '
                     'Повтор по этой цели допускается не более одного раза; '
                     'автоматически запускать субагента нельзя.')
        elif guidance and marker == 'INSPECT':
            note += 'Sol: сверь требования с доступными свидетельствами; пока не считай результат подтверждённым.'
        else:
            note += 'Это рекомендация проверяющей модели, а не доказательство правильности результата.'
        return note + '\n' + review['reason']
