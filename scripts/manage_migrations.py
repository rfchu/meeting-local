"""Explicit offline upgrade CLI; never imports app.main or starts model workers."""
import argparse,json,os,sqlite3,subprocess,time,uuid
from pathlib import Path
from app.paths import RuntimePaths
from app.backups import BackupError,write_json
from app.migrations import inspect_database,MigrationError
from app.upgrades import migration_drill,upgrade_data
ROOT=Path(__file__).resolve().parents[1]

def main():
    paths=RuntimePaths.from_env(root=ROOT)
    parser=argparse.ArgumentParser(description='听记版本化迁移：先备份和临时恢复预演，再原子升级')
    actions=parser.add_subparsers(dest='action',required=True)
    for name in ('status','upgrade'):
        child=actions.add_parser(name);child.add_argument('--data',default=str(paths.data))
        if name=='upgrade':child.add_argument('--backup-output')
    drill=actions.add_parser('drill');drill.add_argument('backup');drill.add_argument('--report')
    args=parser.parse_args()
    try:
        if args.action=='status':result=inspect_database(Path(args.data)/'meetings.sqlite3')
        elif args.action=='drill':
            result=migration_drill(args.backup)
            if args.report:write_json(Path(args.report),result)
        else:
            output=args.backup_output or paths.backups/('pre-upgrade-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
            revision=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,timeout=3)
            dirty=subprocess.run(['git','status','--porcelain'],cwd=ROOT,capture_output=True,text=True,timeout=3)
            version=revision.stdout.strip()+('+working-tree' if dirty.stdout else '')
            result=upgrade_data(args.data,output,version)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (OSError,ValueError,RuntimeError,sqlite3.Error,subprocess.SubprocessError) as error:
        message=str(error) if isinstance(error,(BackupError,MigrationError,RuntimeError)) else '迁移未完成，请核对权限、磁盘空间和数据库状态；已有备份不会自动删除'
        parser.exit(1,message+'\n')

if __name__=='__main__':main()
