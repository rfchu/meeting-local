"""Explicit install-time download. Runtime never downloads model files."""
import os,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('HF_HOME',str(ROOT/'models/hf-cache'))
os.environ.setdefault('HF_HUB_DISABLE_XET','1')
os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY','1')
from huggingface_hub import HfApi,snapshot_download
SPECS=[('mlx-community/Qwen3-ASR-1.7B-8bit','qwen3-asr-1.7b-8bit'),('mlx-community/Qwen3-ForcedAligner-0.6B-8bit','qwen3-aligner-0.6b-8bit')]
manifest_path=ROOT/'qwen-models-manifest.json'
previous=json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
models=[]
for repo,name in SPECS:
    pinned=next((m['revision'] for m in previous.get('models',[]) if m['repo']==repo),None)
    revision=pinned or HfApi(token=False).model_info(repo).sha
    print(f'Downloading {repo} at {revision}',flush=True)
    folder=ROOT/'models'/name
    snapshot_download(repo_id=repo,revision=revision,local_dir=folder,token=False,max_workers=3,
        allow_patterns=['*.json','*.safetensors','*.txt','*.model','LICENSE*','README.md'])
    files=[]
    for path in sorted(folder.iterdir()):
        if not path.is_file():continue
        digest=hashlib.sha256()
        with path.open('rb') as handle:
            while block:=handle.read(8*1024*1024):digest.update(block)
        files.append({'name':path.name,'bytes':path.stat().st_size,'sha256':digest.hexdigest()})
    models.append({'repo':repo,'revision':revision,'directory':name,'files':files})
manifest_path.write_text(json.dumps({'models':models},indent=2))
print('Both Qwen models are installed locally.',flush=True)
