"""E5 ownership protocol. A PID is diagnostic only, never authority to signal or attach."""
import fcntl,hashlib,json,os,stat


def inspect(root):
    result={'protocol_version':1,'lease':'free','recording_id':None,'incarnation':None,
            'control_transport':'stdin_v1','reattach_supported':False,'decision':'saved_audio_only'}
    try:fd=os.open(root/'capture-owner.lock',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return result
    except OSError:return result|{'lease':'unknown','decision':'blocked_unknown_owner'}
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
            return result|{'lease':'unknown','decision':'blocked_unknown_owner'}
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:result['lease']='held'
        except OSError:return result|{'lease':'unknown','decision':'blocked_unknown_owner'}
        else:return result
        result['decision']='blocked_unknown_owner'
        meta=os.open(root/'capture-owner.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            info=os.fstat(meta)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>16384:return result
            value=json.loads(os.read(meta,16385))
        finally:os.close(meta)
        if not isinstance(value,dict):return result
        def token(value):return isinstance(value,str) and len(value)==32 and all(c in '0123456789abcdef' for c in value)
        fingerprint=hashlib.sha256(str(root.resolve()).encode()).hexdigest()
        if value.get('protocol_version')!=1 or value.get('workspace_fingerprint')!=fingerprint or not token(value.get('recording_id')) or not token(value.get('incarnation')) or value.get('transport')!='stdin_v1' or value.get('reattach') is not False:return result
        return result|{'recording_id':value['recording_id'],'incarnation':value['incarnation'],'decision':'controller_unavailable'}
    except (OSError,ValueError,TypeError):return result|{'lease':'unknown' if result['lease']=='free' else result['lease'],'decision':'blocked_unknown_owner'}
    finally:os.close(fd)


def blocks_publication(value,rid):
    return value['lease']!='free' and (value['recording_id'] is None or value['recording_id']==rid)
