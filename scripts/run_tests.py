"""Переносимый offline pytest runner: synthetic home и запрет сети.

Unit mode не импортирует Hermes SDK. --core явно включает native config tests
и требует совместимых зависимостей текущего Hermes в выбранном Python.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import xml.etree.ElementTree as ET


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', type=Path, help='Проверенный checkout Hermes для native config regression')
    parser.add_argument('--scratch', type=Path, help='Родительский каталог только для нового synthetic test root')
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    scratch_parent = options.scratch or Path(os.environ.get('TMPDIR') or tempfile.gettempdir())
    scratch_parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='decision-review-tests-', dir=str(scratch_parent))).resolve()
    home, temp, user = scratch / 'home', scratch / 'tmp', scratch / 'user'
    for path in (home, temp, user, scratch / 'local', scratch / 'roaming'):
        path.mkdir()
    platform = {k: v for k, v in os.environ.items() if k.upper() in {
        'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT'}}
    os.environ.clear()
    os.environ.update(platform)
    os.environ.update({
        'HERMES_HOME': str(home), 'HOME': str(user), 'USERPROFILE': str(user),
        'LOCALAPPDATA': str(scratch / 'local'), 'APPDATA': str(scratch / 'roaming'),
        'TEMP': str(temp), 'TMP': str(temp), 'TMPDIR': str(temp),
        'PYTHON_DOTENV_DISABLED': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
        'PPLX_TEST_PRODUCTION_ROOT': str(root),
    })
    tempfile.tempdir = str(temp)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root / 'tests'))
    native = options.core is not None
    if native:
        core = options.core.resolve()
        if not (core / 'hermes_cli/plugins.py').is_file():
            parser.error('--core должен указывать на настоящий Hermes checkout')
        os.environ['PPLX_TEST_NATIVE_CORE'] = str(core)
        sys.path.insert(0, str(core))
    denied_network = []

    def audit(event, args):
        if event in {'socket.connect', 'socket.bind', 'socket.getaddrinfo',
                     'socket.gethostbyname', 'socket.gethostbyaddr', 'socket.sendto'}:
            denied_network.append(event)
            raise PermissionError('Сеть запрещена offline test runner')
        if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0]))
            if path.name.lower() in {'.env', 'auth.json'}:
                raise PermissionError('Credential-file I/O запрещён offline runner')

    sys.addaudithook(audit)
    ini, junit, log = scratch / 'pytest.ini', scratch / 'junit.xml', scratch / 'pytest.log'
    ini.write_text('[pytest]\n', encoding='utf-8')
    args = ['-c', str(ini), '--rootdir=' + str(root), '--import-mode=importlib',
            '--confcutdir=' + str(root / 'tests'),
            '-p', 'no:cacheprovider', '--capture=sys', '-o', 'junit_family=xunit1', '-o', 'log_file=' + str(log),
            '--basetemp=' + str(temp / 'pytest'), '--junitxml=' + str(junit),
            '-q', '--tb=short', str(root / 'tests')]
    if not native:
        args.extend(['--ignore=' + str(root / 'tests/test_native_config.py')])
    import pytest
    code = int(pytest.main(args))
    cases = list(ET.parse(junit).iter('testcase')) if junit.is_file() else []
    counts = {name: sum(case.find(tag) is not None for case in cases)
              for name, tag in [('failures', 'failure'), ('errors', 'error'), ('skipped', 'skipped')]}
    summary = {'mode': 'native SDK config + offline suite' if native else 'offline unit, no native SDK claim',
               'exit_code': code, 'tests': len(cases), **counts,
               'network_attempts_denied': len(denied_network), 'junit': str(junit),
               'synthetic_root_retained': str(scratch)}
    (scratch / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))
    if not cases or counts['failures'] or counts['errors']:
        return code or 1
    return code


if __name__ == '__main__':
    raise SystemExit(main())
