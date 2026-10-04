"""Изолированные фикстуры: только синтетические ключи, запрещённая сеть."""
import importlib.util
import json
import os
import socket
import sys
import types
from pathlib import Path
import httpx
import pytest

MODEL = 'perplexity/pplx-decider-v1-27b'
ROOT = Path(__file__).resolve().parents[1]

class Context:
    def __init__(self):
        self.settings = {'enabled': True, 'reviewer_model': MODEL}
        self.hooks = {}
        self.unload = None
    def get_config(self, key, default=None):
        return self.settings.get(key, default)
    def register_hook(self, name, cb):
        self.hooks[name] = cb
    def on_unload(self, cb):
        self.unload = cb


def answer_payload(request, completed=.95, reliable=.95, adverse=.05, choice='принять'):
    answers = {}
    for name, question in request['questions'].items():
        typ = question['type']
        if typ == 'noul':
            value = completed if name in ('goal_completed', 'task_satisfied') else reliable if name in ('result_reliable', 'claims_supported') else adverse
            answers[name] = {'type': typ, 'noul': value}
        elif typ == 'choice':
            options = list(question['criteria'])
            answers[name] = {'type': typ, 'choice': choice, 'confidence': .9,
                             'probabilities': {o: .9 if o == choice else .05 for o in options}}
        else:
            answers[name] = {'type': typ, 'score': 3.8, 'confidence': .9,
                             'probabilities': {'0': .01, '1': .01, '2': .01, '3': .11, '4': .86}}
    return {'id': 'synthetic-response', 'model': MODEL, 'provider': 'Perplexity',
            'answers': answers, 'usage': {'input_tokens': 10, 'output_tokens': 0, 'cost': .00001}}

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*a, **kw):
        raise AssertionError('Сеть запрещена offline-тестом')
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)

@pytest.fixture
def env(monkeypatch, tmp_path):
    home = [tmp_path / 'synthetic-profile']
    secrets = []
    redacted = []
    def get_secret(name, default=None):
        secrets.append((str(home[0]), name))
        return 'synthetic-owner-key'
    def redact(text):
        redacted.append(text)
        return text
    agent = types.ModuleType('agent'); agent.__path__ = []
    scope = types.ModuleType('agent.secret_scope'); scope.get_secret = get_secret
    red = types.ModuleType('agent.redact'); red.redact_for_egress = redact
    const = types.ModuleType('hermes_constants'); const.get_hermes_home = lambda: home[0]
    for name, value in [('agent', agent), ('agent.secret_scope', scope), ('agent.redact', red), ('hermes_constants', const)]:
        monkeypatch.setitem(sys.modules, name, value)
    spec = importlib.util.spec_from_file_location('pplx_review_test', ROOT / '__init__.py')
    plugin = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, plugin)
    spec.loader.exec_module(plugin)
    calls = []
    replies = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append((request, payload))
        if replies:
            response = replies.pop(0)
            if isinstance(response, Exception):
                raise response
            if callable(response):
                return response(request, payload)
            return httpx.Response(200, json=response)
        return httpx.Response(200, json=answer_payload(payload))
    ctx = Context()
    return types.SimpleNamespace(p=plugin, ctx=ctx, home=home, calls=calls, replies=replies,
                                 transport=httpx.MockTransport(handler), secrets=secrets, redacted=redacted)

def start(env):
    assert callable(getattr(env.p, 'register', None)), 'Отсутствует native register(ctx)'
    runtime = env.p.register(env.ctx)
    runtime.transport = env.transport
    return runtime

class UnitPublicationContext(Context):
    """Unit-only owner: синтетический JSON, не native PluginContext/validator."""
    def __init__(self, config_path):
        super().__init__()
        self.config_path = config_path

    def get_config(self, key, default=None):
        config = json.loads(self.config_path.read_text(encoding='utf-8'))
        return config['plugins']['entries']['pplx-decider-review']['settings'].get(key, default)

    def set_config(self, key, value):
        config = json.loads(self.config_path.read_text(encoding='utf-8'))
        config['plugins']['entries']['pplx-decider-review']['settings'][key] = value
        self.config_path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        assert self.get_config(key) == value


@pytest.fixture
def publication_env(request, record_property, scenario):
    """Те же behavior cases: unit по умолчанию, настоящий SDK только явно."""
    if os.environ.get('PPLX_TEST_NATIVE_CORE'):
        native = request.getfixturevalue('native_env')
        record_property('publication_test_mode', 'native SDK integration')
        return native
    unit = request.getfixturevalue('env')
    home = unit.home[0]
    home.mkdir()
    settings = {
        'enabled': True, 'provider': 'openrouter',
        'endpoint': 'https://openrouter.ai/api/alpha/decisions', 'language': 'ru',
        'review_subagents': True, 'review_main_agent': True, 'mode': 'bounded',
        'max_review_retries': 1, 'min_accept_confidence': .8, 'retry_threshold': .65,
        'fail_open_on_api_error': True, 'log_enabled': True,
        'callback_budget_seconds': 2, 'timeout_seconds': 1,
    }
    assert scenario in ('valid', 'default')
    if scenario == 'valid':
        settings['reviewer_model'] = MODEL
    config_path = home / 'config.yaml'
    config_path.write_text(json.dumps({'plugins': {'entries': {
        'pplx-decider-review': {'settings': settings}}}}), encoding='utf-8')
    unit.ctx = UnitPublicationContext(config_path)
    runtime = start(unit)
    request.addfinalizer(runtime.close)
    record_property('publication_test_mode', 'unit: synthetic owner, no native SDK claim')
    record_property('production_source', str(ROOT))
    return types.SimpleNamespace(runtime=runtime, ctx=unit.ctx, home=home, calls=unit.calls)
