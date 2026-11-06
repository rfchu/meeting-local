"""Versioned main-database migrations; existing upgrades require the offline CLI."""
import hashlib,sqlite3,json
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from .schema_v21 import install_legacy_schema,seed_templates

LATEST_VERSION=22
LEDGER_SQL='''CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,name TEXT NOT NULL,checksum TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('applied','adopted')),
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)'''
BASELINE_CHECKSUM=hashlib.sha256(Path(__file__).with_name('schema_v21.py').read_bytes()).hexdigest()
LEDGER_CHECKSUM=hashlib.sha256(LEDGER_SQL.encode()).hexdigest()
MIGRATIONS={21:('legacy_schema_baseline',BASELINE_CHECKSUM),22:('versioned_migration_ledger',LEDGER_CHECKSUM)}

class MigrationError(ValueError):pass


def objects(db):
    return {(row[0],row[1],row[2]) for row in db.execute("SELECT type,name,tbl_name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}


def columns(db,table):
    quoted='"'+table.replace('"','""')+'"'
    return {row[1]:(row[2].upper(),row[5]) for row in db.execute('PRAGMA table_info('+quoted+')')}


@lru_cache(maxsize=1)
def baseline_spec():
    with closing(sqlite3.connect(':memory:')) as db:
        db.row_factory=sqlite3.Row;install_legacy_schema(db)
        required=objects(db)
        return required,{name:columns(db,name) for kind,name,_ in required if kind=='table'}


def validate_schema(db,version):
    required,spec=baseline_spec()
    if not required.issubset(objects(db)):raise MigrationError('数据库缺少预期表、索引或触发器，未升级；请核对备份与版本')
    for table,expected in spec.items():
        actual=columns(db,table)
        if any(actual.get(name)!=definition for name,definition in expected.items()):raise MigrationError('数据库列结构与版本记录不符，未升级')
    if version==LATEST_VERSION:validate_ledger(db)


def validate_ledger(db):
    if ('table','schema_migrations','schema_migrations') not in objects(db):raise MigrationError('数据库迁移账本缺失')
    try:rows=db.execute('SELECT version,name,checksum,mode FROM schema_migrations ORDER BY version').fetchall()
    except sqlite3.Error:raise MigrationError('数据库迁移账本结构无效') from None
    if [r[0] for r in rows]!=[21,22]:raise MigrationError('数据库迁移账本版本不连续或存在未知记录')
    for version,name,checksum,mode in rows:
        if (name,checksum)!=MIGRATIONS[version] or mode not in ('applied','adopted') or version==22 and mode!='applied':
            raise MigrationError('数据库迁移账本校验不通过，未修改数据')


def check_integrity(db):
    if [tuple(row) for row in db.execute('PRAGMA integrity_check')]!=[('ok',)]:raise MigrationError('数据库完整性检查未通过')
    if db.execute('PRAGMA foreign_key_check').fetchone() is not None:raise MigrationError('数据库外键检查未通过')


def inspect_connection(db):
    version=db.execute('PRAGMA user_version').fetchone()[0]
    if version>LATEST_VERSION:raise MigrationError('数据库版本高于当前程序，禁止降级启动或覆盖')
    if version<0:raise MigrationError('数据库版本无效')
    user_objects=objects(db)
    if version<LATEST_VERSION and any(name=='schema_migrations' for _,name,_ in user_objects):raise MigrationError('数据库版本与迁移账本冲突')
    if version in (21,22):validate_schema(db,version)
    elif user_objects and ('table','meetings','meetings') not in user_objects:raise MigrationError('数据库不是可识别的历史会议结构')
    check_integrity(db)
    return {'current_version':version,'target_version':LATEST_VERSION,'needs_upgrade':version<LATEST_VERSION,
            'pending_versions':[v for v in MIGRATIONS if v>version], 'integrity':'ok',
            'ledger':'verified' if version==LATEST_VERSION else 'not_adopted'}


def inspect_database(path):
    path=Path(path)
    if not path.is_file():raise MigrationError('数据库不存在，未创建或修改任何内容')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as db:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        return inspect_connection(db)


def row_digest(db,table,names):
    quote=lambda name:'"'+name.replace('"','""')+'"'
    statement='SELECT '+','.join(map(quote,names))+' FROM '+quote(table)
    hashes=[]
    for row in db.execute(statement):
        values=[{'blob':value.hex()} if isinstance(value,bytes) else value for value in row]
        hashes.append(hashlib.sha256(json.dumps(values,ensure_ascii=False,separators=(',',':')).encode()).digest())
    return hashlib.sha256(b''.join(sorted(hashes))).hexdigest()


def business_snapshot(db):
    result={}
    for kind,table,_ in objects(db):
        if kind=='table' and table!='schema_migrations':
            names=list(columns(db,table));result[table]=(names,row_digest(db,table,names))
    return result


def verify_business(db,snapshot):
    for table,(names,digest) in snapshot.items():
        if row_digest(db,table,names)!=digest:raise MigrationError('升级改变了既有业务数据，已拒绝提交')


def migrate_connection(db):
    """Caller owns the transaction; failure must roll back DDL, data and version together."""
    before=inspect_connection(db);version=before['current_version']
    if version==LATEST_VERSION:return dict(before,status='already_current')
    original_data=business_snapshot(db)
    if version<21:
        install_legacy_schema(db);db.execute('PRAGMA user_version=21')
        validate_schema(db,21)
    db.execute(LEDGER_SQL)
    for number,(name,checksum) in MIGRATIONS.items():
        mode='adopted' if number==21 and version==21 else 'applied'
        db.execute('INSERT INTO schema_migrations(version,name,checksum,mode) VALUES(?,?,?,?)',(number,name,checksum,mode))
    db.execute('PRAGMA user_version=22')
    validate_schema(db,LATEST_VERSION);check_integrity(db);verify_business(db,original_data)
    return {'status':'migrated','from_version':version,'to_version':LATEST_VERSION,'ledger':'verified','integrity':'ok'}


def migrate_database(path):
    with closing(sqlite3.connect(path,timeout=3)) as db:
        db.row_factory=sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        try:result=migrate_connection(db);db.commit()
        except BaseException:db.rollback();raise
    return result


def initialize_store(path):
    path=Path(path);existed=path.exists() and path.stat().st_size>0
    if existed:
        state=inspect_database(path)
        if state['needs_upgrade']:raise MigrationError('数据结构需要离线升级；请先安全停服，运行 ./migrate.command upgrade；未自动修改旧数据库')
    with closing(sqlite3.connect(path,timeout=30)) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('BEGIN IMMEDIATE')
        try:
            if not existed:migrate_connection(db)
            else:validate_ledger(db)
            seed_templates(db);db.commit()
        except BaseException:db.rollback();raise
