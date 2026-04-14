import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import json
import uuid
import time
from typing import List, Optional, Dict, Any
from loguru import logger

# 相对导入改成绝对导入
from src.models.domain import Requirement, TestCase, ProjectContext
from src.data.database import init_db, get_session, get_all_requirements, get_all_test_cases
from src.core.generation.generator import TestCaseGenerator
from src.core.ai.llm_service import LLMService
from src.core.kg.graph_service import KnowledgeGraphService
from src.core.generation.workflow import GenerationWorkflow

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

# Initialize DB on startup
@app.on_event("startup")
def startup_event():
    init_db()

@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.get("/requirements", response_model=List[Requirement])
def list_requirements():
    return get_all_requirements()

@app.get("/test_cases", response_model=List[TestCase])
def list_test_cases():
    return get_all_test_cases()

from fastapi.responses import StreamingResponse
import json

@app.post("/generate/stream")
async def generate_cases_stream(request: Request, reqs: List[Requirement]):
    """
    Streamed generation of test cases.
    Yields JSON events for progress and final results.
    """
    model_gen = request.headers.get("X-LLM-MODEL-GEN")
    model_judge = request.headers.get("X-LLM-MODEL-JUDGE")
    base_url = request.headers.get("X-OPENAI-BASE-URL")
    api_key = request.headers.get("X-OPENAI-API-KEY")
    llm_service = LLMService(api_key=api_key, base_url=base_url, model=model_gen)
    if model_judge:
        llm_service.model_judge = model_judge
    kg_service = KnowledgeGraphService()
    generator = TestCaseGenerator(llm_service, kg_service)
    
    async def event_generator():
        def _jsonify(obj):
            if hasattr(obj, "model_dump"):
                return obj.model_dump(mode="json")
            return str(obj)

        results = []
        async for update in generator.stream_generate(reqs):
            if update["type"] == "result":
                results.extend(update["data"])
            
            # Yield update as JSON
            yield f"data: {json.dumps(update, ensure_ascii=False, default=_jsonify)}\n\n"
        
        # Save results to DB at the end
        with get_session() as session:
            for tc in results:
                session.merge(tc)
            session.commit()
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/refine/stream")
async def refine_cases_manual_stream(request: Request, tc_list: List[TestCase], feedback: str):
    """
    Streamed manual refinement of test cases.
    """
    model_gen = request.headers.get("X-LLM-MODEL-GEN")
    model_judge = request.headers.get("X-LLM-MODEL-JUDGE")
    base_url = request.headers.get("X-OPENAI-BASE-URL")
    api_key = request.headers.get("X-OPENAI-API-KEY")
    llm_service = LLMService(api_key=api_key, base_url=base_url, model=model_gen)
    if model_judge:
        llm_service.model_judge = model_judge
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
    model_judge = request.headers.get("X-LLM-MODEL-JUDGE")
    base_url = request.headers.get("X-OPENAI-BASE-URL")
    api_key = request.headers.get("X-OPENAI-API-KEY")
    llm_service = LLMService(api_key=api_key, base_url=base_url, model=model_gen)
    if model_judge:
        llm_service.model_judge = model_judge
    
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
