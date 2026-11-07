"""Transactional helper delivery. Never starts recording or asks for capture permissions."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import argparse,json,os,plistlib,subprocess
from scripts.host_delivery import Delivery,DeliveryError,digest_tree
from app.capture_binary import BinaryLease

class CaptureDelivery(Delivery):
    app_name='TingjiCapture.app'
    history_name='.tingji-capture-releases'
    def verify(self,app):
        if app.is_symlink():raise DeliveryError('采集器应用不能是符号链接')
        with (app/'Contents/Info.plist').open('rb') as f:info=plistlib.load(f)
        if info.get('CFBundleIdentifier')!='cn.seeknow.tingji.capture' or info.get('CFBundleExecutable')!='TingjiCapture':raise DeliveryError('采集器身份不匹配')
        digest_tree(app)
        subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True,capture_output=True)
        if 'arm64' not in subprocess.check_output(['lipo','-archs',str(app/'Contents/MacOS/TingjiCapture')],text=True).split():raise DeliveryError('采集器不支持 Apple Silicon')
        return info
    def runtime(self,app):
        value=json.loads(subprocess.check_output([str(app/'Contents/MacOS/TingjiCapture'),'--diagnostics-self-test'],text=True,timeout=5))
        if value.get('helper_build',0)<3:raise DeliveryError('旧采集器不支持实例协议，未发布')
        return {'helper_build':value['helper_build'],'capture_started':False,'permissions_requested':False}
    def running(self,app):
        executable=str(app/'Contents/MacOS/TingjiCapture')
        lines=subprocess.check_output(['ps','-axo','command='],text=True,timeout=5).splitlines()
        aliases={executable,executable.removeprefix('/private')}
        if any(any(line==v or line.startswith(v+' ') for v in aliases) for line in lines):raise DeliveryError('采集器仍在使用，请先结束录音／试录；未覆盖程序')
    def state_dirs(self,*args):return []
    def extra_locks(self):return [BinaryLease(self.target/'Contents/MacOS/TingjiCapture',exclusive=True)]
    def compile(self,stage):
        env=os.environ.copy();env['TINGJI_CAPTURE_COMPILE_APP']=str(stage)
        subprocess.run(['bash',str(ROOT/'scripts/compile_capture.sh')],cwd=ROOT,env=env,check=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['status','build','rollback','recover'],default='status',nargs='?')
    parser.add_argument('--target',type=Path,default=Path.home()/'Library/Caches/cn.seeknow.tingji/TingjiCapture.app');parser.add_argument('--release')
    args=parser.parse_args()
    try:
        delivery=CaptureDelivery(args.target)
        if args.action=='status':value=delivery.status()
        elif args.action=='recover':
            from scripts.host_delivery import lease
            with lease(delivery.history,'delivery.lock'):value=delivery.recover()
        else:
            if args.action=='rollback' and not args.release:raise DeliveryError('回退必须指定版本编号')
            value=delivery.apply(args.release if args.action=='rollback' else None)
        print(json.dumps(value,ensure_ascii=False,indent=2))
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:parser.exit(1,str(error)+'\n')

if __name__=='__main__':main()
