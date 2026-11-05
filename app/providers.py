"""Providers operate on local files. No network calls during inference."""
import json, os, shutil, subprocess, time
from pathlib import Path
from typing import Protocol

BASE=Path(__file__).resolve().parents[1]

class ASRProvider(Protocol):
    def transcribe(self, audio: Path, output: Path, run) -> list[dict]: ...

class WhisperCpp:
    engine_id="whisper"
    label="Whisper large-v3-turbo"
    chunk_seconds=300
    needs_punctuation=True
    def __init__(self):
        self.binary=os.getenv('WHISPER_CLI') or str(BASE/'vendor/whisper.cpp/build/bin/whisper-cli')
        self.model=os.getenv('WHISPER_MODEL') or str(BASE/'models/ggml-large-v3-turbo.bin')
    def ready(self): return Path(self.binary).is_file() and Path(self.model).is_file()
    def identity(self):
        files=[Path(self.binary),Path(self.model),BASE/'models/ggml-silero-v6.2.0.bin']
        return [{'path':str(p),'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in files if p.exists()]
    def live_command(self,audio,output):
        if not self.ready(): raise RuntimeError('本地识别引擎或模型尚未安装，请运行 scripts/setup.sh')
        args=[self.binary,'-m',self.model,'-f',str(audio),'-l','zh','-oj','-of',str(output),'-np','-t','8','-ml','80']
        vad=BASE/'models/ggml-silero-v6.2.0.bin'
        if vad.exists(): args += ['--vad','-vm',str(vad)]
        return args
    def read_live_result(self,output):
        result=json.loads(output.with_suffix('.json').read_text())
        segments=[]
        for row in result.get('transcription',[]):
            offsets=row.get('offsets',{})
            text=row.get('text','').strip()
            if text and not (text.startswith('[') and text.endswith(']')):
                segments.append({'start':offsets['from']/1000,'end':offsets['to']/1000,'text':text,'speaker':'未分配'})
        return segments
    def transcribe(self,audio,output,run):
        run(self.live_command(audio,output))
        return self.read_live_result(output)


class QwenMLX:
    engine_id="qwen"
    label="千问 Qwen3-ASR-1.7B · 8-bit"
    # Keep the ASR/forced-alignment input comfortably below the aligner limit.
    chunk_seconds=120
    needs_punctuation=False

    def __init__(self):
        self.python=os.getenv('QWEN_PYTHON') or str(BASE/'.venv-qwen/bin/python')
        self.model=os.getenv('QWEN_ASR_MODEL') or str(BASE/'models/qwen3-asr-1.7b-8bit')
        self.aligner=os.getenv('QWEN_ALIGNER_MODEL') or str(BASE/'models/qwen3-aligner-0.6b-8bit')
        self.runtime=Path(self.python).parent.parent/'qwen-runtime.json'

    @staticmethod
    def model_ready(folder):
        path=Path(folder)
        tokenizer_ready=(path/'tokenizer.json').is_file() or all((path/name).is_file() for name in ('tokenizer_config.json','vocab.json','merges.txt'))
        if not tokenizer_ready or not all((path/name).is_file() for name in ('config.json','preprocessor_config.json')):
            return False
        indexes=list(path.glob('*.safetensors.index.json'))
        if indexes:
            try:
                shards=set(json.loads(indexes[0].read_text())['weight_map'].values())
                return bool(shards) and all((path/name).is_file() for name in shards)
            except (ValueError,KeyError): return False
        return any(p.stat().st_size>0 for p in path.glob('*.safetensors'))

    def ready(self):
        return Path(self.python).is_file() and self.runtime.is_file() and self.model_ready(self.model) and self.model_ready(self.aligner)

    def identity(self):
        files=[Path(self.python),self.runtime,BASE/'scripts/qwen_transcribe.py',BASE/'scripts/qwen_live_server.py',BASE/'scripts/qwen_stream_decoder.py',BASE/'app/alignment.py']
        for directory in (self.model,self.aligner):
            path=Path(directory)
            files.extend(p for p in sorted(path.glob('*')) if p.suffix in ('.json','.safetensors','.txt','.model'))
        return [{'path':str(p),'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in files if p.is_file()]

    def transcribe(self,audio,output,run):
        return self._transcribe(audio,output,run,False)

    def transcribe_live(self,audio,output,run):
        return self._transcribe(audio,output,run,True)

    def resident_command(self):
        return [self.python,str(BASE/'scripts/qwen_live_server.py'),'--model',self.model]

    def stream_command(self,audio,output):
        return [self.python,str(BASE/'scripts/qwen_transcribe.py'),'--model',self.model,'--aligner',self.aligner,
                '--audio',str(audio),'--output',str(output),'--draft','--stream-events']

    def _transcribe(self,audio,output,run,draft):
        if not self.ready(): raise RuntimeError('千问模型或对齐模型尚未就绪，请运行 bash scripts/setup_qwen.sh')
        result_path=output.with_suffix('.qwen.json')
        args=[self.python,str(BASE/'scripts/qwen_transcribe.py'),'--model',self.model,'--aligner',self.aligner,
              '--audio',str(audio),'--output',str(result_path)]
        if draft:args.append('--draft')
        run(args)
        result=json.loads(result_path.read_text())
        return result['segments']


def get_asr_provider(engine='whisper'):
    if engine=='whisper': return WhisperCpp()
    if engine=='qwen': return QwenMLX()
    raise ValueError('不支持的识别引擎')


def engine_catalog():
    return [{'id':p.engine_id,'label':p.label,'ready':p.ready(),'model':Path(p.model).name,
             'timestamps':'字词对齐后分句' if p.engine_id=='qwen' else '模型句级时间戳'}
            for p in (WhisperCpp(),QwenMLX())]

class LocalDiarization:
    def __init__(self):
        self.segmentation=os.getenv('DIARIZATION_SEGMENTATION') or str(BASE/'models/sherpa-onnx-pyannote-segmentation-3-0/model.onnx')
        self.embedding=os.getenv('DIARIZATION_EMBEDDING') or str(BASE/'models/3dspeaker.onnx')
    def ready(self):
        import importlib.util
        return bool(importlib.util.find_spec('sherpa_onnx')) and Path(self.segmentation).is_file() and Path(self.embedding).is_file()
    def diarize(self,audio,output,run,num_speakers=0):
        if not self.ready(): raise RuntimeError('本地发言人模型尚未安装，请运行 scripts/setup.sh')
        import sys
        run([sys.executable,str(BASE/'scripts/diarize.py'),self.segmentation,self.embedding,str(audio),str(output),str(num_speakers)])
        return json.loads(output.read_text())


class LocalPunctuation:
    def __init__(self):
        self.model=str(BASE/'models/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8/model.int8.onnx')
    def ready(self): return Path(self.model).is_file()
    def process(self,segments,folder,run):
        import sys
        source=folder/'unpunctuated.json'; output=folder/'punctuated.json'
        source.write_text(json.dumps(segments,ensure_ascii=False))
        run([sys.executable,str(BASE/'scripts/punctuate.py'),self.model,str(source),str(output)])
        return json.loads(output.read_text())


def attach_speakers(segments, turns):
    for s in segments:
        if s.get('timestamp_precision')=='chunk':s['speaker']='未分配';continue
        scores={}
        for t in turns:
            overlap=max(0,min(s['end'],t['end'])-max(s['start'],t['start']))
            scores[t['speaker']]=scores.get(t['speaker'],0)+overlap
        if scores and max(scores.values())>0: s['speaker']=max(scores,key=scores.get)
    return segments


def merge_overlapping_chunks(chunks):
    """Keep the earlier chunk's complete tail; deduplicate only supported text.

    A midpoint ownership rule can drop text when two decodes split a sentence
    differently. Preserve uncertain overlaps for human review rather than lose it.
    """
    from difflib import SequenceMatcher
    def norm(text): return ''.join(c.lower() for c in text if c.isalnum())
    merged=[]
    for chunk in chunks:
        for source in chunk:
            s=dict(source)
            if not merged: merged.append(s); continue
            end=max(x['end'] for x in merged[-8:])
            if s['start']>=end-.05: merged.append(s);continue
            recent=''.join(x['text'] for x in merged[-8:] if x['end']>=s['start']-1)
            before=norm(recent); after=norm(s['text'])
            if not after: continue
            match=SequenceMatcher(None,before,after,autojunk=False).find_longest_match()
            if s['end']<=end+.2 and (after in before or (match.size>=4 and match.size/len(after)>=.7)):
                continue
            # Remove an exact repeated prefix only; retain the newly decoded suffix.
            overlap=0
            for n in range(min(len(before),len(after)),2,-1):
                if before.endswith(after[:n]): overlap=n;break
            if overlap:
                count=0;cut=0
                for index,c in enumerate(s['text']):
                    if c.isalnum():count+=1
                    if count==overlap:cut=index+1;break
                s['text']=s['text'][cut:].lstrip('，,。.!！?？、 ')
                s['start']=min(s['end'],max(end,s['start']))
                if not s['text']:continue
            elif s['start']<end:
                s['boundary_review']=True
            merged.append(s)
    return sorted(merged,key=lambda s:s['start'])


def checkpoint_identity_matches(engine,previous,current):
    if previous==current:return True
    if engine!='qwen':return False
    software={str(BASE/p) for p in ('scripts/qwen_transcribe.py','scripts/qwen_live_server.py','app/alignment.py')}
    def model_files(items):return [item for item in items if item.get('path') not in software]
    return bool(model_files(previous)) and model_files(previous)==model_files(current)
