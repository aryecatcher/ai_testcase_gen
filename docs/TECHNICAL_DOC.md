# AI-Driven Test Case Generation System (V2)

## 1. System Architecture
The system follows a modular layered architecture based on the Technical Scheme (1.txt).

### 1.1 Core Layers (`src/core`)
- **Ingestion Layer (`ingestion`)**: 
  - `doc_processor.py`: Handles file reading (Docx, Excel, JSON).
  - `ingestor.py`: Converts raw data into `Requirement` objects, performs mock NER/SRL extraction, and calculates confidence scores.
- **AI Layer (`ai`)**:
  - `llm_service.py`: Interface to LLM (OpenAI/Mock) for extraction, generation, and refinement.
  - `prompts.py`: Contains optimized prompts with Context Injection, Methodology Induction, and KG constraints.
- **Knowledge Graph Layer (`kg`)**:
  - `graph_service.py`: Mock service to simulate Graph DB lookups for domain constraints and scenario expansion.
- **Generation Layer (`generation`)**:
  - `generator.py`: Orchestrates the flow: Requirement -> KG Lookup -> LLM Generation -> TestCase Object.
- **Feedback Layer (`feedback`)**:
  - `manager.py`: Handles human-in-the-loop feedback to refine test cases.
- **Output Layer (`output`)**:
  - `exporter.py`: Exports test cases to Standard Excel and Feishu format.

### 1.2 Data Models (`src/models`)
- `domain.py`: Pydantic models defining the data exchange norms.
  - `Requirement`: Standardized requirement structure.
  - `TestCase`: Standardized test case structure (including `TestInstruction`, `BusinessLogic`).

## 2. Key Features
- **Standardized Ingestion**: Supports parsing and structured extraction.
- **Methodology-Driven Generation**: Enforces Boundary Value Analysis, Equivalence Partitioning via Prompts.
- **Knowledge Graph Integration**: Injects domain rules (mocked) into the generation process.
- **Feedback Loop**: Allows users to refine cases and updates the model context.
- **Multi-Format Export**: Compatible with Feishu and generic Excel.

## 3. Deployment
- **Environment**: Python 3.9+
- **Dependencies**: `requirements.txt`
- **Run**: `streamlit run ai_test_case_gen/ui/main.py`
