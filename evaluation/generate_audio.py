"""Explicit offline synthesis of authored fixtures. Never reads meetings or opens a microphone."""
import argparse,hashlib,json,subprocess
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--synthesize',action='store_true');p.add_argument('--corpus',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);args=p.parse_args()
    if not args.synthesize:p.error('生成合成音频须显式指定 --synthesize')
    if args.output_dir.exists():p.error('输出目录已存在，未覆盖固定样本')
    corpus=json.loads(args.corpus.read_text());args.output_dir.mkdir(parents=True,mode=0o700);manifest={}
    for case in corpus['cases']:
        if case['track']!='asr_text':continue
        target=args.output_dir/(case['id']+'.wav');temporary=target.with_suffix('.aiff')
        subprocess.run(['/usr/bin/say','-v','Tingting','-r','185','-o',str(temporary),case['reference']],check=True)
        subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(temporary),'-ar','16000','-ac','1','-c:a','pcm_s16le',str(target)],check=True)
        temporary.unlink();manifest[case['id']]=hashlib.sha256(target.read_bytes()).hexdigest()
    (args.output_dir/'audio-manifest.json').write_text(json.dumps({'generator':'macOS say Tingting rate185; ffmpeg PCM16 mono16000','files':manifest},indent=2)+'\n')
    print(json.dumps(manifest))

if __name__=='__main__':main()
