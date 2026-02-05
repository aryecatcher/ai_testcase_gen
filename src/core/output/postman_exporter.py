import json
from typing import List, Dict, Any
from ...models.domain import TestCase

class PostmanExporter:
    def __init__(self, test_cases: List[TestCase]):
        self.test_cases = test_cases

    def to_collection(self) -> bytes:
        """
        Create a minimal Postman collection JSON.
        Only includes cases with type 'Interface'.
        """
        items = []
        for tc in self.test_cases:
            if tc.dimension.lower() != "interface":
                continue
            name = tc.title or tc.test_case_id
            # Basic GET request placeholder
            item = {
                "name": name,
                "request": {
                    "method": "GET",
                    "header": [],
                    "url": {
                        "raw": "http://example.com/api",
                        "protocol": "http",
                        "host": ["example", "com"],
                        "path": ["api"]
                    }
                }
            }
            items.append(item)

        collection = {
            "info": {
                "name": "Generated API Tests",
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
            },
            "item": items
        }
        return json.dumps(collection, ensure_ascii=False, indent=2).encode("utf-8")
