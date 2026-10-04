"""Metadata/retention: unit owner по умолчанию, явный native SDK; синтетический HTTP."""
import json

import httpx
import pytest
from conftest import MODEL, answer_payload
from test_native_config import native_env  # fixture discovery; SDK import только при явном opt-in


def log_path(home):
    return home / 'plugin-data' / 'pplx-decider-review' / 'reviews.jsonl'


@pytest.mark.parametrize('scenario', ['default'])
def test_unavailable_log_does_not_invent_actual_model(publication_env):
    env = publication_env
    env.runtime.transport = httpx.MockTransport(lambda request: httpx.Response(503))
    env.runtime.cached_review('metadata-owner', 'Проверить отказ', {'summary': 'Синтетический результат'}, main=True)
    row = json.loads(log_path(env.home).read_text(encoding='utf-8'))
    assert row['model'] is None
    assert row['requested_model'] == MODEL
    assert row['verified'] is False and row['verdict'] == 'INSPECT'

    snapshot = MODEL + '-20261001'
    def reply(request):
        payload = answer_payload(json.loads(request.content))
        payload['model'] = snapshot
        return httpx.Response(200, json=payload)
    env.runtime.transport = httpx.MockTransport(reply)
    env.runtime.cached_review('metadata-owner', 'Проверить snapshot', {'summary': 'Другой результат'}, main=True)
    row = json.loads(log_path(env.home).read_text(encoding='utf-8').splitlines()[-1])
    assert row['model'] == snapshot and row['requested_model'] == MODEL and row['verified'] is True


@pytest.mark.parametrize('scenario', ['default'])
def test_malformed_provider_is_fail_open_inspect(publication_env):
    env = publication_env
    for provider in (123, None, True, [], {}, 'x' * 129):
        def reply(request):
            payload = answer_payload(json.loads(request.content))
            payload['provider'] = provider
            return httpx.Response(200, json=payload)
        env.runtime.transport = httpx.MockTransport(reply)
        review = env.runtime.review('Проверить metadata', {'summary': 'Синтетический результат'}, main=True)
        assert (review['verified'], review['verdict']) == (False, 'INSPECT')
        assert review['model'] is None and review['provider'] is None

    for provider in ('', 'Произвольный upstream', 'x' * 128, None):
        def reply(request):
            payload = answer_payload(json.loads(request.content))
            if provider is None:
                payload.pop('provider')
            else:
                payload['provider'] = provider
            return httpx.Response(200, json=payload)
        env.runtime.transport = httpx.MockTransport(reply)
        review = env.runtime.review('Проверить metadata', {'summary': 'Синтетический результат'}, main=True)
        assert review['verified'] is True and review['provider'] == provider


@pytest.mark.parametrize('scenario', ['default'])
@pytest.mark.parametrize('keep,limit,oversized', [(1, 2048, False), (2, 2048, False),
                                               (3, 1024, False), (1, 1024, True),
                                               (2, 1024, True), (3, 1024, True)])
def test_retention_settings_bound_known_files(publication_env, keep, limit, oversized):
    import importlib
    env = publication_env
    package = type(env.runtime).__module__
    logger = importlib.import_module(package + '.review_log')
    protocol = importlib.import_module(package + '.protocol')
    path = log_path(env.home)
    path.parent.mkdir(parents=True)
    known = [path, path.with_name(path.name + '.1'), path.with_name(path.name + '.2')]
    old = (json.dumps({'synthetic_old': 'x' * 1600}) + '\n').encode('utf-8')
    for target in known:
        target.write_bytes(old)
    foreign = path.with_name('reviews.jsonl.backup')
    foreign.write_bytes(b'foreign-name-sentinel')
    review = protocol.unavailable('x' * 4000 if oversized else 'Синтетический отказ')
    logger.write_review(env.home, 'metadata-owner', review, max_bytes=limit, keep=keep)
    sizes = [target.stat().st_size for target in known if target.exists()]
    assert len(sizes) <= keep and all(size <= limit for size in sizes)
    assert foreign.read_bytes() == b'foreign-name-sentinel'
    if not oversized:
        assert json.loads(path.read_text(encoding='utf-8').splitlines()[-1])['model'] is None


@pytest.mark.parametrize('scenario', ['default'])
@pytest.mark.parametrize('keep', [1, 2])
def test_retention_shrink_without_rotation(publication_env, keep):
    import importlib
    env = publication_env
    package = type(env.runtime).__module__
    logger = importlib.import_module(package + '.review_log')
    protocol = importlib.import_module(package + '.protocol')
    path = log_path(env.home)
    path.parent.mkdir(parents=True)
    for name in ('reviews.jsonl', 'reviews.jsonl.1', 'reviews.jsonl.2'):
        path.with_name(name).write_bytes(b'{}\n')
    logger.write_review(env.home, 'metadata-owner', protocol.unavailable('Отказ'), max_bytes=2048, keep=keep)
    assert path.read_bytes().startswith(b'{}\n')
    assert len(list(path.parent.glob('reviews.jsonl*'))) == keep


@pytest.mark.parametrize('scenario', ['default'])
@pytest.mark.parametrize('name', ['reviews.jsonl', 'reviews.jsonl.1', 'reviews.jsonl.2'])
def test_symlinked_known_file_refuses_all_mutation(publication_env, tmp_path, name):
    import importlib
    env = publication_env
    package = type(env.runtime).__module__
    logger = importlib.import_module(package + '.review_log')
    protocol = importlib.import_module(package + '.protocol')
    path = log_path(env.home)
    path.parent.mkdir(parents=True)
    known = [path.with_name(n) for n in ('reviews.jsonl', 'reviews.jsonl.1', 'reviews.jsonl.2')]
    for target in known:
        target.write_bytes(b'owned-sentinel\n')
    foreign = tmp_path / 'foreign-file'
    foreign.write_bytes(b'foreign-sentinel\n')
    link = path.with_name(name)
    link.unlink()
    try:
        link.symlink_to(foreign)
    except OSError as error:
        pytest.skip('Windows не разрешил создать symlink: ' + str(error.winerror))
    before = {target: target.read_bytes() for target in known}
    logger.write_review(env.home, 'metadata-owner', protocol.unavailable('Отказ'), max_bytes=1024, keep=1)
    assert link.is_symlink() and {target: target.read_bytes() for target in known} == before
    assert foreign.read_bytes() == b'foreign-sentinel\n'


@pytest.mark.parametrize('scenario', ['default'])
@pytest.mark.parametrize('nested', [False, True])
def test_symlinked_owner_ancestor_refuses_foreign_profile(publication_env, tmp_path, nested):
    import importlib
    env = publication_env
    package = type(env.runtime).__module__
    logger = importlib.import_module(package + '.review_log')
    protocol = importlib.import_module(package + '.protocol')
    foreign = tmp_path / 'foreign-owner'
    foreign.mkdir()
    link = env.home / 'owner-link'
    try:
        link.symlink_to(foreign, target_is_directory=True)
    except OSError as error:
        pytest.skip('Windows не разрешил создать symlink: ' + str(error.winerror))
    real_home = foreign / 'nested' if nested else foreign
    home = link / 'nested' if nested else link
    path = log_path(real_home)
    path.parent.mkdir(parents=True)
    known = [path.with_name(n) for n in ('reviews.jsonl', 'reviews.jsonl.1', 'reviews.jsonl.2')]
    for target in known:
        target.write_bytes(b'foreign-profile-sentinel\n')
    before = {target: target.read_bytes() for target in known}
    logger.write_review(home, 'metadata-owner', protocol.unavailable('Отказ'), max_bytes=1024, keep=1)
    assert {target: target.read_bytes() for target in known} == before and link.is_symlink()


@pytest.mark.parametrize('scenario', ['default'])
def test_disabled_logging_does_not_prune_inherited_files(publication_env):
    env = publication_env
    config_path = env.home / 'config.yaml'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    settings = config['plugins']['entries']['pplx-decider-review']['settings']
    settings.update(log_enabled=False, log_max_bytes=256, log_keep_files=1)
    config_path.write_text(json.dumps(config), encoding='utf-8')
    assert env.ctx.get_config('log_enabled', True) is False
    path = log_path(env.home)
    path.parent.mkdir(parents=True)
    known = [path.with_name(n) for n in ('reviews.jsonl', 'reviews.jsonl.1', 'reviews.jsonl.2')]
    for target in known:
        target.write_bytes(b'inherited-sentinel' * 100)
    before = {target: target.read_bytes() for target in known}
    env.runtime.cached_review('metadata-owner', 'Проверить отключённый журнал', {'summary': 'Синтетический результат'}, main=True)
    assert {target: target.read_bytes() for target in known} == before
