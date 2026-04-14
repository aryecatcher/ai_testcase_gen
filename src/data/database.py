from sqlmodel import SQLModel, create_engine, Session, select
from typing import List, Optional
import os
from ..models.domain import Requirement, TestCase

# Database configuration
DB_FILE = "data/app_database.db"
DATABASE_URL = f"sqlite:///{DB_FILE}"

# Create engine
engine = create_engine(DATABASE_URL, echo=False)

def init_db():
    """Initialize database and create tables."""
    # Ensure data directory exists
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
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
