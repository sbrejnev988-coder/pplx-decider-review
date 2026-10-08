"""Focused native SDK regressions; controlled Decisions, no LLM/network.

Run via lanes/plugin/run_native_onepass.py under the selected native Python.
PluginContext, config validator, home/secret scopes and hook dispatcher are real.
The no-edit all-finals cases require a host that actually emits those metadata;
the current standard SDK is not claimed to support that producer path.
"""
from __future__ import annotations
import ast
import contextvars
import importlib.util
import json
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace

import httpx
import hermes_cli.plugins as native
import hermes_cli.plugins_state as native_state
import hermes_constants as homes
import agent.secret_scope as secrets

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'openai/gpt-6-luna-decisions'
ENDPOINT = 'https://openrouter.ai/api/alpha/decisions'
HOOKS = {'pre_llm_call', 'transform_tool_result', 'subagent_start', 'subagent_stop', 'pre_verify', 'transform_llm_output'}
GOAL = 'Самостоятельно проверить факты и вернуть подтверждённый результат'
DRAFT = 'Первый черновик: результат готов, свидетельств пока нет.'
FINAL = 'Итог модели: неподтверждённое заявление снято; ограничения указаны.'

# Execute only the hash-pinned current-source pure fixture and its literal constant.
base = ROOT / 'tests/conftest.py'
import hashlib
assert hashlib.sha256(base.read_bytes()).hexdigest() == '3748ea896b3d69e6900e032d8602842c5a2521a42c834607393f1b8c4caa26ac'
tree = ast.parse(base.read_text(encoding='utf-8'))
nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'answer_payload'
         or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'MODEL' for t in n.targets)]
assert len(nodes) == 2
fixture_namespace = {}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(base), 'exec'), fixture_namespace)
answer_payload = fixture_namespace['answer_payload']


class NativeOnePass(unittest.TestCase):
    def setUp(self):
        self.envs = []
        self.threads = []

    def tearDown(self):
        for thread, release in self.threads:
            release.set()
            thread.join(3)
            self.assertFalse(thread.is_alive(), 'Fixture worker must finish naturally')
        for env in reversed(self.envs):
            env.manager.unload(env.manifest)
            self.assertTrue(env.runtime.closed)
            self.assertFalse(env.manager._hooks)
            secrets.reset_secret_scope(env.secret_token)
            homes.reset_hermes_home_override(env.home_token)

    def make_env(self, **settings):
        home = SCRATCH_HOME / (self._testMethodName + '-owner-' + str(len(self.envs)))
        home.mkdir()
        config = {'enabled': True, 'reviewer_model': MODEL, 'mode': 'bounded',
                  'callback_budget_seconds': 2, 'timeout_seconds': 1, 'log_enabled': False}
        config.update(settings)
        home.joinpath('config.yaml').write_text(json.dumps({'model': {'default': 'never-use-main'},
            'plugins': {'entries': {'decision-review': {'settings': config}}}}), encoding='utf-8')
        ht = homes.set_hermes_home_override(home)
        st = secrets.set_secret_scope({'OPENROUTER_API_KEY': 'synthetic-only'}, profile_home=str(home))
        manifest = native.parse_manifest_file(ROOT / 'plugin.yaml', ROOT, 'user', '')
        self.assertIsNotNone(manifest)
        manager = native.PluginManager(scope_key=str(home))
        ctx = native.PluginContext(manifest, manager)
        self.assertIs(type(ctx), native.PluginContext)
        self.assertIs(native._plugin_relative_segments, native_state._plugin_relative_segments)
        for forbidden in ('model', 'security', 'settings', '../reviewer_model'):
            with self.assertRaises(ValueError):
                ctx.get_config(forbidden, None)
        name = 'native_onepass_' + str(len(self.envs)) + '_' + self._testMethodName
        spec = importlib.util.spec_from_file_location(name, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
        plugin = importlib.util.module_from_spec(spec)
        sys.modules[name] = plugin
        spec.loader.exec_module(plugin)
        calls, replies = [], []
        def handler(request):
            payload = json.loads(request.content)
            self.assertEqual((request.method, str(request.url), payload['model']), ('POST', ENDPOINT, MODEL))
            calls.append(payload)
            if replies:
                reply = replies.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                if callable(reply):
                    return reply(request, payload)
                return httpx.Response(200, json=reply)
            return httpx.Response(200, json=answer_payload(payload))
        runtime = plugin.register(ctx)
        runtime.transport = httpx.MockTransport(handler)
        self.assertEqual(set(manager._hooks), HOOKS)
        self.assertFalse(manager._middleware)
        self.assertFalse(manager._plugin_tool_names)
        env = SimpleNamespace(runtime=runtime, ctx=ctx, manager=manager, manifest=manifest,
            home=home, home_token=ht, secret_token=st, calls=calls, replies=replies, plugin=plugin)
        self.envs.append(env)
        return env

    def hook(self, env, name, **kwargs):
        values = env.manager.invoke_hook(name, **kwargs)
        return values[0] if values else None

    def begin(self, env, turn='turn-1', goal=GOAL, sid='parent', **kwargs):
        return self.hook(env, 'pre_llm_call', session_id=sid, turn_id=turn, task_id='task-1',
            user_message=goal, conversation_history=[{'role': 'user', 'content': goal}], **kwargs)

    def adverse(self, env, inspect=False):
        env.replies.append(lambda r, p: httpx.Response(200, json=answer_payload(p,
            completed=.7 if inspect else .4, reliable=.7 if inspect else .5,
            adverse=.4 if inspect else .7)))

    def pre(self, env, **kwargs):
        args = dict(session_id='parent', final_response=DRAFT, changed_paths=[], attempt=0, all_finals=True)
        args.update(kwargs)
        return self.hook(env, 'pre_verify', **args)

    def final(self, env, **kwargs):
        args = dict(session_id='parent', response_text=FINAL)
        args.update(kwargs)
        return self.hook(env, 'transform_llm_output', **args)

    def test_existing_main_and_note_controls_keep_actual_assertions(self):
        # Reuse only reviewed function ASTs, not conftest's fake SDK or pytest.
        from importlib import import_module
        groups = {
            'test_main.py': ['test_main_preverify_real_edits_one_continue_with_ru_probability_context',
                'test_no_tracked_edits_no_continue_but_general_final_still_advisory',
                'test_same_parent_goal_retry_recommendation_cap_across_changed_evidence',
                'test_main_final_reuses_same_candidate_preverify_review'],
            'test_note_readability.py': ['test_retry_note_explains_probabilities_without_claiming_proven_defects',
                'test_child_retry_recommendation_requires_actual_verification',
                'test_unavailable_note_has_no_invented_probabilities'],
        }
        namespace = {'json': json, 'sys': sys, 'httpx': httpx, 'import_module': import_module,
                     'answer_payload': answer_payload, 'start': lambda env: env.runtime}
        helpers = {'begin', 'adverse_reply'}
        for filename, names in groups.items():
            path = ROOT / 'tests' / filename
            parsed = ast.parse(path.read_text(encoding='utf-8'))
            selected = [n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name in set(names) | helpers
                        or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'MAIN_KEYS' for t in n.targets)]
            exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), namespace)
            for name in names:
                env = self.make_env()
                env.ctx.hooks = {n: c[0] for n, c in env.manager._hooks.items()}
                class Settings:
                    def __setitem__(self, key, value):
                        env.ctx.set_config(key, value)
                env.ctx.settings = Settings()  # Writes still use native config/namespace validation.
                class Calls:
                    def __len__(self):
                        return len(env.calls)
                    def __getitem__(self, index):
                        return (None, env.calls[index])
                proxy = SimpleNamespace(runtime=env.runtime, p=env.plugin, ctx=env.ctx,
                                        calls=Calls(), replies=env.replies, home=[env.home])
                namespace[name](proxy)

    def test_plain_native_one_pass_and_honest_draft_scope(self):
        env = self.make_env()
        self.begin(env)
        self.adverse(env)
        nudge = self.pre(env)
        self.assertIsNotNone(nudge, 'Explicit native all_finals must deliver Decisions feedback before final without edits')
        self.assertEqual(nudge['action'], 'continue')
        self.assertIn('Основной агент', nudge['message'])
        self.assertIn('неподтверж', nudge['message'])
        self.assertIn('вероятность', nudge['message'])
        self.assertIn('самостоятельно', nudge['message'])
        self.assertIn('факт', nudge['message'])
        self.assertEqual(len(env.calls), 1)
        wire = json.dumps(env.calls[0]['state'], ensure_ascii=False)
        self.assertIn(DRAFT, wire)
        self.assertNotIn(FINAL, wire)
        for status in ('requested', 'completed', None):
            kw = {} if status is None else {'verification_pass_status': status}
            text = self.final(env, **kw)
            self.assertTrue(text.startswith(FINAL + '\n\n---\n'))
            self.assertIn('DRAFT', text)
            self.assertIn('исходный черновик', text)
            self.assertIn('verification_pass_status=' + (status or 'unknown'), text)
            self.assertNotIn('Sol:', text)
            self.assertNotIn('Основной агент:', text)
            self.assertNotIn('DECISIONS ACCEPT', text)
            self.assertNotIn('Sol исправил', text)
            self.assertNotIn('Основной агент исправил', text)
            if status != 'completed':
                self.assertNotIn('проход Sol завершён', text)
                self.assertNotIn('завершённом проходе основного агента', text)
            self.assertEqual(len(env.calls), 1, 'Correction must not trigger a second Decisions request')
        self.assertIsNone(self.pre(env, final_response=FINAL))
        self.assertIsNone(self.pre(env, final_response=FINAL, attempt=1))

    def test_legacy_no_edits_and_accept_do_not_force_continue(self):
        env = self.make_env()
        self.begin(env)
        self.assertIsNone(self.pre(env, all_finals=False))
        self.assertIsNone(self.pre(env, all_finals=1))
        self.assertFalse(env.calls)
        self.assertIsNone(self.pre(env, changed_paths=['C:/synthetic/a.py'], all_finals=False))
        text = self.final(env, response_text=DRAFT)
        self.assertTrue(text.startswith(DRAFT))
        self.assertEqual(len(env.calls), 1)
        self.assertIn('DECISIONS ACCEPT', text)
        self.assertIsNone(self.pre(env, final_response=FINAL, changed_paths=['C:/synthetic/a.py']))
        self.assertEqual(len(env.calls), 1)

    def test_eligibility_child_disabled_advisory_and_attempt_types(self):
        env = self.make_env()
        self.begin(env)
        for bad in (True, '0', -1, 1):
            self.assertIsNone(self.pre(env, attempt=bad))
        for setting, value in (('mode', 'advisory'), ('enabled', False), ('review_main_agent', False)):
            old = env.ctx.get_config(setting, True if setting != 'mode' else 'bounded')
            env.ctx.set_config(setting, value)
            self.assertIsNone(self.pre(env))
            env.ctx.set_config(setting, old)
        self.begin(env, sid='child', parent_session_id='parent')
        self.assertIsNone(self.pre(env, session_id='child'))
        self.assertIsNone(self.final(env, session_id='child'))
        self.begin(env, turn='turn-2', goal='Спасибо!')
        self.assertIsNone(self.pre(env))
        self.assertFalse(env.calls)

    def test_api_error_and_tampered_policy_fail_open_without_second_request(self):
        env = self.make_env()
        self.begin(env)
        env.replies.append(httpx.ConnectError('Synthetic refused network'))
        self.assertIsNone(self.pre(env))
        text = self.final(env, verification_pass_status='requested')
        self.assertTrue(text.startswith(FINAL))
        self.assertIn('заключение Decision Review отсутствует', text)
        self.assertEqual(len(env.calls), 1)
        self.begin(env, turn='turn-2')
        env.ctx.set_config('reviewer_model', 'wrong-model')
        self.assertIsNone(self.pre(env))
        self.assertEqual(len(env.calls), 1)
        text = self.final(env, verification_pass_status='completed')
        self.assertTrue(text.startswith(FINAL))
        self.assertIn('заключение Decision Review отсутствует', text)
        self.assertEqual(len(env.calls), 1)

    def test_concurrent_callback_reserves_before_http_and_single_nudge(self):
        env = self.make_env()
        self.begin(env)
        entered, release = threading.Event(), threading.Event()
        def delayed(request, payload):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Fixture deadline')
            return httpx.Response(200, json=answer_payload(payload, completed=.4, reliable=.5, adverse=.7))
        env.replies.append(delayed)
        values = []
        caller = contextvars.copy_context()
        t = threading.Thread(target=lambda: caller.run(lambda: values.append(self.pre(env))))
        self.threads.append((t, release))
        t.start()
        self.assertTrue(entered.wait(2))
        # Exercise plugin admission directly as well: native dispatch suppression
        # must not mask a plugin race in this negative control.
        second = env.manager._hooks['pre_verify'][0](session_id='parent', final_response=FINAL,
            changed_paths=[], attempt=0, all_finals=True)
        self.assertIsNone(second)
        release.set()
        t.join(3)
        self.assertFalse(t.is_alive())
        self.assertIsNotNone(values[0], 'A duplicate must not steal the original reserved nudge')
        self.assertEqual(len(env.calls), 1)
        self.assertIsNone(self.pre(env, final_response=FINAL))
        text = self.final(env, verification_pass_status='completed')
        self.assertIn('DECISIONS RETRY', text)
        self.assertEqual(len(env.calls), 1)

    def test_receipt_independent_of_caller_and_cache_miss_after_correction(self):
        env = self.make_env()
        self.begin(env)
        self.adverse(env, inspect=True)
        result = self.pre(env, changed_paths=['C:/synthetic/a.py'])
        self.assertEqual(result['action'], 'continue', 'Verified INSPECT also needs a real user nudge')
        state = env.runtime.main_state('parent')
        # These objects are exposed by earlier public helper calls/cache consumers.
        state['last_final']['review']['verdict'] = 'ACCEPT'
        state['last_final']['review']['answers']['task_satisfied']['noul'] = 1
        result['message'] = 'caller-mutated'
        state['cache'].clear()
        state.pop('last_final', None)
        text = self.final(env, verification_pass_status='completed')
        self.assertIn('DECISIONS INSPECT', text)
        self.assertIn('70,0%', text)
        self.assertNotIn('caller-mutated', text)
        self.assertNotIn('DECISIONS ACCEPT', text)
        self.assertEqual(len(env.calls), 1)
        # Assessment TTL invalidates reuse, never the turn's HTTP budget.
        now = env.runtime.store.clock()
        env.runtime.store.clock = lambda: now + 301
        text = self.final(env, verification_pass_status='completed')
        self.assertIn('заключение Decision Review отсутствует', text)
        self.assertEqual(len(env.calls), 1)

    def test_new_native_turn_repeated_goal_resets_only_main_cap(self):
        env = self.make_env()
        self.begin(env)
        self.adverse(env)
        self.assertIsNotNone(self.pre(env))
        self.begin(env)  # Idempotent replay of same real turn.
        self.assertIsNone(self.pre(env, final_response=FINAL))
        self.begin(env, turn='turn-2')
        self.adverse(env)
        self.assertIsNotNone(self.pre(env), 'Same goal in a new native turn has its own main slot')
        self.assertIn('DECISIONS RETRY', self.final(env, verification_pass_status='completed'))
        self.assertEqual(len(env.calls), 2)
        def child(summary):
            self.adverse(env)
            out = self.hook(env, 'transform_tool_result', tool_name='delegate_task', session_id='parent',
                args={'goal': 'Проверить дочерний компонент'}, result=json.dumps({'results': [{'status': 'completed', 'summary': summary}]}))
            return json.loads(out)['pplx_review'][0]
        self.assertEqual(child('Первая дочерняя попытка')['verdict'], 'RETRY')
        self.begin(env, turn='turn-3')
        self.assertEqual(child('Вторая дочерняя попытка')['verdict'], 'INSPECT')

    def test_native_status_cache_loss_never_requests_new_review(self):
        env = self.make_env()
        self.begin(env)
        for status in ('requested', 'completed'):
            text = self.final(env, verification_pass_status=status)
            self.assertTrue(text.startswith(FINAL))
            self.assertIn('DRAFT', text)
            self.assertIn('заключение Decision Review отсутствует', text)
        self.assertFalse(env.calls)

    def test_config_change_new_goal_and_ttl_do_not_adopt_old_receipt(self):
        env = self.make_env()
        self.begin(env)
        self.adverse(env)
        self.assertIsNotNone(self.pre(env))
        env.ctx.set_config('reviewer_model', 'wrong-model')
        text = self.final(env, verification_pass_status='completed')
        self.assertIn('заключение Decision Review отсутствует', text)
        self.assertNotIn('DECISIONS RETRY', text)
        env.ctx.set_config('reviewer_model', MODEL)
        self.begin(env, goal='Новая цель в той же native сессии и том же ходе')
        text = self.final(env, verification_pass_status='requested')
        self.assertIn('заключение Decision Review отсутствует', text)
        self.assertIsNone(self.pre(env, final_response=FINAL))
        self.assertEqual(len(env.calls), 1)
        now = env.runtime.store.clock()
        env.runtime.store.clock = lambda: now + 4000
        text = self.final(env, verification_pass_status='completed')
        self.assertIsNone(text)  # State expired: no goal/receipt to inherit.
        self.assertEqual(len(env.calls), 1)

    def test_running_worker_scope_aba_and_unload_fences(self):
        env = self.make_env()
        self.begin(env)
        entered, release = threading.Event(), threading.Event()
        def delayed(request, payload):
            entered.set()
            release.wait(3)
            return httpx.Response(200, json=answer_payload(payload, completed=.4, reliable=.5, adverse=.7))
        env.replies.append(delayed)
        values = []
        caller = contextvars.copy_context()
        t = threading.Thread(target=lambda: caller.run(lambda: values.append(self.pre(env))))
        self.threads.append((t, release))
        t.start()
        self.assertTrue(entered.wait(2))
        self.begin(env, turn='turn-2')
        self.begin(env, turn='turn-1')  # ABA must not legalize the old scope.
        env.runtime.close()
        self.assertTrue(env.runtime.inflight.locked())
        release.set()
        t.join(3)
        self.assertFalse(t.is_alive())
        self.assertEqual(values, [None])
        self.assertFalse(env.runtime.inflight.locked())
        self.assertIsNone(self.final(env, verification_pass_status='completed'))
        self.assertEqual(len(env.calls), 1)
