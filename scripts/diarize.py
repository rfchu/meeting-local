"""Local ONNX diarization. Separate process releases all inference memory."""
import json, sys
from pathlib import Path
import sherpa_onnx
import soundfile as sf
segmentation,embedding,audio,output=sys.argv[1:5]
num_speakers=int(sys.argv[5]) if len(sys.argv)>5 else 0
config=sherpa_onnx.OfflineSpeakerDiarizationConfig(
    segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
        pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=segmentation),num_threads=4,provider='cpu'),
    embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=embedding,num_threads=4,provider='cpu'),
    clustering=sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers or -1,threshold=0.5),
    min_duration_on=0.3,min_duration_off=0.5)
if not config.validate(): raise RuntimeError('发言人模型配置无效')
model=sherpa_onnx.OfflineSpeakerDiarization(config)
samples,rate=sf.read(audio,dtype='float32')
if rate!=model.sample_rate or samples.ndim!=1: raise RuntimeError('需要 16kHz 单声道音频')
result=model.process(samples).sort_by_start_time()
labels={}
turns=[]
for r in result:
    if r.speaker not in labels: labels[r.speaker]=f'发言人 {len(labels)+1}'
    turns.append({'start':r.start,'end':r.end,'speaker':labels[r.speaker]})
path=Path(output); temp=path.with_suffix('.tmp');temp.write_text(json.dumps(turns,ensure_ascii=False));temp.replace(path)
