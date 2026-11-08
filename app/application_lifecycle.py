"""Application startup/shutdown owns resources; routers never construct them."""
import asyncio
from contextlib import asynccontextmanager
from starlette.concurrency import run_in_threadpool
from .application_services import ApplicationServices

def application_lifespan(services:ApplicationServices,start_worker:bool):
    local_embedding=services.providers.embedding
    capture_probes=services.capture_probes
    keep_awake=services.keep_awake
    lifecycle=services.lifecycle
    live_asr_lock=services.live_asr_lock
    live_overview=services.live_overview
    live_sessions=services.live_sessions
    native_capture=services.native_capture
    recording_validation=services.recording_validation
    resident=services.resident
    store=services.store
    worker=services.worker
    @asynccontextmanager
    async def lifespan(app):
        lifecycle.start()
        try:
            boot_models = getattr(app.state, 'boot_models', None)
            if boot_models:
                await run_in_threadpool(boot_models)
            loop=asyncio.get_running_loop()
            worker.asr_lock=live_asr_lock
            worker.release_model=lambda:asyncio.run_coroutine_threadsafe(resident.close(),loop).result(timeout=10)
            if start_worker: worker.start()
            resident.start_monitor()
            store.execute("UPDATE live_asr_ranges SET state='failed',error='服务重启，识别未完成；快速完成会补识别该范围' WHERE state='running'")
            await run_in_threadpool(services.recordings.recover_shorthand)
            await run_in_threadpool(native_capture.reconcile_interrupted)
            await live_sessions.resume()
            native_capture.start_monitor()
            capture_probes.start_monitor()
            live_overview.start_monitor()
            keep_awake.start_monitor()
            recording_validation.start_monitor()
            yield
        finally:
            lifecycle.record('service.shutdown_requested')
            clean=False
            try:
                await run_in_threadpool(recording_validation.close)
                await run_in_threadpool(live_overview.close)
                await live_sessions.close()
                await run_in_threadpool(capture_probes.close)
                await run_in_threadpool(keep_awake.close)
                await run_in_threadpool(native_capture.close)
                await run_in_threadpool(worker.close)
                await resident.shutdown()
                local_embedding().cancel.set()
                local_embedding().release()
                clean=True
            finally:
                services.resources.close()
                lifecycle.close(clean)

    return lifespan
