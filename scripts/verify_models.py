"""Verify installed model files against the delivered manifests."""
import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
entries=json.loads((root/'models-manifest.json').read_text())['files']
qwen=root/'qwen-models-manifest.json'
if qwen.exists():
    for model in json.loads(qwen.read_text())['models']:
        entries.extend({'file':str(Path('models')/model['directory']/f['name']),'sha256':f['sha256']} for f in model['files'])
for entry in entries:
    path=root/entry['file']
    if not path.is_file():raise SystemExit(f"缺少模型：{entry['file']}")
    digest=hashlib.sha256()
    with path.open('rb') as f:
        while chunk:=f.read(8*1024*1024):digest.update(chunk)
    if digest.hexdigest()!=entry['sha256']:raise SystemExit(f"模型校验不匹配：{entry['file']}；请核对下载版本")
print(f"已校验 {len(entries)} 个本地模型文件")
