"""Pinned model utility: status, download/resume, or package for offline import."""
import argparse,json,time,zipfile
from pathlib import Path
from app.local_embedding import local_embedding,FILES,verify

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['status','download','pack']);parser.add_argument('--output',default='bge-small-zh-v1.5-int8.zip');args=parser.parse_args()
    model=local_embedding()
    if args.action=='download':
        model.start_install()
        while model.state['state']=='downloading':time.sleep(.2)
        if model.state['state']!='ready':raise SystemExit(model.state['error'])
    if args.action=='pack':
        verify(model.folder)
        with zipfile.ZipFile(Path(args.output),'w',compression=zipfile.ZIP_DEFLATED) as out:
            for name in [*FILES,'manifest.json']:out.write(model.folder/name,name)
        print('离线包已生成：'+args.output)
    else:print(json.dumps(model.status(),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
