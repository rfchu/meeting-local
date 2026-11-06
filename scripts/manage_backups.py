"""Local-only offline backup/verify/restore drill. Does not import app.main."""
import argparse,json,os,sqlite3,subprocess,time,uuid
from pathlib import Path
from app.paths import RuntimePaths
from app.backups import create_backup,verify_backup,restore_backup,drill_backup,preflight,BackupError,write_json
ROOT=Path(__file__).resolve().parents[1]

def main():
    paths=RuntimePaths.from_env(root=ROOT)
    parser=argparse.ArgumentParser(description='听记离线一致性备份与恢复（不启动模型，不覆盖既有目录）')
    actions=parser.add_subparsers(dest='action',required=True)
    check=actions.add_parser('preflight',help='不停服检查云端占位文件与预计体积，不读取正文')
    check.add_argument('--data',default=str(paths.data))
    create=actions.add_parser('create',help='服务停止后备份数据目录')
    create.add_argument('--data',default=str(paths.data))
    create.add_argument('--output',default=None)
    for name in ('verify','drill','restore'):
        child=actions.add_parser(name);child.add_argument('backup')
        if name=='restore':child.add_argument('--target',required=True)
        if name=='drill':child.add_argument('--report',default=None)
    args=parser.parse_args()
    try:
        if args.action=='preflight':result=preflight(args.data)
        elif args.action=='create':
            output=Path(args.output or paths.backups/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]))
            revision=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,timeout=3)
            result=create_backup(args.data,output,revision.stdout.strip() if revision.returncode==0 else None)
            result['backup_directory']=str(output.resolve())
        elif args.action=='verify':result=verify_backup(args.backup)
        elif args.action=='restore':result=restore_backup(args.backup,args.target);result['restored_directory']=str(Path(args.target).resolve())
        else:
            result=drill_backup(args.backup)
            if args.report:write_json(Path(args.report),result)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if result['status']=='needs_download':parser.exit(2)
    except (OSError,ValueError,RuntimeError,sqlite3.Error,subprocess.SubprocessError) as error:
        # Do not print database content, paths embedded in OS errors, model configuration or keys.
        parser.exit(1,(str(error) if isinstance(error,(BackupError,RuntimeError)) else '备份／恢复未完成，请核对目录、权限、磁盘空间与数据状态')+'\n')

if __name__=='__main__':main()
