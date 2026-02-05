from typing import Dict, Any
import random
import string

class DataSynthesizer:
    def phone_number(self) -> str:
        # Generate 11-digit Chinese mobile number (simplified)
        prefix = random.choice(["130", "131", "132", "133", "134", "135", "136", "137", "138", "139", "150", "151", "152", "157", "158", "159"])
        suffix = "".join(random.choice(string.digits) for _ in range(8))
        return prefix + suffix

    def boundary_string(self, min_len: int, max_len: int) -> Dict[str, Any]:
        s_min_1 = "a" * max(0, min_len - 1)
        s_min = "a" * min_len
        s_min_plus = "a" * (min_len + 1)
        s_max_minus = "a" * (max_len - 1)
        s_max = "a" * max_len
        s_max_plus = "a" * (max_len + 1)
        return {
            "min-1": s_min_1,
            "min": s_min,
            "min+1": s_min_plus,
            "max-1": s_max_minus,
            "max": s_max,
            "max+1": s_max_plus,
        }

    def positive_amounts(self) -> Dict[str, float]:
        return {
            "zero": 0.0,
            "min_positive": 0.01,
            "large": 50000.0
        }
