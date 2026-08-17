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
        has_boundary = any(
            any(k in " ".join(c.get("methodology", [])) for k in ["Boundary", "边界", "BVA", "Equivalence"])
            for c in raw_cases
        )
        has_exception = any(
            c.get("type", "").lower() in ["exception", "security"] or
            any(k in " ".join(c.get("methodology", [])) for k in ["Negative", "异常", "负向", "Error Guessing"])
            for c in raw_cases
        )
        feedbacks = []
        if not has_boundary:
            feedbacks.append("补充边界值/长度/次数限制相关用例。")
        if not has_exception:
            feedbacks.append("补充异常/负向路径（非法输入、缺失输入、重复输入等）。")
        return "\n".join(feedbacks)
