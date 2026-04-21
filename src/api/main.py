import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import asyncio

from dotenv import load_dotenv
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import json
import os
import uuid
import time
from datetime import datetime
from typing import List, Optional, Dict, Any
from io import BytesIO
from loguru import logger
from pydantic import BaseModel
from sqlmodel import delete, select

# 相对导入改成绝对导入
from src.models.domain import Requirement, TestCase, ProjectContext, GenerationJob
from src.data.database import init_db, get_session, get_all_requirements, get_all_test_cases
from src.core.generation.generator import TestCaseGenerator
from src.core.ai.llm_service import LLMService
from src.core.kg.graph_service import KnowledgeGraphService
from src.core.generation.workflow import GenerationWorkflow
from src.core.ingestion.ingestor import RequirementIngestor
from src.core.output.exporter import TestCaseExporter
from src.core.output.feishu_client import FeishuClient
from src.core.analytics import annotate_quality_characteristics

load_dotenv()

app = FastAPI(title="AI Test Case Generator API")

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global Error: {exc} | URL: {request.url}")
    return JSONResponse(
        status_code=500,
        content={"message": "Internal Server Error", "detail": str(exc)},
    )

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Logging Middleware
class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        start_time = time.time()
        
        logger.info(f"Request Start: {request.method} {request.url} [ID: {request_id}]")
        
        response = await call_next(request)
        
        process_time = time.time() - start_time
        logger.info(f"Request Finished: {response.status_code} [ID: {request_id}] Time: {process_time:.3f}s")
        
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = str(process_time)
        return response

app.add_middleware(LoggingMiddleware)

STARTUP_STATUS: Dict[str, Any] = {
    "status": "pending",
    "checked_at": "",
    "steps": [],
    "llm": {},
}

GENERATION_CANCEL_FLAGS: Dict[str, bool] = {}
GENERATION_SUBSCRIBERS: Dict[str, List[asyncio.Queue]] = {}
GENERATION_QUEUE: Optional[asyncio.Queue[str]] = None
GENERATION_WORKERS: List[asyncio.Task] = []


class FeishuSheetExportPayload(BaseModel):
    case_ids: List[str] = []
    requirement_link_base_url: str = ""
    app_id: str = ""
    app_secret: str = ""
    tenant_access_token: str = ""
    base_url: str = "https://open.feishu.cn"
    spreadsheet_token: str = ""
    sheet_id: str = ""
    start_cell: str = "A1"
    auto_create_sheet: bool = True
    sheet_title: str = ""
    sheet_folder_token: str = ""


class ExcelExportPayload(BaseModel):
    case_ids: List[str] = []


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _normalize_requirement_for_db(req: Requirement) -> Requirement:
    # Ensure JSON columns are plain dicts before SQLModel writes them to SQLite JSON fields.
    try:
        req.ingestion_metadata = _to_jsonable(getattr(req, "ingestion_metadata", None))
        req.extracted_entities = _to_jsonable(getattr(req, "extracted_entities", None))
        req.req_spec = _to_jsonable(getattr(req, "req_spec", None))
    except Exception:
        # Best-effort; if something is really wrong, DB commit will still raise.
        pass
    return req


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def _to_serializable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=_json_default))


def _job_snapshot(job: GenerationJob) -> Dict[str, Any]:
    return {
        "job_id": job.job_id,
        "status": job.status,
        "events": job.events or [],
        "result_count": job.result_count,
        "error": job.error,
        "req_ids": job.req_ids or [],
        "upload_batch_id": job.upload_batch_id,
        "created_at": job.created_at.isoformat(timespec="seconds") if isinstance(job.created_at, datetime) else str(job.created_at or ""),
        "completed_at": job.completed_at.isoformat(timespec="seconds") if isinstance(job.completed_at, datetime) else (str(job.completed_at or "") if job.completed_at else ""),
    }


def _append_job_event(job: GenerationJob, event: Dict[str, Any]) -> Dict[str, Any]:
    serial = _to_serializable(event)
    events = list(job.events or [])
    serial["event_index"] = len(events)
    events.append(serial)
    job.events = events
    if serial.get("type") == "result" and isinstance(serial.get("data"), list):
        job.result_count += len(serial["data"])
    with get_session() as session:
        session.merge(job)
        session.commit()
    for queue in list(GENERATION_SUBSCRIBERS.get(job.job_id, [])):
        try:
            queue.put_nowait(serial)
        except Exception:
            pass
    return serial


async def _enqueue_generation_job(job_id: str) -> None:
    global GENERATION_QUEUE
    if GENERATION_QUEUE is None:
        return
    await GENERATION_QUEUE.put(job_id)


async def _run_generation_job(job_id: str) -> None:
    with get_session() as session:
        job_state = session.get(GenerationJob, job_id)
        if job_state is None:
            return
        if job_state.status in {"completed", "failed", "cancelled"}:
            return
        job_state.status = "running"
        session.merge(job_state)
        session.commit()
        req_ids = list(job_state.req_ids or [])
        current_batch_id = job_state.upload_batch_id
        reqs = [session.get(Requirement, rid) for rid in req_ids]
        reqs = [req for req in reqs if req is not None]

    llm_service = LLMService()
    kg_service = KnowledgeGraphService()
    generator = TestCaseGenerator(llm_service, kg_service)

    results: List[TestCase] = []
    cancelled = False
    try:
        async for update in generator.stream_generate(reqs):
            if GENERATION_CANCEL_FLAGS.get(job_id):
                cancelled = True
                with get_session() as session:
                    job_state = session.get(GenerationJob, job_id)
                    if job_state is not None:
                        _append_job_event(job_state, {"type": "cancelled", "job_id": job_id, "message": "任务已中断"})
                        job_state.status = "cancelled"
                        job_state.completed_at = datetime.now()
                        session.merge(job_state)
                        session.commit()
                break
            if update["type"] == "result":
                results.extend(update["data"])
            update["job_id"] = job_id
            with get_session() as session:
                job_state = session.get(GenerationJob, job_id)
                if job_state is not None:
                    _append_job_event(job_state, update)

        if not cancelled:
            annotate_quality_characteristics(reqs, results)
            with get_session() as session:
                job_state = session.get(GenerationJob, job_id)
                for tc in results:
                    env = getattr(tc, "system_env", None) or {}
                    if hasattr(env, "model_dump"):
                        env = env.model_dump(mode="json")
                    if not isinstance(env, dict):
                        env = {}
                    if current_batch_id and not env.get("source_upload_batch_id"):
                        env["source_upload_batch_id"] = current_batch_id
                    env["generation_job_id"] = job_id
                    tc.system_env = env
                    session.merge(tc)
                if job_state is not None:
                    job_state.status = "completed"
                    job_state.completed_at = datetime.now()
                    session.merge(job_state)
                session.commit()
            with get_session() as session:
                job_state = session.get(GenerationJob, job_id)
                if job_state is not None:
                    _append_job_event(job_state, {"type": "completed", "job_id": job_id, "message": "生成完毕"})
    except Exception as e:
        logger.exception(f"Generation job {job_id} failed: {e}")
        with get_session() as session:
            job_state = session.get(GenerationJob, job_id)
            if job_state is not None:
                job_state.status = "failed"
                job_state.error = str(e)
                job_state.completed_at = datetime.now()
                session.merge(job_state)
                session.commit()
        with get_session() as session:
            job_state = session.get(GenerationJob, job_id)
            if job_state is not None:
                _append_job_event(job_state, {"type": "error", "job_id": job_id, "message": str(e)})
    finally:
        GENERATION_CANCEL_FLAGS.pop(job_id, None)


async def _generation_worker(worker_name: str) -> None:
    global GENERATION_QUEUE
    if GENERATION_QUEUE is None:
        return
    while True:
        job_id = await GENERATION_QUEUE.get()
        try:
            await _run_generation_job(job_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"{worker_name} failed while processing {job_id}: {e}")
        finally:
            GENERATION_QUEUE.task_done()


def _req_batch_id(req: Requirement) -> str:
    meta = getattr(req, "ingestion_metadata", None) or {}
    if hasattr(meta, "model_dump"):
        meta = meta.model_dump(mode="json")
    if isinstance(meta, dict):
        return str(meta.get("upload_batch_id", "") or "")
    return ""


def _case_batch_id(tc: TestCase) -> str:
    env = getattr(tc, "system_env", None) or {}
    if hasattr(env, "model_dump"):
        env = env.model_dump(mode="json")
    if isinstance(env, dict):
        return str(env.get("source_upload_batch_id", "") or "")
    return ""


def _record_startup_step(name: str, status: str, detail: str = "") -> None:
    STARTUP_STATUS["steps"].append({
        "name": name,
        "status": status,
        "detail": detail,
    })


@app.on_event("startup")
async def startup_event():
    global GENERATION_QUEUE, GENERATION_WORKERS
    STARTUP_STATUS["status"] = "running"
    STARTUP_STATUS["checked_at"] = datetime.now().isoformat(timespec="seconds")
    STARTUP_STATUS["steps"] = []
    STARTUP_STATUS["llm"] = {}
    try:
        init_db()
        _record_startup_step("database", "success", "数据库初始化完成")
    except Exception as e:
        _record_startup_step("database", "error", str(e))
        STARTUP_STATUS["status"] = "error"
        return

    try:
        llm_service = LLMService()
        connection = llm_service.check_connection()
        STARTUP_STATUS["llm"] = connection
        if connection.get("status") == "success":
            _record_startup_step("llm_connection", "success", connection.get("message", "模型连接成功"))
            STARTUP_STATUS["status"] = "success"
        else:
            _record_startup_step("llm_connection", "error", connection.get("message", "模型连接失败"))
            STARTUP_STATUS["status"] = "degraded"
    except Exception as e:
        STARTUP_STATUS["llm"] = {"status": "error", "message": str(e)}
        _record_startup_step("llm_connection", "error", str(e))
        STARTUP_STATUS["status"] = "degraded"

    GENERATION_QUEUE = asyncio.Queue()
    worker_count = max(1, int(os.getenv("GENERATION_QUEUE_WORKERS", "1")))
    GENERATION_WORKERS = [
        asyncio.create_task(_generation_worker(f"generation-worker-{idx+1}"))
        for idx in range(worker_count)
    ]
    with get_session() as session:
        pending_jobs = session.exec(
            select(GenerationJob).where(GenerationJob.status.in_(["queued", "running"]))
        ).all()
        pending_job_ids = [job.job_id for job in pending_jobs]
        for job in pending_jobs:
            if job.status == "running":
                job.status = "queued"
                job.error = ""
                session.merge(job)
        session.commit()
    if os.getenv("GENERATION_RESUME_ON_STARTUP", "false").lower() in {"1", "true", "yes", "on"}:
        for job_id in pending_job_ids:
            await _enqueue_generation_job(job_id)


@app.on_event("shutdown")
async def shutdown_event():
    for worker in GENERATION_WORKERS:
        worker.cancel()
    for worker in GENERATION_WORKERS:
        try:
            await worker
        except asyncio.CancelledError:
            pass

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "startup_status": STARTUP_STATUS.get("status", "unknown"),
        "checked_at": STARTUP_STATUS.get("checked_at", ""),
    }


@app.get("/startup-status")
def get_startup_status():
    return STARTUP_STATUS

@app.get("/requirements", response_model=List[Requirement])
def list_requirements(batch_id: Optional[str] = None):
    requirements = get_all_requirements()
    annotate_quality_characteristics(requirements, [])
    if batch_id:
        return [r for r in requirements if _req_batch_id(r) == batch_id]
    return requirements


@app.post("/requirements/ingest")
async def ingest_requirements_files(
    files: List[UploadFile] = File(...),
    replace_existing: bool = Form(False),
):
    if not files:
        raise HTTPException(status_code=400, detail="未提供上传文件。")

    ingestor = RequirementIngestor()
    temp_dir = Path("temp_upload_api")
    temp_dir.mkdir(parents=True, exist_ok=True)
    all_requirements: List[Requirement] = []
    errors: List[Dict[str, str]] = []
    batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    for uploaded in files:
        file_path = temp_dir / uploaded.filename
        try:
            content = await uploaded.read()
            file_path.write_bytes(content)
            reqs = ingestor.ingest(str(file_path))
            for req in reqs:
                meta = _to_jsonable(getattr(req, "ingestion_metadata", None)) or {}
                if not isinstance(meta, dict):
                    meta = {}
                meta["upload_batch_id"] = batch_id
                meta["source_file"] = uploaded.filename
                meta["parsed_at"] = datetime.now().isoformat(timespec="seconds")
                req.ingestion_metadata = meta
            all_requirements.extend(reqs)
        except Exception as e:
            errors.append({"file": uploaded.filename, "error": str(e)})
        finally:
            try:
                if file_path.exists():
                    file_path.unlink()
            except Exception:
                pass

    if not all_requirements and errors:
        return JSONResponse(
            status_code=400,
            content={"message": "全部文件解析失败。", "errors": errors, "saved": 0},
        )
    annotate_quality_characteristics(all_requirements, [])

    with get_session() as session:
        if replace_existing:
            session.exec(delete(TestCase))
            session.exec(delete(Requirement))
        try:
            for req in all_requirements:
                session.merge(_normalize_requirement_for_db(req))
            session.commit()
        except Exception as e:
            logger.error(f"Failed to persist ingested requirements: {e}")
            raise HTTPException(status_code=500, detail=f"保存到数据库失败：{e}")

    return {
        "saved": len(all_requirements),
        "files": [f.filename for f in files],
        "errors": errors,
        "replace_existing": replace_existing,
        "batch_id": batch_id,
    }

@app.get("/test_cases", response_model=List[TestCase])
def list_test_cases(batch_id: Optional[str] = None):
    requirements = get_all_requirements()
    test_cases = get_all_test_cases()
    annotate_quality_characteristics(requirements, test_cases)
    if batch_id:
        return [tc for tc in test_cases if _case_batch_id(tc) == batch_id]
    return test_cases


@app.post("/export/excel")
def export_cases_excel(payload: ExcelExportPayload):
    with get_session() as session:
        if payload.case_ids:
            cases = [session.get(TestCase, cid) for cid in payload.case_ids]
            test_cases = [c for c in cases if c is not None]
        else:
            test_cases = session.exec(select(TestCase)).all()
        req_ids = list({tc.related_req_id for tc in test_cases if getattr(tc, "related_req_id", "")})
        requirements = [session.get(Requirement, rid) for rid in req_ids]
        requirements_by_id = {req.id: req for req in requirements if req is not None}
    annotate_quality_characteristics(list(requirements_by_id.values()), test_cases)
    exporter = TestCaseExporter(test_cases, requirements_by_id=requirements_by_id)
    binary = exporter.to_excel()
    filename = f"test_cases_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        BytesIO(binary),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@app.post("/export/feishu-sheet")
def export_cases_to_feishu_sheet(payload: FeishuSheetExportPayload):
    with get_session() as session:
        if payload.case_ids:
            candidates = [session.get(TestCase, cid) for cid in payload.case_ids]
            test_cases = [c for c in candidates if c is not None]
        else:
            test_cases = session.exec(select(TestCase)).all()
        req_ids = list({tc.related_req_id for tc in test_cases if getattr(tc, "related_req_id", "")})
        requirements = [session.get(Requirement, rid) for rid in req_ids]
        requirements_by_id = {req.id: req for req in requirements if req is not None}
    annotate_quality_characteristics(list(requirements_by_id.values()), test_cases)

    exporter = TestCaseExporter(
        test_cases,
        requirement_link_base_url=payload.requirement_link_base_url or "",
        requirements_by_id=requirements_by_id,
    )
    client = FeishuClient(
        app_id=payload.app_id,
        app_secret=payload.app_secret,
        tenant_access_token=payload.tenant_access_token,
        base_url=payload.base_url,
        spreadsheet_token=payload.spreadsheet_token,
        sheet_id=payload.sheet_id,
    )

    spreadsheet_token = payload.spreadsheet_token
    sheet_id = payload.sheet_id
    if not spreadsheet_token and payload.auto_create_sheet:
        created = client.create_spreadsheet(
            payload.sheet_title or f"AI测试用例_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            folder_token=payload.sheet_folder_token,
        )
        if created and created.get("spreadsheet_token"):
            spreadsheet_token = created.get("spreadsheet_token", "")
            sheet_id = created.get("sheet_id", "")

    if not spreadsheet_token:
        raise HTTPException(status_code=400, detail="缺少 Spreadsheet Token，且自动创建失败。")

    values = exporter.to_sheet_values()
    ok = client.push_sheet_values(
        values,
        spreadsheet_token=spreadsheet_token,
        sheet_id=sheet_id,
        start_cell=payload.start_cell or "A1",
    )
    if not ok:
        detail = client.last_error or "飞书 Sheet 推送失败。"
        raise HTTPException(status_code=500, detail=detail)

    final_sheet_id = sheet_id or client.detect_sheet_id(spreadsheet_token) or ""
    sheet_url = f"https://feishu.cn/sheets/{spreadsheet_token}" + (f"?sheet={final_sheet_id}" if final_sheet_id else "")
    return {
        "success": True,
        "count": max(len(values) - 1, 0),
        "spreadsheet_token": spreadsheet_token,
        "sheet_id": final_sheet_id,
        "sheet_url": sheet_url,
    }

@app.post("/generate/stream")
async def generate_cases_stream(request: Request, reqs: List[Requirement], job_id: Optional[str] = None):
    """
    Streamed generation of test cases.
    Yields JSON events for progress and final results.
    """
    current_batch_id = ""
    if reqs:
        current_batch_id = _req_batch_id(reqs[0])
    
    job = job_id or str(uuid.uuid4())
    with get_session() as session:
        existing_job = session.get(GenerationJob, job)

    if existing_job is None:
        GENERATION_CANCEL_FLAGS[job] = False
        with get_session() as session:
            session.add(
                GenerationJob(
                    job_id=job,
                    upload_batch_id=current_batch_id,
                    req_ids=[req.id for req in reqs],
                    status="queued",
                    events=[],
                    result_count=0,
                    error="",
                )
            )
            session.commit()
        await _enqueue_generation_job(job)

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()
        index = 0
        GENERATION_SUBSCRIBERS.setdefault(job, []).append(queue)
        try:
            while True:
                with get_session() as session:
                    job_state = session.get(GenerationJob, job)
                    if job_state is None:
                        break
                    snapshot = _job_snapshot(job_state)
                while index < len(snapshot["events"]):
                    payload = snapshot["events"][index]
                    index += 1
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if snapshot["status"] in {"completed", "failed", "cancelled"}:
                    break
                try:
                    await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    continue
        finally:
            subscribers = GENERATION_SUBSCRIBERS.get(job, [])
            if queue in subscribers:
                subscribers.remove(queue)
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/generate/{job_id}/status")
def get_generate_job_status(job_id: str, since_index: int = 0):
    with get_session() as session:
        job = session.get(GenerationJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="生成任务不存在")
    snapshot = _job_snapshot(job)
    start = max(0, since_index)
    return {
        "job_id": job_id,
        "status": snapshot["status"],
        "done": snapshot["status"] in {"completed", "failed", "cancelled"},
        "result_count": snapshot["result_count"],
        "error": snapshot["error"],
        "upload_batch_id": snapshot["upload_batch_id"],
        "req_ids": snapshot["req_ids"],
        "completed_at": snapshot["completed_at"],
        "next_index": len(snapshot["events"]),
        "events": snapshot["events"][start:],
    }


@app.post("/generate/{job_id}/cancel")
def cancel_generate_job(job_id: str):
    GENERATION_CANCEL_FLAGS[job_id] = True
    return {"success": True, "job_id": job_id}

@app.post("/refine/stream")
async def refine_cases_manual_stream(request: Request, tc_list: List[TestCase], feedback: str):
    """
    Streamed manual refinement of test cases.
    """
    model_gen = request.headers.get("X-LLM-MODEL-GEN")
    base_url = request.headers.get("X-OPENAI-BASE-URL")
    api_key = request.headers.get("X-OPENAI-API-KEY")
    llm_service = LLMService(api_key=api_key, base_url=base_url, model=model_gen)
    kg_service = KnowledgeGraphService()
    workflow = GenerationWorkflow(llm_service, kg_service)
    
    async def event_generator():
        async for update in workflow.run_refine_with_updates(tc_list, feedback):
            yield f"data: {json.dumps(update, ensure_ascii=False)}\n\n"
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/kg/learn")
async def kg_learn(module: str, tc: Optional[TestCase] = None, rule: Optional[str] = None):
    """
    Learn new rule into KG. Can learn from a TestCase (extracting rules) or raw text.
    """
    kg_service = KnowledgeGraphService()
    llm_service = LLMService()
    
    if tc:
        # Extract structured rules from TestCase
        ti = tc.get_test_instruction()
        extracted_rules = await llm_service.extract_kg_rules(
            title=tc.title or "Untitled",
            steps=ti.steps,
            expected=ti.expected_result
        )
        if extracted_rules:
            count = kg_service.batch_learn_rules(module, extracted_rules)
            return {"success": count > 0, "extracted_rules": extracted_rules, "count": count}
    
    if rule:
        success = kg_service.learn_from_feedback(module, rule)
        return {"success": success, "count": 1 if success else 0}
        
    return {"success": False, "message": "No TestCase or Rule provided."}

@app.post("/kg/learn/history")
async def kg_learn_from_history(request: Request, module: str, history: List[Dict[str, Any]]):
    """
    Extract rules from user feedback history and learn them.
    """
    kg_service = KnowledgeGraphService()
    model_gen = request.headers.get("X-LLM-MODEL-GEN")
    base_url = request.headers.get("X-OPENAI-BASE-URL")
    api_key = request.headers.get("X-OPENAI-API-KEY")
    llm_service = LLMService(api_key=api_key, base_url=base_url, model=model_gen)
    
    extracted_rules = await llm_service.extract_rules_from_feedback_history(history)
    if extracted_rules:
        count = kg_service.batch_learn_rules(module, extracted_rules)
        return {"success": count > 0, "extracted_rules": extracted_rules, "count": count}
        
    return {"success": False, "message": "No rules extracted from history."}

@app.post("/kg/learn/postmortem")
async def kg_learn_postmortem(module: str, failure: str):
    """
    Learn failure modes / postmortem records into KG.
    """
    kg_service = KnowledgeGraphService()
    success = kg_service.learn_from_postmortem(module, failure)
    return {"success": success, "count": 1 if success else 0}

@app.post("/kg/learn/item")
async def kg_learn_item(module: str, item_type: str, content: str, metadata: Optional[Dict[str, Any]] = None):
    """
    Learn a generic knowledge item after lightweight confirmation.
    """
    kg_service = KnowledgeGraphService()
    success = kg_service.learn_generic_item(module, item_type, content, metadata=metadata or {})
    return {"success": success, "count": 1 if success else 0}

@app.get("/kg/summary")
async def get_kg_summary():
    """
    Returns a summary of all modules and their rules/scenarios.
    """
    kg_service = KnowledgeGraphService()
    summary = kg_service.get_all_modules_summary()
    return summary

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api.main:app", host="127.0.0.1", port=8002)
