from typing import List, Dict, Any

class CaseOptimizer:
    def __init__(self):
        pass

    def evaluate_gaps(self, raw_cases: List[Dict[str, Any]]) -> str:
        """
        Inspect cases and return feedback text if gaps found:
        - Missing boundary methodology for length constraints
        - Missing exception path
        """
        has_boundary = any("Boundary" in " ".join(c.get("methodology", [])) for c in raw_cases)
        has_exception = any(c.get("type", "").lower() in ["exception", "security"] for c in raw_cases)
        feedbacks = []
        if not has_boundary:
            feedbacks.append("Add boundary value analysis cases for numeric/length constraints.")
        if not has_exception:
            feedbacks.append("Include exception/negative paths (e.g., invalid inputs, network errors).")
        return "\n".join(feedbacks)
