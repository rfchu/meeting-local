"""E4 delivery transactions use disposable bundles and inject compilation/signature boundaries."""
import fcntl
import json
import os
import plistlib
from pathlib import Path
from unittest.mock import patch
import pytest
from scripts import host_delivery as h


def bundle(path,content='old'):
    (path/'Contents/MacOS').mkdir(parents=True)
    (path/'Contents/MacOS/TingjiHost').write_text(content)
    with (path/'Contents/Info.plist').open('wb') as f:plistlib.dump({'CFBundleIdentifier':h.BUNDLE_ID,'CFBundleExecutable':'TingjiHost','CFBundleShortVersionString':'0.4.0'},f)
    return path


@pytest.fixture
def delivery(tmp_path,monkeypatch):
    monkeypatch.setenv('TINGJI_HOST_STATE_DIR',str(tmp_path/'state'))
    monkeypatch.setattr(h,'DEFAULT_STATE',tmp_path/'default-state')
    target=bundle(tmp_path/'Tingji.app');d=h.Delivery(target)
    monkeypatch.setattr(h,'verify_bundle',h.bundle_info)
    monkeypatch.setattr(h,'runtime_check',lambda app:{'synthetic':True})
    monkeypatch.setattr(h,'reject_running',lambda app:None)
    def compile_run(command,**kwargs):bundle(Path(kwargs['env']['TINGJI_HOST_COMPILE_APP']),'new')
    monkeypatch.setattr(h.subprocess,'run',compile_run)
    return d


def test_upgrade_retains_old_and_rollback_preserves_settings_and_external_files(delivery,tmp_path):
    d=delivery
    for name in ('data','models','state'):(tmp_path/name).mkdir(exist_ok=True,mode=0o700)
    for name in ('data/meeting.wav','models/weights','state/connection.json'):(tmp_path/name).write_text('keep')
    first=d.apply();assert first['rollback_available'] and not first['service_restarted']
    assert (d.target/'Contents/MacOS/TingjiHost').read_text()=='new'
    assert (d.stage_path(first['release'])/'Contents/MacOS/TingjiHost').read_text()=='old'
    second=d.apply(first['release'])
    assert second['state']=='published' and (d.target/'Contents/MacOS/TingjiHost').read_text()=='old'
    for name in ('data/meeting.wav','models/weights','state/connection.json'):assert (tmp_path/name).read_text()=='keep'
    status=d.status();assert status['pending_transaction'] is False
    history={item['id']:item for item in status['history']}
    assert not history[first['release']]['rollback_available']
    assert history[second['release']]['rollback_available']


def test_first_install_no_rollback(delivery):
    import shutil
    shutil.rmtree(delivery.target)
    result=delivery.apply();assert not result['rollback_available'] and delivery.target.is_dir()


def test_live_host_lease_rejects_before_compile(delivery,tmp_path,monkeypatch):
    state=tmp_path/'state';h.private_folder(state)
    with (state/'host.lock').open('w') as f:
        os.chmod(state/'host.lock',0o600)
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(h.DeliveryError,match='旧宿主'):delivery.apply()
    assert (delivery.target/'Contents/MacOS/TingjiHost').read_text()=='old'


def test_delivery_lease_rejects_competitor(delivery):
    with h.lease(delivery.history,'delivery.lock'):
        with pytest.raises(h.DeliveryError):delivery.apply()


@pytest.mark.parametrize('phase',['compile','verify','runtime','exchange'])
def test_prepublication_failures_keep_old_app(delivery,monkeypatch,phase):
    def fail(*args,**kwargs):raise h.DeliveryError('injected')
    if phase=='compile':monkeypatch.setattr(h.subprocess,'run',fail)
    elif phase=='verify':
        original=h.verify_bundle
        monkeypatch.setattr(h,'verify_bundle',lambda p:fail() if p!=delivery.target else original(p))
    elif phase=='runtime':monkeypatch.setattr(h,'runtime_check',fail)
    else:monkeypatch.setattr(h,'exchange',fail)
    with pytest.raises(h.DeliveryError):delivery.apply()
    assert (delivery.target/'Contents/MacOS/TingjiHost').read_text()=='old'
    if phase=='exchange':assert delivery.recover()['state']=='aborted'


@pytest.mark.parametrize('published',[False,True])
def test_interrupted_prepared_record_is_reconciled_without_replacing_files(delivery,published):
    d=delivery;h.private_folder(d.history);release='a'*32;stage=d.stage_path(release);bundle(stage,'new')
    record={'id':release,'state':'prepared','old_digest':h.digest_tree(d.target),'new_digest':h.digest_tree(stage)}
    h.write_json(d.journal,record)
    if published:h.exchange(stage,d.target)
    before=h.digest_tree(d.target)
    assert d.recover()['state']==('published' if published else 'aborted')
    assert h.digest_tree(d.target)==before


def test_incomplete_journal_blocks_new_build_and_unknown_changes_do_not_recover(delivery):
    d=delivery;h.private_folder(d.history)
    h.write_json(d.journal,{'id':'b'*32,'state':'prepared','old_digest':'old','new_digest':'new'})
    with pytest.raises(h.DeliveryError,match='未完成'):d.apply()
    with pytest.raises(h.DeliveryError,match='不一致'):d.recover()


def test_changed_archive_or_active_target_rejects_rollback(delivery):
    first=delivery.apply();archived=delivery.stage_path(first['release'])/'Contents/MacOS/TingjiHost'
    archived.write_text('tampered')
    with pytest.raises(h.DeliveryError,match='旧版本'):delivery.apply(first['release'])
    archived.write_text('old');(delivery.target/'Contents/MacOS/TingjiHost').write_text('changed')
    with pytest.raises(h.DeliveryError,match='当前应用'):delivery.apply(first['release'])


@pytest.mark.parametrize('value',['../escape','a'*31,'A'*32,''])
def test_release_id_cannot_escape_history(delivery,value):
    with pytest.raises(h.DeliveryError):delivery.stage_path(value)


def test_symlink_target_archive_and_lock_are_rejected(tmp_path,delivery):
    link=tmp_path/'else'/'Tingji.app';link.parent.mkdir();link.symlink_to(delivery.target)
    with pytest.raises(h.DeliveryError):h.Delivery(link)
    h.private_folder(delivery.history)
    (delivery.history/('c'*32)).symlink_to(tmp_path)
    with pytest.raises(h.DeliveryError):delivery.stage_path('c'*32)
    (delivery.history/'evil.lock').symlink_to(tmp_path/'victim')
    with pytest.raises(OSError):
        with h.lease(delivery.history,'evil.lock'):pass


def test_running_process_scan_accounts_for_space_paths_and_alias(tmp_path,monkeypatch):
    app=tmp_path/'space dir'/'Tingji.app'
    monkeypatch.setattr(h.subprocess,'check_output',lambda *a,**kw:str(app/'Contents/MacOS/TingjiHost')+' --probe\n')
    with pytest.raises(h.DeliveryError,match='仍在运行'):h.reject_running(app)


def test_runtime_preflight_read_only_missing_python_and_schema(tmp_path,monkeypatch):
    app=bundle(tmp_path/'Tingji.app');resource=app/'Contents/Resources';resource.mkdir()
    config={'version':1,'port':9876,'projectRoot':str(tmp_path/'repo'),'dataRoot':str(tmp_path/'data'),'backupsRoot':str(tmp_path/'backups')}
    h.write_json(resource/'host-config.json',config)
    with pytest.raises(h.DeliveryError,match='Python 环境'):h.runtime_check(app)
    repo=Path(config['projectRoot']);(repo/'.venv/bin').mkdir(parents=True);(repo/'.venv/bin/python').touch();(repo/'scripts').mkdir();(repo/'scripts/manage_service.py').touch()
    monkeypatch.setattr(h.subprocess,'check_output',lambda *a,**kw:json.dumps({'python':[3,13],'dependencies':True}))
    assert h.runtime_check(app)['schema_version'] is None
    assert not Path(config['dataRoot']).exists()
    import sqlite3
    Path(config['dataRoot']).mkdir();dbpath=Path(config['dataRoot'])/'meetings.sqlite3'
    with sqlite3.connect(dbpath) as db:db.execute('PRAGMA user_version=99')
    before=dbpath.read_bytes()
    with pytest.raises(h.DeliveryError,match='数据库版本'):h.runtime_check(app)
    assert dbpath.read_bytes()==before


def test_manifest_source_mismatch_is_rejected(tmp_path,monkeypatch):
    app=bundle(tmp_path/'Tingji.app');resource=app/'Contents/Resources';resource.mkdir()
    repo=tmp_path/'repo';(repo/'.venv/bin').mkdir(parents=True);(repo/'.venv/bin/python').touch();(repo/'scripts').mkdir();(repo/'scripts/manage_service.py').touch()
    h.write_json(resource/'host-config.json',{'version':1,'port':9876,'projectRoot':str(repo),'dataRoot':str(tmp_path/'absent'),'backupsRoot':str(tmp_path/'backups')})
    h.write_json(resource/'delivery.json',{'format':1,'protocol_version':1,'runtime_digest':'wrong','schema_versions':[22]})
    monkeypatch.setattr(h.subprocess,'check_output',lambda *a,**kw:json.dumps({'python':[3,13],'dependencies':True}))
    with pytest.raises(h.DeliveryError,match='源码运行环境'):h.runtime_check(app)


def test_status_absent_is_read_only(tmp_path):
    target=tmp_path/'absent'/'Tingji.app';assert h.Delivery(target).status()['target_exists'] is False
    assert not target.parent.exists()


def test_post_exchange_record_failure_can_recover(delivery,monkeypatch):
    original=h.write_json
    def interrupted(path,value):
        if path.name=='release.json':raise OSError('injected after exchange')
        return original(path,value)
    monkeypatch.setattr(h,'write_json',interrupted)
    with pytest.raises(OSError):delivery.apply()
    assert (delivery.target/'Contents/MacOS/TingjiHost').read_text()=='new'
    monkeypatch.setattr(h,'write_json',original)
    assert delivery.recover()['state']=='published'
    record=h.read_json(delivery.journal)
    assert (delivery.stage_path(record['id'])/'Contents/MacOS/TingjiHost').read_text()=='old'
