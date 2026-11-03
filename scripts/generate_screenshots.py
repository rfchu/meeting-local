import asyncio
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path
import uvicorn
from app.main import create_app
from app.db import Store, uid, encode
from app.minutes import MeetingMinutes, MinutesItem
from app.qa import Answer, QA

PORT = 8768
CHROME_PORT = 9225
BASE_DIR = Path(__file__).resolve().parents[1]
SCREENSHOTS_DIR = BASE_DIR / "assets" / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

def setup_demo_data(root: Path):
    store = Store(root)
    # 1. Main Meeting: 星河项目交付评审会
    mid = uid()
    directory = store.directory(mid)
    directory.mkdir(parents=True, exist_ok=True)
    with wave.open(str(directory / 'original'), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\0\0' * 16000 * 15)

    store.execute(
        'INSERT INTO meetings(id, title, filename, duration, revision) VALUES(?,?,?,?,?)',
        (mid, '星河项目交付评审会', 'meeting.wav', 15.0, 1)
    )

    segments = [
        {'id': 'seg-1', 'start': 0.0, 'end': 5.2, 'speaker': '张总 (主持人)', 'text': '关于星河项目的上线安排，原定周五的验收交付调整到下周一上午十点。', 'boundary_review': False},
        {'id': 'seg-2', 'start': 5.5, 'end': 10.4, 'speaker': '李明 (研发主管)', 'text': '核心支付模块的压力测试已经全部通过，待办事项由我负责在周日前同步客户。', 'boundary_review': False},
        {'id': 'seg-3', 'start': 10.8, 'end': 14.8, 'speaker': '王华 (QA)', 'text': '验收用例覆盖率达到百分之九十八，现场演示环境准备就绪。', 'boundary_review': False},
    ]
    store.save_revision(mid, segments, 'manual')

    # Notes & Attention Reviews
    from app.notes import insert_note
    with store.connect() as db:
        n1 = insert_note(db, mid, {'captured_at': 2.0, 'raw_text': '交付时间调整至下周一上午十点', 'note_type': 'decision', 'note_scope': 'meeting'})
        n2 = insert_note(db, mid, {'captured_at': 7.0, 'raw_text': '李明周日前输出支付压测报告', 'note_type': 'action', 'note_scope': 'meeting'})
        n3 = insert_note(db, mid, {'captured_at': 11.0, 'raw_text': '我的随堂备注：需核实演示网络备用方案', 'note_type': 'note', 'note_scope': 'personal'})
        
        db.execute('''INSERT INTO attention_reviews(note_id, meeting_id, decision, content, source_segment_ids, revision, note_fingerprint, review_key)
            VALUES(?,?,?,?,?,?,?,?)''',
            (n1, mid, 'confirmed', '确认交付时间调整为下周一 10:00', encode(['seg-1']), 2, 'fp-1', 'key-1'))
        db.execute('''INSERT INTO attention_reviews(note_id, meeting_id, decision, content, source_segment_ids, revision, note_fingerprint, review_key)
            VALUES(?,?,?,?,?,?,?,?)''',
            (n2, mid, 'confirmed', '李明负责支付压测报告同步客户', encode(['seg-2']), 2, 'fp-2', 'key-2'))

    # Minutes v1 (Initial draft)
    doc_v1 = MeetingMinutes(
        title='星河项目交付初稿 (v1)',
        style='formal',
        meeting_date='2026-11-03',
        attendees='张总、李明',
        purpose='交付例会初步讨论',
        overview=[MinutesItem(content='按常规讨论交付安排，未最终确定。', source_segment_ids=['seg-1'])],
        decisions=[MinutesItem(content='暂按本周五原计划推进。', source_segment_ids=['seg-1'])],
        action_items=[MinutesItem(content='准备常规交付材料', owner='李明', due_date='周五', status='待处理', source_segment_ids=['seg-2'])],
        open_questions=[]
    )
    aid_v1 = uid()
    store.execute(
        'INSERT INTO artifacts(id, meeting_id, revision, kind, payload, provenance) VALUES(?,?,?,?,?,?)',
        (aid_v1, mid, 1, 'meeting_minutes', encode(doc_v1.model_dump()), encode({'label': '规则初稿 v1'}))
    )

    # Minutes v2 (AI Full Minutes - Adopted)
    doc_v2 = MeetingMinutes(
        title='星河项目交付评审决议 (v2)',
        style='ai',
        meeting_date='2026-11-03',
        attendees='张总 (主持人)、李明 (研发主管)、王华 (QA)',
        purpose='明确星河项目最终交付排期与各模块验收责任分工',
        overview=[MinutesItem(content='会议对星河项目的上线排期进行了终审，确定调整交付时间并分配了各方职责。', source_segment_ids=['seg-1'])],
        decisions=[MinutesItem(content='星河项目正式验收交付时间调整至下周一上午 10:00。', source_segment_ids=['seg-1'])],
        action_items=[
            MinutesItem(content='完成核心支付模块压测报告并同步客户方', owner='李明', due_date='本周日', status='进行中', source_segment_ids=['seg-2']),
            MinutesItem(content='完成验收演示环境全链路联调与备用网络测试', owner='王华', due_date='周五下班前', status='已完成', source_segment_ids=['seg-3'])
        ],
        open_questions=[MinutesItem(content='客户现场网络接入双机热备方案确认。', source_segment_ids=['seg-3'])]
    )
    aid_v2 = uid()
    store.execute(
        'INSERT INTO artifacts(id, meeting_id, revision, kind, payload, provenance) VALUES(?,?,?,?,?,?)',
        (aid_v2, mid, 2, 'meeting_minutes', encode(doc_v2.model_dump()), encode({'label': 'AI 全文整理 v2'}))
    )
    store.execute('INSERT OR REPLACE INTO minutes_selection(meeting_id, artifact_id) VALUES(?,?)', (mid, aid_v2))

    # Link tasks
    with store.connect() as db:
        t1 = uid()
        db.execute('''INSERT INTO note_todos(id, meeting_id, note_id, content, status, owner, due_date, revision, note_fingerprint, source_segment_ids, note_scope)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (t1, mid, n2, '完成核心支付模块压测报告并同步客户方', 'pending', '李明', '本周日', 2, 'fp-2', encode(['seg-2']), 'meeting'))
        db.execute('INSERT INTO meeting_task_links(meeting_id, task_id) VALUES(?,?)', (mid, t1))
        db.execute('INSERT INTO minutes_task_bindings(meeting_id, artifact_id, action_index, task_id) VALUES(?,?,?,?)', (mid, aid_v2, 0, t1))

        # Delivery record
        delivery_id = uid()
        delivery_payload = {
            'document': doc_v2.model_dump(),
            'segments': segments,
            'revision': 2,
            'include_citations': True,
            'task_status': [
                {'action_index': 0, 'task_id': t1, 'content': '完成核心支付模块压测报告并同步客户方', 'owner': '李明', 'due_date': '本周日', 'status': 'pending'}
            ]
        }
        db.execute('INSERT INTO minutes_deliveries(id, meeting_id, artifact_id, payload) VALUES(?,?,?,?)',
                   (delivery_id, mid, aid_v2, encode(delivery_payload)))

    # 2. Second Meeting for Cross-Meeting context: 产品架构与安全月会
    mid2 = uid()
    dir2 = store.directory(mid2); dir2.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dir2 / 'original'), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000); wav.writeframes(b'\0\0' * 16000 * 10)
    store.execute(
        'INSERT INTO meetings(id, title, filename, duration, revision) VALUES(?,?,?,?,?)',
        (mid2, '产品架构与安全月会', 'arch.wav', 10.0, 1)
    )
    store.save_revision(mid2, [
        {'id': 'm2-1', 'start': 0.0, 'end': 6.0, 'speaker': '安全总监', 'text': '支付网关与结算通道已通过国家三级等保安全评审。'}
    ], 'manual')

    # 3. LLM Config & QA Setup
    pid = uid()
    store.execute('''INSERT INTO llm_configs(id, name, provider_type, base_url, model, is_default, context_window)
        VALUES(?,?,?,?,?,1,32768)''',
        (pid, '本地 Qwen2.5-14B (LM Studio)', 'local', 'http://127.0.0.1:1234/v1', 'qwen2.5-14b'))

    # Prepare QA Run in DB
    qa = QA(store)
    qa_run = qa.prepare(
        question='星河项目的交付时间发生了什么变化？',
        profile_id=pid,
        query='交付 调整',
        mode='keyword',
        kind='transcript'
    )
    qid = qa_run['id']
    mock_answer = Answer(
        status='answered',
        claims=[
            {
                'text': '星河项目的正式验收交付时间已从原定周五调整至下周一上午十点。',
                'kind': 'record',
                'citations': [{'source': 'E1', 'quote': '原定周五的验收交付调整到下周一上午十点'}]
            },
            {
                'text': '核心支付模块的压力测试已全量通过，由李明负责在周日前完成报告并同步客户。',
                'kind': 'record',
                'citations': [{'source': 'E2', 'quote': '核心支付模块的压力测试已经全部通过，待办事项由我负责在周日前同步客户'}]
            }
        ]
    )
    jid = uid()
    store.execute(
        "INSERT INTO jobs(id, meeting_id, state, stage, progress, error, config, kind) VALUES(?, '', 'completed', '完成', 1.0, '', ?, 'retrieval_answer')",
        (jid, encode({'qa_id': qid}))
    )
    store.execute(
        "UPDATE qa_runs SET job_id=?, answer=? WHERE id=?",
        (jid, encode(mock_answer.model_dump()), qid)
    )

    # 4. Preparation brief
    from app.meeting_flow import save_brief
    with store.connect() as db:
        save_brief(db, mid, {
            'title': '星河项目交付评审会',
            'purpose': '确定星河项目最终交付排期与各模块验收责任分工',
            'questions': '下周一交付时现场演示网络是否有双路备份？',
            'previous_meeting_id': mid2
        })

    return mid, aid_v1, aid_v2, qid

def main():
    with tempfile.TemporaryDirectory(prefix='tingji-screenshots-') as tmp:
        root = Path(tmp)
        mid, aid1, aid2, qid = setup_demo_data(root)

        app = create_app(root, start_worker=False)
        app.state.keep_awake.supported = False
        config = uvicorn.Config(app, host='127.0.0.1', port=PORT, access_log=False)
        server = uvicorn.Server(config)

        import threading
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        time.sleep(1.5)

        chrome_profile = f"/tmp/tingji-chrome-{int(time.time())}"
        chrome = subprocess.Popen([
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "--headless=new",
            f"--remote-debugging-port={CHROME_PORT}",
            f"--user-data-dir={chrome_profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--window-size=1440,920"
        ])
        time.sleep(1.5)

        try:
            import urllib.request
            req = urllib.request.Request(f"http://127.0.0.1:{CHROME_PORT}/json/new", method='PUT')
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode())
                ws_url = data['webSocketDebuggerUrl']

            env = {
                **os.environ,
                "WS_URL": ws_url,
                "SCREENSHOTS_DIR": str(SCREENSHOTS_DIR),
                "PORT": str(PORT),
                "AID_V1": str(aid1),
                "AID_V2": str(aid2),
                "QA_ID": str(qid),
            }
            res = subprocess.run(
                ["node", str(BASE_DIR / "scripts" / "capture_screenshots.mjs")],
                env=env,
                capture_output=True,
                text=True
            )
            print("Node Output:")
            print(res.stdout)
            if res.returncode != 0:
                print("Node Error:")
                print(res.stderr)
        finally:
            chrome.kill()
            server.should_exit = True
            shutil.rmtree(chrome_profile, ignore_errors=True)

if __name__ == '__main__':
    main()
