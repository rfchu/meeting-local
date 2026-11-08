import hashlib
from pathlib import Path

import pytest

from app.model_pack import ModelPackError, load_candidate, parse_manifest, verify_directory


def fixture_manifest():
    return {
        'schema_version': 1, 'pack_version': '0.1.0',
        'app_versions': {'min': '0.4.0', 'before': '0.5.0'},
        'components': [{
            'capability': 'transcription',
            'source': 'https://example.com/model', 'license_evidence': 'https://example.com/license',
            'files': [{'path': 'models/test/model.bin', 'bytes': 4,
                       'sha256': hashlib.sha256(b'good').hexdigest()}],
        }],
    }


@pytest.fixture
def staged(tmp_path):
    file = tmp_path / 'models/test/model.bin'
    file.parent.mkdir(parents=True)
    file.write_bytes(b'good')
    return tmp_path, file


@pytest.mark.parametrize(('change', 'error'), [
    ('missing', 'missing'), ('size', 'size_mismatch'), ('hash', 'hash_mismatch'),
    ('file_link', 'unsafe_entry'), ('parent_link', 'unsafe_entry'),
    ('extra', 'unexpected_entry'), ('directory', 'unsafe_entry'),
    ('hardlink', 'unsafe_entry'),
])
def test_staged_directory_rejects_bad_payload(staged, change, error):
    root, file = staged
    if change == 'missing':
        file.unlink()
    elif change in ('size', 'hash'):
        file.write_bytes(b'longer' if change == 'size' else b'evil')
    elif change == 'directory':
        file.unlink()
        file.mkdir()
    elif change == 'hardlink':
        (root.parent / 'outside-hardlink').hardlink_to(file)
    elif change == 'extra':
        (root / 'models/run.sh').write_text('#!/bin/sh')
    else:
        target = file if change == 'file_link' else file.parent
        outside = root.parent / ('outside-' + change)
        target.rename(outside)
        target.symlink_to(outside)
    with pytest.raises(ModelPackError) as exc:
        verify_directory(root, parse_manifest(fixture_manifest(), '0.4.0'))
    assert exc.value.code == error


@pytest.mark.parametrize('path', ['/models/a.bin', 'models/../a.bin', 'models//a.bin',
                                'models/a\\b.bin', 'models/./a.bin', 'models/a.py'])
def test_manifest_rejects_unsafe_paths(path):
    raw = fixture_manifest()
    raw['components'][0]['files'][0]['path'] = path
    with pytest.raises(ModelPackError):
        parse_manifest(raw, '0.4.0')


@pytest.mark.parametrize('alias', ['models/test/model.bin', 'models/Test/Model.bin',
                                 'models/test/model.bin/child.bin'])
def test_manifest_rejects_path_collisions(alias):
    raw = fixture_manifest()
    files = raw['components'][0]['files']
    files.append(dict(files[0], path=alias))
    with pytest.raises(ModelPackError):
        parse_manifest(raw, '0.4.0')


def test_verifies_valid_package_and_rejects_incompatible_app(staged):
    root, _ = staged
    pack = parse_manifest(fixture_manifest(), '0.4.9')
    assert verify_directory(root, pack) == 4
    with pytest.raises(ModelPackError) as exc:
        parse_manifest(fixture_manifest(), '0.5.0')
    assert exc.value.code == 'incompatible_app'


def test_candidate_preserves_all_pinned_files():
    import json
    root = Path(__file__).resolve().parents[1]
    old = json.loads((root / 'models-manifest.json').read_text())['files']
    qwen = json.loads((root / 'qwen-models-manifest.json').read_text())['models']
    expected = {e['file']: (e['bytes'], e['sha256']) for e in old}
    for model in qwen:
        for e in model['files']:
            expected[f"models/{model['directory']}/{e['name']}"] = (e['bytes'], e['sha256'])
    pack = load_candidate('0.4.0')
    assert {str(e.path): (e.size, e.sha256) for e in pack.files} == expected


def test_dataless_file_is_rejected_before_hashing(staged, monkeypatch):
    from types import SimpleNamespace

    from app import model_pack

    if model_pack.sys.platform != 'darwin':
        pytest.skip('Darwin file flags')
    root, _ = staged
    original = model_pack.os.fstat

    def dataless(fd):
        info = original(fd)
        return SimpleNamespace(st_mode=info.st_mode, st_nlink=info.st_nlink,
                               st_size=info.st_size, st_flags=0x40000000)

    monkeypatch.setattr(model_pack.os, 'fstat', dataless)
    with pytest.raises(ModelPackError) as exc:
        verify_directory(root, parse_manifest(fixture_manifest(), '0.4.0'))
    assert exc.value.code == 'cloud_placeholder'
