"""E1 read-only affinity contract for the macOS host. No capture ownership transfer."""
import hashlib,os

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
