from sqlmodel import SQLModel, create_engine, Session, select
from typing import List, Optional
import os
from dotenv import load_dotenv
from ..models.domain import Requirement, TestCase, GenerationJob

load_dotenv()

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("缺少 DATABASE_URL 配置。当前版本仅支持 PostgreSQL，请在 .env 中配置 postgresql+psycopg 连接串。")
if not DATABASE_URL.startswith("postgresql+psycopg://"):
    raise RuntimeError("DATABASE_URL 配置无效。当前版本仅支持 postgresql+psycopg:// 开头的 PostgreSQL 连接串。")

# Create engine
engine_kwargs = {"echo": False, "pool_pre_ping": True}
engine = create_engine(DATABASE_URL, **engine_kwargs)

def init_db():
    """Initialize database and create tables."""
    SQLModel.metadata.create_all(engine)

def get_session():
    """Get a database session."""
    return Session(engine)

def save_requirement(req: Requirement):
    with get_session() as session:
        session.add(req)
        session.commit()
        session.refresh(req)
    return req

def get_requirement_by_id(req_id: str) -> Optional[Requirement]:
    with get_session() as session:
        return session.get(Requirement, req_id)

def update_requirement(req: Requirement):
    with get_session() as session:
        session.add(req)
        session.commit()
        session.refresh(req)
    return req

def get_all_requirements() -> List[Requirement]:
    with get_session() as session:
        statement = select(Requirement)
        return session.exec(statement).all()

def save_test_case(tc: TestCase):
    with get_session() as session:
        session.add(tc)
        session.commit()
        session.refresh(tc)
    return tc

def get_test_cases_by_req(req_id: str) -> List[TestCase]:
    with get_session() as session:
        statement = select(TestCase).where(TestCase.related_req_id == req_id)
        return session.exec(statement).all()

def get_all_test_cases() -> List[TestCase]:
    with get_session() as session:
        statement = select(TestCase)
        return session.exec(statement).all()


def get_generation_job(job_id: str) -> Optional[GenerationJob]:
    with get_session() as session:
        return session.get(GenerationJob, job_id)


def save_generation_job(job: GenerationJob) -> GenerationJob:
    with get_session() as session:
        session.merge(job)
        session.commit()
    return job
