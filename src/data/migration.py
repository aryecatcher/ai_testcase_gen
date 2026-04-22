import json
import os
from pathlib import Path
from loguru import logger
from .database import init_db, get_session
from ..models.domain import Requirement, TestCase, ProjectContext
from ..config.runtime import PROJECT_CONTEXT_JSON_PATH

JSON_STORAGE_PATH = PROJECT_CONTEXT_JSON_PATH

def migrate_json_to_db():
    """Migrate data from project_context.json to PostgreSQL database."""
    if not os.path.exists(JSON_STORAGE_PATH):
        logger.info("No project_context.json found. Skipping migration.")
        return

    logger.info(f"Found {JSON_STORAGE_PATH}. Starting migration...")
    
    try:
        with open(JSON_STORAGE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # Parse using ProjectContext to validate data
        context = ProjectContext.model_validate(data)
        
        # Initialize DB
        init_db()
        
        with get_session() as session:
            # 1. Migrate Requirements
            for req_data in context.requirements:
                # Ensure it doesn't already exist
                existing = session.get(Requirement, req_data.id)
                if not existing:
                    session.add(req_data)
                    logger.debug(f"Migrated Requirement: {req_data.id}")
                else:
                    logger.debug(f"Requirement {req_data.id} already exists in DB.")

            # 2. Migrate TestCases
            for tc_data in context.test_cases:
                # Ensure it doesn't already exist
                existing = session.get(TestCase, tc_data.test_case_id)
                if not existing:
                    session.add(tc_data)
                    logger.debug(f"Migrated TestCase: {tc_data.test_case_id}")
                else:
                    logger.debug(f"TestCase {tc_data.test_case_id} already exists in DB.")
            
            session.commit()
            logger.info("Migration completed successfully!")
            
            # Optional: rename old file to backup
            # backup_path = JSON_STORAGE_PATH + ".bak"
            # os.rename(JSON_STORAGE_PATH, backup_path)
            # logger.info(f"Old data backed up to {backup_path}")

    except Exception as e:
        logger.error(f"Migration failed: {e}")

if __name__ == "__main__":
    migrate_json_to_db()
