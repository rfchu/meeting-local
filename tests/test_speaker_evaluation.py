import pytest
from app.speaker_evaluation import evaluate_speakers

def fixture():
    return {'segments':[
        {'id':'a','start':0,'end':10,'speaker':'甲'},
        {'id':'b','start':10,'end':11,'speaker':'乙'},
        {'id':'c','start':11,'end':12,'speaker':'甲'},
        {'id':'d','start':12,'end':13,'speaker':'乙'}],
        'reference_labels':{'a':'甲','b':'甲','c':'甲','d':None},
        'suggestions':[{'segment_id':'b','proposed_speaker':'甲','strength':'supported'},
                       {'segment_id':'c','proposed_speaker':'乙','strength':'review'},
                       {'segment_id':'d','proposed_speaker':'甲','strength':'review'}]}

def test_evaluation_reports_fixes_and_harm_separately():
    report=evaluate_speakers(fixture())
    assert report['before']['segment_accuracy']==report['proposed']['segment_accuracy']==2/3
    assert report['before']['duration_weighted_accuracy']==11/12
    assert report['changes']=={'evaluated':2,'fixed':1,'harmful':1,'still_wrong':0,'correction_precision':.5}
    assert report['unevaluated_proposals']==1
    assert report['annotation_coverage']==.75
    assert report['by_strength']['review']['harmful_changes']==1

def test_abstention_and_zero_duration_are_not_perfect_precision():
    data=fixture();data['suggestions']=[]
    for s in data['segments']:s['end']=s['start']
    report=evaluate_speakers(data)
    assert report['changes']['correction_precision'] is None
    assert report['before']['duration_weighted_accuracy'] is None

@pytest.mark.parametrize('change',['unknown','duplicate','locked','stale','no_labels','negative'])
def test_invalid_fixture_rejected(change):
    data=fixture()
    if change=='unknown':data['suggestions'][0]['segment_id']='unknown'
    if change=='duplicate':data['suggestions'].append(data['suggestions'][0])
    if change=='locked':data['segments'][1]['speaker_locked']=True
    if change=='stale':data['suggestions'][0]['original_speaker']='甲'
    if change=='no_labels':data['reference_labels']={}
    if change=='negative':data['segments'][0]['end']=-1
    with pytest.raises(ValueError):evaluate_speakers(data)


def test_cli_template_score_and_no_overwrite(tmp_path):
    import json,subprocess,sys
    from pathlib import Path
    script=Path(__file__).resolve().parents[1]/'scripts/evaluate_speakers.py'
    source=tmp_path/'source.json';template=tmp_path/'template.json';report=tmp_path/'report.json'
    source.write_text(json.dumps(fixture()))
    run=lambda *args:subprocess.run([sys.executable,str(script),*map(str,args)],capture_output=True)
    assert run('--input',source,'--template','--output',template).returncode==0
    assert all(label is None for label in json.loads(template.read_text())['reference_labels'].values())
    assert run('--input',source,'--output',report).returncode==0
    original=report.read_bytes()
    assert run('--input',source,'--output',report).returncode==1
    assert report.read_bytes()==original
