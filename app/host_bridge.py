"""E1 read-only affinity contract for the macOS host. No capture ownership transfer."""
import hashlib,json,os,sqlite3,time

PRODUCT='cn.seeknow.tingji'
PROTOCOL_VERSION=1

def path_fingerprint(path):
    return hashlib.sha256(str(path.resolve()).encode('utf-8')).hexdigest()

def identity(paths):
    return {'product':PRODUCT,'protocol_version':PROTOCOL_VERSION,
            'project_fingerprint':path_fingerprint(paths.repository),'data_fingerprint':path_fingerprint(paths.data),
            'pid':os.getpid(),'instance_id':os.getenv('TINGJI_INSTANCE_ID'),
            'service_mode':os.getenv('TINGJI_SERVICE_MODE','foreground'),
            'workspace_path':'/','capture_ownership':{'browser':'recording_page','native':'api_service','host':'none'},
            'restart_capture_reattach':False}


def workspace_binding(paths):
    value='v1:'+path_fingerprint(paths.repository)+':'+path_fingerprint(paths.data)
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

def maintenance_busy(data):
    from .capture_ownership import inspect
    if inspect(data / 'recordings')['lease'] != 'free':
        return True
    database = data / 'meetings.sqlite3'
    if database.exists():
        from contextlib import closing
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('running','queued') LIMIT 1").fetchone():
                return True
    for path in (data / 'recordings').glob('*/session.json'):
        try:
            if time.time() - path.stat().st_mtime < 120:
                if json.loads(path.read_text()).get('state') in ('starting', 'recording', 'paused'):
                    return True
        except FileNotFoundError:
            continue
    return False
