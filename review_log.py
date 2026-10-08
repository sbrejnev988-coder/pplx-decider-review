"""Профильный журнал: только allowlist метаданных, без текста доказательств."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
import threading

_LOCK = threading.Lock()
MAX_BYTES = 1048576


def opaque_id(value):
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:16]


def write_review(home, session_id, review, max_bytes=MAX_BYTES, keep=3, *, main=False, latency_ms=0):
    """Применяет retention на попытке записи, даже если новая строка слишком велика.

    log_enabled=False в runtime не вызывает эту функцию и не прочищает старые файлы.
    """
    # ID сохраняется только в фактически наблюдённом безопасном формате OpenRouter.
    response_id = review.get('request_id')
    response_id = response_id if isinstance(response_id, str) and re.fullmatch(r'gen-dec-\d{9,13}-[A-Za-z0-9]{8,40}', response_id) else None
    probabilities = {k: {'noul': a['noul']} if a['type'] == 'noul' else {f: a[f] for f in ('confidence', 'probabilities')}
                     for k, a in review.get('answers', {}).items()}
    record = {'timestamp': datetime.now(timezone.utc).isoformat(), 'target_type': 'main' if main else 'subagent',
              'profile_id': opaque_id(home), 'session_id': opaque_id(session_id), 'requested_model': review['requested_model'],
              'model': review.get('model'), 'request_id': response_id,
              'policy_decision': review['verdict'], 'latency_ms': latency_ms,
              'error_category': None if review['verified'] else 'review_unavailable',
              'verdict': review['verdict'], 'verified': review['verified'], 'probabilities': probabilities,
              'errors': review['errors'], 'reason': review['reason']}
    encoded = (json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    limit = max_bytes if type(max_bytes) is int and 256 <= max_bytes <= MAX_BYTES else MAX_BYTES
    copies = keep if type(keep) is int and 1 <= keep <= 3 else 3
    root = Path(home)
    # Новый ID пишет только в свой корень; прежние журналы не мигрируются.
    folder = root / 'plugin-data' / 'decision-review'
    path = folder / 'reviews.jsonl'
    try:
        with _LOCK:
            # Не пишем через перенаправление или symlink в чужой профиль/файл.
            known = [path, path.with_name(path.name + '.1'), path.with_name(path.name + '.2')]
            # Проверяем все известные файлы и предков до любого pruning/rotation.
            # Windows junction/reparse point также не является нашим обычным путём.
            for target in (*folder.parents, folder, *known):
                if target.is_symlink():
                    return
                try:
                    status = target.lstat()
                except FileNotFoundError:
                    continue
                if getattr(status, 'st_file_attributes', 0) & 0x400:
                    return
            if any(target.exists() and not target.is_file() for target in known):
                return
            folder.mkdir(parents=True, exist_ok=True)
            # keep — общее число файлов, включая current. Старые oversized файлы
            # удаляем целиком; чужие имена не сканируем и не изменяем.
            for i, target in enumerate(known):
                if target.exists() and (i >= copies or target.stat().st_size > limit):
                    target.unlink()
            if len(encoded) > limit:
                return
            if path.exists() and path.stat().st_size + len(encoded) > limit:
                for i in range(copies - 1, 0, -1):
                    target = path.with_name(path.name + '.' + str(i))
                    source = path if i == 1 else path.with_name(path.name + '.' + str(i - 1))
                    if target.is_symlink() or source.is_symlink():
                        return
                    if source.exists():
                        source.replace(target)
                if copies == 1:
                    path.unlink()
            with path.open('ab') as stream:
                stream.write(encoded)
    except OSError:
        # Ошибка журналирования не меняет результат native инструмента или reviewer.
        return
