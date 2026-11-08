import errno
import time
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app import model_import
from test_model_pack import fixture_manifest
from app.model_pack import parse_manifest


@pytest.fixture
def importer(tmp_path, monkeypatch):
    pack = parse_manifest(fixture_manifest(), '0.4.0')
    monkeypatch.setattr(model_import, 'load_candidate', lambda _: pack)
    active = tmp_path / 'active'
    active.mkdir()
    (active / 'model.bin').write_bytes(b'in-use')
    monkeypatch.setenv('MEETING_MODELS', str(active))
    source = tmp_path / 'source/models'
    (source / 'test').mkdir(parents=True)
    (source / 'test/model.bin').write_bytes(b'good')
    (source / 'ignored.sh').write_text('extra')
    data = tmp_path / 'data'
    with TestClient(create_app(data, start_worker=False)) as client:
        yield client, source, data
    assert list(active.iterdir()) == [active / 'model.bin']
    assert (active / 'model.bin').read_bytes() == b'in-use'


URL = '/api/runtime/model-import'


def start(client, source):
    response = client.post(URL, json={'directory': str(source)})
    assert response.status_code == 202, response.text


def complete(client):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        result = client.get(URL).json()
        if result['phase'] not in ('copying', 'validating', 'downloading'):
            return result
        time.sleep(.01)
    pytest.fail('import did not finish')


def test_import_copies_verifies_and_reuses_stage(importer):
    client, source, data = importer
    assert client.get(URL).json()['total_bytes'] == 4
    start(client, source)
    first = complete(client)
    assert first['phase'] == 'ready' and first['copied_bytes'] == 4
    start(client, source.parent)
    second = complete(client)
    assert second['phase'] == 'ready' and second['reused']
    stages = list((data / 'model-imports').iterdir())
    assert len(stages) == 1
    assert (stages[0] / 'models/test/model.bin').read_bytes() == b'good'
    assert not (stages[0] / 'models/ignored.sh').exists()


@pytest.mark.parametrize('scenario,expected', [
    ('preflight', 'no_space'), ('write', 'no_space'), ('placeholder', 'cloud_placeholder'),
    ('bad_hash', 'hash_mismatch'), ('link', 'unsafe_entry'),
])
def test_failed_import_leaves_no_stage(importer, monkeypatch, scenario, expected):
    client, source, data = importer
    path = source / 'test/model.bin'
    if scenario == 'preflight':
        monkeypatch.setattr(model_import.shutil, 'disk_usage', lambda _: SimpleNamespace(free=3))
    elif scenario == 'write':
        def no_space(*args):
            raise OSError(errno.ENOSPC, 'disk full')
        monkeypatch.setattr(model_import.os, 'fsync', no_space)
    elif scenario == 'placeholder':
        monkeypatch.setattr(model_import, 'is_cloud_placeholder', lambda _: True)
    elif scenario == 'link':
        path.unlink()
        path.symlink_to(data.parent / 'active/model.bin')
    else:
        path.write_bytes(b'evil')
    start(client, source)
    result = complete(client)
    assert result['phase'] == 'failed' and result['error_code'] == expected
    assert not list((data / 'model-imports').glob('*'))


def test_cancel_cleans_partial_stage_and_allows_retry(importer, monkeypatch):
    client, source, data = importer
    copying, release = Event(), Event()
    original = model_import.DirectoryImport.copy_file

    def wait_for_cancel(*args):
        original(*args)
        copying.set()
        assert release.wait(4)

    monkeypatch.setattr(model_import.DirectoryImport, 'copy_file', wait_for_cancel)
    start(client, source)
    try:
        assert copying.wait(4)
        assert client.post(URL, json={'directory': str(source)}).status_code == 409
        assert client.delete(URL).status_code == 202
    finally:
        release.set()
    assert complete(client)['phase'] == 'cancelled'
    assert not list((data / 'model-imports').glob('*'))
    start(client, source)
    assert complete(client)['phase'] == 'ready'


def make_zip(path, members):
    import zipfile
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return path


def start_archive(client, archive):
    response = client.post(URL + '/archive', json={'archive': str(archive)})
    assert response.status_code == 202, response.text


def test_archive_and_directory_produce_identical_hashes(importer):
    import hashlib
    client, source, data = importer
    archive = make_zip(data.parent / 'model.zip', [('models/test/model.bin', b'good')])
    start_archive(client, archive)
    assert complete(client)['phase'] == 'ready'
    stage, = (data / 'model-imports').iterdir()
    zip_hash = hashlib.sha256((stage / 'models/test/model.bin').read_bytes()).hexdigest()
    import shutil
    shutil.rmtree(stage)
    start(client, source)
    assert complete(client)['phase'] == 'ready'
    directory_hash = hashlib.sha256((stage / 'models/test/model.bin').read_bytes()).hexdigest()
    assert zip_hash == directory_hash == hashlib.sha256(b'good').hexdigest()


@pytest.mark.parametrize('member,error', [
    ('../escaped.bin', 'unsafe_archive_entry'),
    ('/models/escaped.bin', 'unsafe_archive_entry'),
    ('models/test/extra.bin', 'unexpected_entry'),
    ('models/test/model.bin', 'archive_size_mismatch'),
])
def test_archive_rejects_unsafe_members_and_expansion(importer, member, error):
    client, _, data = importer
    members = [(member, b'x' * 65536)]
    archive = make_zip(data.parent / 'bad.zip', members)
    start_archive(client, archive)
    result = complete(client)
    assert result['phase'] == 'failed' and result['error_code'] == error
    assert not list((data / 'model-imports').iterdir())
    assert not (data.parent / 'escaped.bin').exists()


def test_invalid_archive_is_checked_even_with_existing_stage(importer):
    client, source, data = importer
    start(client, source)
    assert complete(client)['phase'] == 'ready'
    archive = make_zip(data.parent / 'bad.zip', [('models/test/model.bin', b'evil')])
    start_archive(client, archive)
    result = complete(client)
    assert result['phase'] == 'failed' and result['error_code'] == 'hash_mismatch'
    stage, = (data / 'model-imports').iterdir()
    assert (stage / 'models/test/model.bin').read_bytes() == b'good'


def test_archive_cancel_discards_partial_stage(importer, monkeypatch):
    client, _, data = importer
    archive = make_zip(data.parent / 'model.zip', [('models/test/model.bin', b'good')])
    writing, release = Event(), Event()
    original = model_import.os.fsync

    def wait_at_write(fd):
        original(fd)
        writing.set()
        assert release.wait(4)

    monkeypatch.setattr(model_import.os, 'fsync', wait_at_write)
    start_archive(client, archive)
    try:
        assert writing.wait(4)
        assert client.delete(URL).status_code == 202
    finally:
        release.set()
    assert complete(client)['phase'] == 'cancelled'
    assert not list((data / 'model-imports').iterdir())
    start_archive(client, archive)
    assert complete(client)['phase'] == 'ready'


def test_archive_rejects_understated_expanded_size(importer):
    import struct

    client, _, data = importer
    archive = make_zip(data.parent / 'understated.zip', [('models/test/model.bin', b'x' * 65536)])
    content = bytearray(archive.read_bytes())
    central = content.index(b'PK\x01\x02')
    struct.pack_into('<I', content, central + 24, 4)
    archive.write_bytes(content)
    start_archive(client, archive)
    result = complete(client)
    assert result['phase'] == 'failed' and result['error_code'] == 'archive_size_mismatch'
    assert not list((data / 'model-imports').iterdir())


def test_archive_rejects_links_and_duplicate_paths(importer):
    import stat
    import zipfile

    client, _, data = importer
    archive = data.parent / 'bad.zip'
    member = zipfile.ZipInfo('models/test/model.bin')
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr(member, '../outside')
    start_archive(client, archive)
    assert complete(client)['error_code'] == 'unsafe_archive_entry'
    make_zip(archive, [('models/test/model.bin', b'good'), ('models/test/Model.bin', b'good')])
    start_archive(client, archive)
    assert complete(client)['error_code'] == 'duplicate_path'
    assert not list((data / 'model-imports').iterdir())


def test_truncated_archive_leaves_no_stage(importer):
    client, _, data = importer
    archive = make_zip(data.parent / 'truncated.zip', [('models/test/model.bin', b'good')])
    archive.write_bytes(archive.read_bytes()[:-10])
    start_archive(client, archive)
    result = complete(client)
    assert result['phase'] == 'failed' and result['error_code'] == 'invalid_archive'
    assert not list((data / 'model-imports').iterdir())


def test_url_download_retries_and_matches_directory_and_zip(importer, monkeypatch):
    import hashlib
    import shutil
    from app import model_download

    client, source, data = importer
    archive = make_zip(data.parent / 'model.zip', [('models/test/model.bin', b'good')])
    payload = archive.read_bytes()
    calls = 0

    def download(url, target, limit, check_cancelled, report):
        nonlocal calls
        calls += 1
        if calls == 1:
            target.write_bytes(payload[:10])
            raise model_download.ModelPackError('network_error')
        target.write_bytes(payload)
        report(len(payload), len(payload))

    monkeypatch.setattr(model_import, 'download_zip', download)
    url = {'url': 'https://models.example/model.zip'}
    assert client.post(URL + '/download', json=url).status_code == 202
    assert complete(client)['error_code'] == 'network_error'
    assert not list((data / 'model-imports').iterdir())

    def staged_hash():
        stage, = (data / 'model-imports').iterdir()
        digest = hashlib.sha256((stage / 'models/test/model.bin').read_bytes()).hexdigest()
        shutil.rmtree(stage)
        return digest

    assert client.post(URL + '/download', json=url).status_code == 202
    result = complete(client)
    assert result['phase'] == 'ready' and result['downloaded_bytes'] == len(payload)
    url_hash = staged_hash()
    start_archive(client, archive)
    assert complete(client)['phase'] == 'ready'
    zip_hash = staged_hash()
    start(client, source)
    assert complete(client)['phase'] == 'ready'
    assert staged_hash() == zip_hash == url_hash
