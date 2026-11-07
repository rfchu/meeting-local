"""Disposable bundle inspection, no actual signing or device access."""
import json,plistlib,subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest
from app import release_readiness as r


def bundles(tmp_path,monkeypatch):
    root=tmp_path/'source';(root/'app').mkdir(parents=True);(root/'app/test.py').write_text('before')
    assets=root/'app/static/assets';assets.mkdir(parents=True)
    (assets/'fixture.js').write_text('export {}')
    (assets.parent/'index.html').write_text('<script src="/assets/fixture.js"></script>')
    python=root/'.venv/bin/python';python.parent.mkdir(parents=True);python.touch()
    cache=tmp_path/'cache'
    for app,name,kind in [('Tingji.app','TingjiHost','host'),('TingjiCapture.app','TingjiCapture','capture')]:
        contents=cache/app/'Contents';(contents/'MacOS').mkdir(parents=True);(contents/'MacOS'/name).touch()
        (contents/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'cn.seeknow.tingji.'+kind,'CFBundleExecutable':name,'CFBundleVersion':'4'}))
        if kind=='host':
            (contents/'Resources').mkdir()
            (contents/'Resources/host-config.json').write_text(json.dumps({'version':1,'port':8765,'projectRoot':str(root)}))
            (contents/'Resources/delivery.json').write_text(json.dumps({'format':1,'protocol_version':1,'schema_versions':[22],'runtime_digest':r.source_digest(root),'source_dirty':False,'source_commit':'known'}))
    calls=[]
    def run(args,**kw):calls.append(args);return SimpleNamespace(stdout='',stderr='Signature=adhoc')
    monkeypatch.setattr(r.subprocess,'run',run);monkeypatch.setattr(r.subprocess,'check_output',lambda *a,**kw:'arm64')
    monkeypatch.setattr(r.platform,'system',lambda:'Darwin')
    return root,cache,calls


def test_local_pass_does_not_imply_standalone_notarized_release(tmp_path,monkeypatch):
    root,cache,calls=bundles(tmp_path,monkeypatch)
    from app.recording_acceptance import CASES
    acceptance=SimpleNamespace(inventory=lambda:[{'case':case,'state':'passed'} for case in CASES])
    result=r.inspect_release(root,acceptance,cache)
    assert result['local_ready'] and result['hardware_review_complete']
    assert not result['external_release_ready'] and result['distribution']=='local_adhoc_source_bound'
    assert result['components'][0]['signature']=='adhoc'
    assert result['frontend']['verified'] and result['backend_fingerprint']
    (root/'app/static/assets/fixture.js').unlink()
    assert not r.inspect_release(root,acceptance,cache)['local_ready']
    assert calls and all(args[0]=='codesign' for args in calls)
    assert not result['capture_started'] and not result['models_started'] and not result['permissions_requested']
    assert str(root) not in json.dumps(result) and str(cache) not in json.dumps(result)


def test_missing_changed_unsigned_or_linked_bundle_is_not_ready(tmp_path,monkeypatch):
    root,cache,calls=bundles(tmp_path,monkeypatch)
    (root/'app/test.py').write_text('changed')
    assert not r.inspect_release(root,cache=cache)['local_ready']
    (cache/'TingjiCapture.app/Contents/MacOS/link').symlink_to(tmp_path)
    before=len(calls);value=r.inspect_bundle(cache/'TingjiCapture.app','capture',root)
    assert not value['verified'] and len(calls)==before
    def fail(*a,**kw):raise subprocess.CalledProcessError(1,'codesign',stderr='private secret')
    monkeypatch.setattr(r.subprocess,'run',fail)
    result=r.inspect_release(root,cache=cache)
    assert not result['local_ready'] and 'private secret' not in json.dumps(result)


def test_missing_evidence_or_unavailable_store_is_not_a_hardware_pass(tmp_path,monkeypatch):
    root,cache,_=bundles(tmp_path,monkeypatch)
    result=r.inspect_release(root,cache=cache)
    assert not result['hardware_evidence_available'] and not result['hardware_review_complete']
    import sqlite3
    def fail():raise sqlite3.OperationalError('private')
    result=r.inspect_release(root,SimpleNamespace(inventory=fail),cache)
    assert not result['hardware_evidence_available'] and not result['external_release_ready']


def test_readiness_does_not_create_missing_cache_or_runtime(tmp_path,monkeypatch):
    monkeypatch.setattr(r.platform,'system',lambda:'Darwin')
    result=r.inspect_release(tmp_path/'absent-source',cache=tmp_path/'absent-cache')
    assert not result['local_ready'] and not (tmp_path/'absent-cache').exists()


def test_partial_hardware_checklist_never_counts_as_complete(tmp_path,monkeypatch):
    root,cache,_=bundles(tmp_path,monkeypatch)
    result=r.inspect_release(root,SimpleNamespace(inventory=lambda:[{'case':'short_capture','state':'passed'}]),cache)
    assert not result['hardware_review_complete'] and not result['external_release_ready']


@pytest.mark.parametrize('field,value',[('format',2),('protocol_version',99),('schema_versions',[99]),('source_dirty',True)])
def test_dirty_or_incompatible_host_manifest_is_not_local_ready(tmp_path,monkeypatch,field,value):
    root,cache,_=bundles(tmp_path,monkeypatch)
    path=cache/'Tingji.app/Contents/Resources/delivery.json';manifest=json.loads(path.read_text());manifest[field]=value;path.write_text(json.dumps(manifest))
    assert not r.inspect_release(root,cache=cache)['local_ready']
