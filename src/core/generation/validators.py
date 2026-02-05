from typing import Dict, Any, List

class ValidationInterceptor:
    def __init__(self):
        pass

    def validate_case(self, raw_case: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply simple constraints to reduce hallucinations:
        - Phone numbers must be 11 digits
        - Amounts must be >= 0
        """
        td = raw_case.get("test_data", {})
        invalid = td.get("invalid", {})
        valid = td.get("valid", {})

        # Normalize phone number in valid set
        phone = valid.get("phone") or valid.get("mobile")
        if phone and isinstance(phone, str):
            digits = "".join([c for c in phone if c.isdigit()])
            if len(digits) != 11:
                valid["phone"] = digits[:11].ljust(11, "0")
                td["valid"] = valid

        # Ensure amount non-negative
        amount = valid.get("amount")
        if amount is not None and isinstance(amount, (int, float)) and amount < 0:
            valid["amount"] = 0.0
            td["valid"] = valid

        raw_case["test_data"] = td
        return raw_case
