"""Durable command receipts. Observation sequence numbers are never command versions."""
import threading,time,uuid
from pydantic import BaseModel,Field
from typing import Literal
from fastapi import HTTPException

TARGET={'pause':'paused','resume':'recording','stop':'stopped','report_interrupted':'interrupted'}
TERMINAL={'stopped','interrupted','failed','completed'}


def control_view(session):
    control=session.get('control',{'version':0})
    if not isinstance(control,dict) or type(control.get('version',0)) is not int or not 0<=control.get('version',0)<=9007199254740991:
        raise ValueError('录音控制版本无法读取，未执行操作')
    if not isinstance(control.get('receipts',[]),list) or (control.get('pending') is not None and not isinstance(control['pending'],dict)):
        raise ValueError('录音控制回执无法读取，未执行操作')
    pending=control.get('pending')
    return {'version':control.get('version',0),'status':'unconfirmed' if pending else 'idle',
            'pending_command':pending.get('command') if pending else None}


class CommandError(ValueError):
    def __init__(self,code,message,control):
        super().__init__(message);self.code=code;self.control=control


class CommandInput(BaseModel):
    command:Literal['pause','resume','stop','report_interrupted']
    request_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    expected_version:int=Field(ge=0,le=9007199254740991)
    observed_state:Literal['recording','paused','stopped','interrupted']|None=None


class RecordingCommands:
    def __init__(self,recordings,native,pause_boundary=lambda *_:None):
        self.recordings=recordings;self.native=native;self.pause_boundary=pause_boundary;self.lock=threading.RLock()

    def error(self,session,code,message):raise CommandError(code,message,control_view(session))

    def status(self,session):
        if session.get('source')=='macos':return self.native.status(session['id'],touch=False)
        return {'state':session['state'],'duration':session.get('duration',0)}

    def result(self,session,receipt,replayed=False,status=None):
        status=status if status is not None else self.status(session)
        return {'recording_id':session['id'],'control':control_view(session),
                'receipt':dict(receipt,replayed=replayed),'state':status['state'],
                'native':status if session.get('source')=='macos' else None}

    def save(self,session):self.recordings.write(self.recordings.folder(session['id'])/'session.json',session)

    def complete(self,session,pending,status,outcome):
        if session.get('source')=='macos' and pending['command']=='pause' and status['state']=='paused':
            self.pause_boundary(session['id'],round(status.get('duration',0)*16000))
        receipt={k:pending[k] for k in ('request_id','command','observed_state','version')}
        receipt.update(outcome=outcome,state=status['state'])
        control=dict(session['control']);session['control']=control;control['pending']=None
        control['receipts']=(control.get('receipts',[])+[receipt])[-64:]
        self.save(session)
        return self.result(session,receipt,status=status)

    def execute(self,rid,command,*,request_id=None,expected_version=None,observed_state=None,source=None):
        if command not in TARGET:raise ValueError('未知录音命令')
        # Match NativeCapture.finish lock order; no runtime snapshot lock is acquired here.
        with self.lock,self.native.lock,self.recordings.lock:
            session=self.recordings.read(rid);control_view(session);native=session.get('source')=='macos'
            if source and source!=('native' if native else 'browser'):
                self.error(session,'wrong_owner','此入口不能控制该采集来源')
            if native and observed_state is not None or native and command=='report_interrupted':
                self.error(session,'wrong_owner','原生采集不能由浏览器状态上报代替控制')
            if not native and observed_state!=TARGET[command]:
                self.error(session,'observation_required','请先完成浏览器采集操作，再上报对应状态')
            request_id=request_id or uuid.uuid4().hex
            control=session.setdefault('control',{'version':0,'pending':None,'receipts':[]})
            for receipt in control.get('receipts',[]):
                if receipt['request_id']!=request_id:continue
                if receipt['command']!=command or receipt.get('observed_state')!=observed_state:
                    self.error(session,'request_reused','请求标识已用于其他录音操作')
                if receipt['outcome']=='superseded':self.error(session,'command_superseded','此操作已被停止请求替代，请重新核对状态')
                return self.result(session,receipt,True)
            pending=control.get('pending');same=pending and pending['request_id']==request_id
            if same and (pending['command']!=command or pending.get('observed_state')!=observed_state):
                self.error(session,'request_reused','请求标识已用于其他录音操作')
            if same:
                status=self.status(session)
                terminal_stop=command=='stop' and status['state'] in TERMINAL and not self.native.is_running(rid)
                if (status['state']==TARGET[command] and self.native.is_running(rid)) or terminal_stop:
                    return self.complete(session,pending,status,'confirmed_after_retry')
                self.error(session,'command_unconfirmed','上次操作结果未确认，未重新发送指令；请核对采集状态，必要时停止录音')
            if expected_version is not None and expected_version!=control['version']:
                self.error(session,'version_conflict','录音控制状态已变化，未执行操作；请核对最新状态后重试')
            if pending and command!='stop':
                self.error(session,'command_unconfirmed','上一操作结果未确认，未发送新指令；可核对状态或停止录音')
            published=session['state']=='completed' or bool(self.recordings.store.one('SELECT id FROM meetings WHERE id=?',(rid,)))
            status=self.status(session);state=status['state']
            if (published or state in TERMINAL) and command in ('pause','resume'):
                self.error(session,'terminal_state','录音已结束，不能重新暂停或继续；请恢复已有内容')
            if native and command in ('pause','resume') and (state not in ('recording','paused') or status.get('health',{}).get('stalled')):
                self.error(session,'invalid_state','采集尚未就绪，未发送控制指令')
            if pending:
                control['receipts']=(control.get('receipts',[])+[dict(pending,outcome='superseded',state=state)])[-64:]
            if control['version']>=9007199254740991:self.error(session,'version_limit','录音控制版本已到上限，未发送指令')
            version=control['version']+1
            intent={'request_id':request_id,'command':command,'observed_state':observed_state,'version':version}
            control['version']=version
            if not native:
                first=not session.get('capture_reported');session['capture_reported']=True
                if not published:session.update(state=TARGET[command],updated=time.time())
                control['pending']=None
                result=self.complete(session,intent,self.status(session),'already_in_state' if state==session['state'] else 'applied')
                if first or state!=session['state']:
                    self.recordings.event('capture.started' if first and command=='resume' else {'pause':'capture.paused','resume':'capture.resumed','stop':'capture.stop_requested','report_interrupted':'capture.interrupted'}[command],rid)
                return result
            control['pending']=intent
            self.save(session)  # No physical command before intent and reserved version are durable.
            try:
                no_op=state==TARGET[command] or (command=='stop' and published and not self.native.is_running(rid))
                status=status if no_op else self.native.command(rid,command)
                # NativeCapture may update stop_reason; merge that metadata instead of overwriting it.
                current=self.recordings.read(rid);current['control']=control
                return self.complete(current,intent,status,'already_in_state' if no_op else 'applied')
            except (OSError,ValueError,RuntimeError):
                # Retain pending intent. Retrying the same ID only reconciles observed target state.
                try:session=self.recordings.read(rid)
                except (OSError,ValueError):pass
                self.error(session,'command_unconfirmed','录音操作结果尚未确认，已有音轨保留；请用同一请求重查，必要时停止录音')


def register_recording_commands(app,commands):
    @app.post('/api/recordings/{rid}/commands')
    def execute(rid:str,body:CommandInput):
        try:return commands.execute(rid,**body.model_dump())
        except CommandError as error:raise HTTPException(409,{'code':error.code,'message':str(error),'control':error.control})
        except FileNotFoundError:raise HTTPException(404,'录音会话不存在')
        except ValueError as error:raise HTTPException(409,{'code':'control_invalid','message':str(error)})
        except OSError:raise HTTPException(503,{'code':'control_storage_unavailable','message':'控制记录未能确认保存，未保证操作成功；已有音轨保留'})
