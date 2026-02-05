import sys
import os
import json
# Add project root to path (assuming this script is in tests/)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from src.core.ingestion.doc_processor import DocProcessor
from src.core.ingestion.ingestor import RequirementIngestor
from src.models.domain import Requirement

def test_docling():
    print("=== Testing Docling Integration ===")
    
    file_path = os.path.abspath("sample_requirements.docx")
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return

    # 1. Test DocProcessor directly
    processor = DocProcessor()
    print(f"\n[DocProcessor] Reading file: {file_path}")
    chunks = processor.read_file(file_path)
    
    print(f"Chunks returned: {len(chunks)}")
    if chunks:
        chunk = chunks[0]
        content_len = len(chunk["content"])
        print(f"Markdown Content Length: {content_len}")
        print(f"Markdown Preview: {chunk['content'][:200]}...")
        
        meta = chunk["metadata"]
        print(f"Metadata Keys: {meta.keys()}")
        if "raw_content" in meta:
             print("Success: 'raw_content' (Docling JSON) found in metadata.")
    
    # 2. Test Ingestor
    ingestor = RequirementIngestor()
    print(f"\n[Ingestor] Ingesting file: {file_path}")
    reqs = ingestor.ingest(file_path)
    
    print(f"Requirements generated: {len(reqs)}")
    if reqs:
        req = reqs[0]
        print(f"Req ID: {req.id}")
        print(f"Extracted Module: {req.extracted_entities.module}")
        print(f"Extracted Feature: {req.extracted_entities.feature}")
        print(f"Confidence: {req.ingestion_metadata.parsing_confidence}")
        
        # Verify markdown content is in original_text
        if len(req.original_text) > 0:
             print("Success: Requirement contains text content.")

    # 3. Export Output
    output_file = "docling_output.txt"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("=== Docling Parsing Result ===\n")
        f.write(f"Source File: {file_path}\n")
        f.write(f"Generated Time: {os.path.basename(__file__)}\n\n")
        
        if reqs:
            for i, r in enumerate(reqs):
                f.write(f"--- Requirement #{i+1} ---\n")
                f.write(f"ID: {r.id}\n")
                f.write(f"Module: {r.extracted_entities.module}\n")
                f.write(f"Feature: {r.extracted_entities.feature}\n")
                f.write(f"Confidence: {r.ingestion_metadata.parsing_confidence}\n")
                f.write("\n[Original Text Content]:\n")
                f.write(r.original_text)
                f.write("\n\n" + "="*50 + "\n\n")
    
    print(f"\n[Output] Results saved to: {os.path.abspath(output_file)}")

if __name__ == "__main__":
    test_docling()
