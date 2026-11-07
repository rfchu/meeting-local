"""Read-only local delivery inventory; does not install, record, infer or notarize."""
import hashlib,json,platform,plistlib,sqlite3,subprocess
from pathlib import Path


def source_digest(root):
    digest=hashlib.sha256()
    for path in sorted([*root.glob('app/**/*.py'),*root.glob('requirements*.txt')]):
        digest.update(str(path.relative_to(root)).encode());digest.update(path.read_bytes())
    return digest.hexdigest()


def inspect_bundle(path,kind,root):
    value={'component':kind,'present':path.exists(),'verified':False,'version':None,'signature':'unknown','source_bound':kind=='host','reason':None}
    if not path.exists():return value|{'reason':'component_missing'}
    try:
        if path.is_symlink():raise ValueError('bundle_link')
        # Do not allow platform tools to follow links hidden within the bundle.
        if any(p.is_symlink() for p in path.rglob('*')):raise ValueError('bundle_link')
        info=plistlib.loads((path/'Contents/Info.plist').read_bytes());name='TingjiHost' if kind=='host' else 'TingjiCapture'
        expected='cn.seeknow.tingji.host' if kind=='host' else 'cn.seeknow.tingji.capture'
        if info.get('CFBundleIdentifier')!=expected or info.get('CFBundleExecutable')!=name:raise ValueError('bundle_identity')
        value['version']=info.get('CFBundleShortVersionString',info.get('CFBundleVersion'))
        subprocess.run(['codesign','--verify','--deep','--strict',str(path)],check=True,capture_output=True,timeout=10)
        if 'arm64' not in subprocess.check_output(['lipo','-archs',str(path/'Contents/MacOS'/name)],text=True,timeout=5).split():raise ValueError('architecture')
        signature=subprocess.run(['codesign','-d','--verbose=4',str(path)],capture_output=True,text=True,check=True,timeout=5)
        details=signature.stdout+signature.stderr
        value['signature']='adhoc' if 'Signature=adhoc' in details else 'signed_notarization_unverified'
        if kind=='host':
            manifest=json.loads((path/'Contents/Resources/delivery.json').read_text());config=json.loads((path/'Contents/Resources/host-config.json').read_text())
            if manifest.get('format')!=1 or manifest.get('protocol_version')!=1 or 22 not in manifest.get('schema_versions',[]):raise ValueError('manifest_incompatible')
            if manifest.get('source_dirty') is not False:raise ValueError('unclean_build')
            if config.get('version')!=1 or type(config.get('port')) is not int or not 1024<=config['port']<=65535:raise ValueError('configuration')
            if manifest.get('runtime_digest')!=source_digest(root) or Path(config.get('projectRoot','')).resolve()!=root.resolve():raise ValueError('source_mismatch')
            value['source_commit']=manifest.get('source_commit');value['source_clean_at_build']=manifest.get('source_dirty') is False
            value['python_present']=(root/'.venv/bin/python').is_file()
            if not value['python_present']:raise ValueError('python_missing')
        elif int(info.get('CFBundleVersion','0'))<4:raise ValueError('helper_upgrade_required')
        value['verified']=True
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError):
        value['reason']='local_component_requires_check'
    return value


def inspect_release(root,acceptance=None,cache=None):
    root=Path(root);cache=Path(cache) if cache is not None else Path.home()/'Library/Caches/cn.seeknow.tingji'
    components=[inspect_bundle(cache/'Tingji.app','host',root),inspect_bundle(cache/'TingjiCapture.app','capture',root)] if platform.system()=='Darwin' else []
    hardware=[];evidence_available=acceptance is not None
    try:hardware=acceptance.inventory() if acceptance else []
    except (OSError,ValueError,RuntimeError,sqlite3.Error):evidence_available=False
    from .recording_acceptance import CASES
    hardware_complete=all(any(item.get('case')==case and item.get('state')=='passed' for item in hardware) for case in CASES)
    return {'schema_version':1,'passive':True,'capture_started':False,'models_started':False,'permissions_requested':False,
        'local_ready':len(components)==2 and all(c['verified'] for c in components),'components':components,
        'distribution':'local_adhoc_source_bound','runtime_scope':'bundled_defaults_not_saved_connections','external_release_ready':False,
        'hardware':hardware,'hardware_evidence_available':evidence_available,'hardware_review_complete':hardware_complete,
        'boundaries':[
            {'id':'standalone_runtime','state':'pending','label':'当前宿主依赖本机源码、Python 环境和模型配置，尚非独立安装包'},
            {'id':'notarization','state':'pending','label':'当前本机交付未完成 Developer ID 发行签名、公证与干净机器安装验收'},
            {'id':'hardware','state':'reviewed' if hardware_complete else 'pending','label':'真实设备验收须逐项回听核对；短测不能替代长录音、睡眠和识别恢复'},
        ]}
