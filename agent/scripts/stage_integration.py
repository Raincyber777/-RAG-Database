"""Prepare sibling changes locally, then apply an explicit hash-checked manifest."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import os

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / os.environ.get('AGENT_INTEGRATION_STAGE', '.integration-stage')
if not STAGE.resolve().is_relative_to(ROOT):
    raise ValueError('Staging must stay within the Agent workspace')
REPOS = {'backend': ROOT.parent, 'frontend': ROOT.parent.parent / 'RAG-AI-system'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def prepare():
    if STAGE.exists():
        raise SystemExit('Staging already exists; refusing to overwrite work')
    manifest = {}
    for label, repo in REPOS.items():
        names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=repo).decode().split('\0')
        for name in filter(None, names):
            if '.env' in Path(name).name or 'local_settings' in name:
                continue
            source = repo / name
            target = STAGE / label / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            manifest[f'{label}/{name}'] = digest(source)
    (STAGE / 'baseline.json').write_text(json.dumps(manifest), encoding='utf-8')


def changes():
    baseline = json.loads((STAGE / 'baseline.json').read_text(encoding='utf-8'))
    selected = json.loads((STAGE / 'changes.json').read_text(encoding='utf-8'))
    items = []
    for key in selected:
        label, name = key.split('/', 1)
        target = (REPOS[label] / name).resolve()
        if not target.is_relative_to(REPOS[label].resolve()):
            raise SystemExit(f'Invalid target: {key}')
        source = STAGE / key
        expected = baseline.get(key)
        if digest(target) != expected:
            raise SystemExit(f'Source changed since staging: {key}')
        if digest(source) == expected:
            raise SystemExit(f'No change: {key}')
        items.append((source, target))
    return items


if __name__ == '__main__':
    if sys.argv[1:] == ['prepare']:
        prepare()
    elif sys.argv[1:] == ['apply']:
        items = changes()  # Validate every target before the first write.
        for source, target in items:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            print(target)
    elif sys.argv[1:] == ['check']:
        for source, target in changes():
            print(target)
    else:
        raise SystemExit('Usage: stage_integration.py prepare|check|apply')
