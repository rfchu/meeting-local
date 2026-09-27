"""Restore punctuation without changing segment IDs or timing."""
import json,sys,re
from pathlib import Path
import sherpa_onnx
model_path,input_path,output_path=sys.argv[1:]
model=sherpa_onnx.OfflinePunctuation(sherpa_onnx.OfflinePunctuationConfig(
    model=sherpa_onnx.OfflinePunctuationModelConfig(ct_transformer=model_path,num_threads=4)))
segments=json.loads(Path(input_path).read_text())
def letters(text):return ''.join(c.lower() for c in text if c.isalnum())
for segment in segments:
    original=segment['text']
    result=model.add_punctuation(original)
    result=re.sub(r'[，,]{2,}','，',result)
    result=re.sub(r'[。]{2,}','。',result)
    # Punctuation must never rewrite substantive recognized content.
    if letters(result)==letters(original): segment['text']=result
path=Path(output_path);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(segments,ensure_ascii=False));tmp.replace(path)
