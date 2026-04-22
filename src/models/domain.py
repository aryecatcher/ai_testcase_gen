from enum import Enum
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field, model_validator
from datetime import datetime
import uuid
from sqlmodel import SQLModel, Field as SQLField, Column, JSON

# --- Knowledge Graph Models ---

class KGNodeType(str, Enum):
    MODULE = "Module"
    FEATURE = "Feature"
    RULE = "Rule"
    TEST_METHOD = "TestMethod"
    TEMPLATE = "Template"
    FAILURE_MODE = "FailureMode"
    EXCEPTION = "Exception"
    SECURITY = "Security"
    BUSINESS = "Business"

class KGNodeModel(BaseModel):
    id: str
    type: KGNodeType
    name: str
    content: str = ""
    alias: List[str] = []
    metadata: Dict[str, Any] = {}

class KGRelationshipType(str, Enum):
    HAS_FEATURE = "HAS_FEATURE"
    HAS_RULE = "HAS_RULE"
    HAS_SCENARIO = "HAS_SCENARIO"
    USES_METHOD = "USES_METHOD"
    HAS_TEMPLATE = "HAS_TEMPLATE"
    HAS_FAILURE_MODE = "HAS_FAILURE_MODE"
    FOLLOWS = "FOLLOWS"
    GLOBAL_RULE = "GLOBAL_RULE"

class KGRelationshipModel(BaseModel):
    source: str
    target: str
    relation: KGRelationshipType
    properties: Dict[str, Any] = {}

# --- Enums ---

class RequirementType(str, Enum):
    FUNCTIONAL = "functional"
    INTERFACE = "interface"
    PERFORMANCE = "performance"
    SECURITY = "security"
    UNKNOWN = "unknown"

class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PARSED = "parsed"
    ENRICHED = "enriched"
    GENERATED = "generated"
    FAILED = "failed"

class TestCaseStatus(str, Enum):
    COMPLETE = "complete"
    PENDING = "pending"

# --- Sub-models for Requirement ---

class IngestionMetadata(BaseModel):
    source_file: str = ""
    parsing_confidence: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)

class ExtractedEntities(BaseModel):
    module: Optional[str] = None
    feature: Optional[str] = None
    constraints: List[Dict[str, Any]] = Field(default_factory=list)

class ReqSpec(BaseModel):
    req_id: str
    module_path: Optional[str] = None
    priority: str = "P2"
    type: RequirementType = RequirementType.FUNCTIONAL

# --- Helper: serialize Pydantic objects to plain dict for JSON storage ---

def _to_jsonable(obj):
    """Convert Pydantic model or dict to JSON-serializable dict."""
    if obj is None:
        return None
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return obj
    return obj

# --- Main Models ---

class Requirement(SQLModel, table=True):
    """Standardized Requirement Model (DB Table)"""
    id: str = SQLField(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    original_text: str

    # Stored as JSON column content — always plain dict
    ingestion_metadata: Optional[Any] = SQLField(default=None, sa_column=Column(JSON))
    extracted_entities: Optional[Any] = SQLField(default=None, sa_column=Column(JSON))
    req_spec: Optional[Any] = SQLField(default=None, sa_column=Column(JSON))

    cleaned_text: Optional[str] = None
    generation_trace: List[str] = SQLField(default_factory=list, sa_column=Column(JSON))
    tokens_consumed: int = 0

    @model_validator(mode="before")
    @classmethod
    def serialize_nested(cls, values):
        """Ensure nested Pydantic models are stored as plain dicts."""
        for field in ("ingestion_metadata", "extracted_entities", "req_spec"):
            if field in values:
                values[field] = _to_jsonable(values[field])
        return values

    def get_ingestion_metadata(self) -> IngestionMetadata:
        """Return ingestion_metadata as IngestionMetadata object."""
        if self.ingestion_metadata is None:
            return IngestionMetadata()
        if isinstance(self.ingestion_metadata, dict):
            return IngestionMetadata(**self.ingestion_metadata)
        return self.ingestion_metadata

    def get_extracted_entities(self) -> ExtractedEntities:
        """Return extracted_entities as ExtractedEntities object."""
        if self.extracted_entities is None:
            return ExtractedEntities()
        if isinstance(self.extracted_entities, dict):
            return ExtractedEntities(**self.extracted_entities)
        return self.extracted_entities

    def calculate_confidence(self) -> float:
        entities = self.get_extracted_entities()
        matched_count = 0
        if entities.module: matched_count += 1
        if entities.feature: matched_count += 1
        if entities.constraints: matched_count += 1
        return round(matched_count / 3, 2)


class BusinessLogic(BaseModel):
    action: str
    constraints: List[str] = Field(default_factory=list)
    extended_scenarios: List[str] = Field(default_factory=list)

class TestDataSets(BaseModel):
    valid: Dict[str, Any] = Field(default_factory=dict)
    invalid: Dict[str, Any] = Field(default_factory=dict)

class TestInstruction(BaseModel):
    pre_condition: str = ""
    steps: List[str] = Field(default_factory=list)
    expected_result: str = ""
    test_data_sets: Optional[TestDataSets] = None

class TestCase(SQLModel, table=True):
    """Standardized Test Case Model (DB Table)"""
    test_case_id: str = SQLField(default_factory=lambda: f"TC-{uuid.uuid4().hex[:8].upper()}", primary_key=True)
    related_req_id: str = SQLField(index=True)
    title: Optional[str] = None

    system_env: Optional[Any] = SQLField(
        default=None,
        sa_column=Column(JSON)
    )
    business_logic: Optional[Any] = SQLField(default=None, sa_column=Column(JSON))
    test_instruction: Optional[Any] = SQLField(default=None, sa_column=Column(JSON))

    methodology: List[str] = SQLField(default_factory=list, sa_column=Column(JSON))
    dimension: str = "Functional"
    priority: str = "P2"
    status: TestCaseStatus = TestCaseStatus.COMPLETE

    review_status: str = "Unreviewed"
    feedback_history: List[Dict[str, Any]] = SQLField(default_factory=list, sa_column=Column(JSON))

    @model_validator(mode="before")
    @classmethod
    def serialize_nested(cls, values):
        """Ensure nested Pydantic models are stored as plain dicts."""
        for field in ("business_logic", "test_instruction", "system_env"):
            if field in values:
                values[field] = _to_jsonable(values[field])
        return values

    def get_test_instruction(self) -> TestInstruction:
        if self.test_instruction is None:
            return TestInstruction()
        if isinstance(self.test_instruction, dict):
            return TestInstruction(**self.test_instruction)
        return self.test_instruction


class GenerationJob(SQLModel, table=True):
    """Persisted generation job state for resume / multi-user deployment."""
    job_id: str = SQLField(primary_key=True)
    upload_batch_id: str = ""
    req_ids: List[str] = SQLField(default_factory=list, sa_column=Column(JSON))
    status: str = "running"
    events: List[Dict[str, Any]] = SQLField(default_factory=list, sa_column=Column(JSON))
    result_count: int = 0
    error: str = ""
    created_at: datetime = SQLField(default_factory=datetime.now)
    completed_at: Optional[datetime] = None


class ProjectContext(BaseModel):
    """Global Context"""
    project_name: str = "Untitled Project"
    requirements: List[Requirement] = Field(default_factory=list)
    test_cases: List[TestCase] = Field(default_factory=list)

    total_tokens: int = 0
    total_generation_time: float = 0.0
    kg_hit_count: int = 0
    total_requests: int = 0

    def get_req_count(self) -> int:
        return len(self.requirements)

    def get_case_count(self) -> int:
        return len(self.test_cases)

    def update_metrics(self, tokens: int, time_taken: float, kg_hit: bool):
        self.total_tokens += tokens
        self.total_generation_time += time_taken
        self.total_requests += 1
        if kg_hit:
            self.kg_hit_count += 1
