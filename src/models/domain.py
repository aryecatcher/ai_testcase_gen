from enum import Enum
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field
from datetime import datetime
import uuid

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

# --- Sub-models for Requirement ---

class IngestionMetadata(BaseModel):
    source_file: str
    parsing_confidence: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)

class ExtractedEntities(BaseModel):
    module: Optional[str] = None
    feature: Optional[str] = None
    constraints: List[Dict[str, Any]] = Field(default_factory=list) # e.g. [{"field": "otp_code", "type": "numeric", "length": 6}]

class ReqSpec(BaseModel):
    req_id: str
    module_path: Optional[str] = None
    priority: str = "P2"
    type: RequirementType = RequirementType.FUNCTIONAL

# --- Main Models ---

class Requirement(BaseModel):
    """
    Standardized Requirement Model (Intermediate JSON)
    Matches Section 2.1.4 and 3
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    original_text: str
    
    # Metadata
    ingestion_metadata: IngestionMetadata
    
    # Structured Data (populated by NLP/AI)
    extracted_entities: ExtractedEntities = Field(default_factory=ExtractedEntities)
    req_spec: Optional[ReqSpec] = None
    
    # Raw processing fields
    cleaned_text: Optional[str] = None
    
    def calculate_confidence(self) -> float:
        # Simple mock logic for confidence
        score = 0.0
        if self.extracted_entities.module: score += 0.3
        if self.extracted_entities.feature: score += 0.3
        if self.extracted_entities.constraints: score += 0.3
        # In real scenario: E_matched / E_required
        return min(score, 1.0)

class BusinessLogic(BaseModel):
    action: str
    constraints: List[str] = Field(default_factory=list)
    extended_scenarios: List[str] = Field(default_factory=list)

class TestDataSets(BaseModel):
    valid: Dict[str, Any] = Field(default_factory=dict)
    invalid: Dict[str, Any] = Field(default_factory=dict)

class TestInstruction(BaseModel):
    pre_condition: str
    steps: List[str]
    expected_result: str
    test_data_sets: Optional[TestDataSets] = None

class TestCase(BaseModel):
    """
    Standardized Test Case Model (Section 2.4.5 & 3)
    """
    test_case_id: str = Field(default_factory=lambda: f"TC-{uuid.uuid4().hex[:8].upper()}")
    related_req_id: str
    title: Optional[str] = None # Added for UI display convenience
    
    # System Environment
    system_env: Dict[str, str] = Field(default_factory=lambda: {"os_target": "Linux/Web", "browser_context": "Chrome 90+"})
    
    # Core Logic
    business_logic: Optional[BusinessLogic] = None
    test_instruction: TestInstruction
    
    # Metadata
    methodology: List[str] = Field(default_factory=list) # e.g. ["Boundary Value", "Invalid Class"]
    dimension: str = "Functional" # Functional, Interface, Performance
    priority: str = "P2"
    
    # Feedback
    review_status: str = "Unreviewed" 
    feedback_history: List[Dict[str, Any]] = Field(default_factory=list)

class ProjectContext(BaseModel):
    """
    Global Context
    """
    project_name: str = "Untitled Project"
    requirements: List[Requirement] = Field(default_factory=list)
    test_cases: List[TestCase] = Field(default_factory=list)
    
    def get_req_count(self) -> int:
        return len(self.requirements)
    
    def get_case_count(self) -> int:
        return len(self.test_cases)
