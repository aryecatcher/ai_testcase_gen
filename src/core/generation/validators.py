from typing import Dict, Any, List, Optional
import ipaddress
import re
from loguru import logger

class ValidationInterceptor:
    """
    Enhanced validation layer to intercept and correct AI hallucinations.
    Uses regex and rule-based checks for common data types.
    """
    
    # Common Patterns
    PATTERNS = {
        "email": r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$",
        "phone": r"^1[3-9]\d{9}$",
        "id_card": r"^\d{17}[\dXx]$",
        "ip_address": r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$",
        "url": r"^https?://[\w\.-]+(?:/[\w\.-]*)*$"
    }
    _DROPPED_SEGMENT_PREFIXES = (
        "Path:",
        "Section:",
        "Summary:",
        "来源文件:",
        "解析格式:",
        "当前片段:",
    )
    _DROPPED_SEGMENT_KEYWORDS = (
        "mandatory",
        "summa",
        "summary",
        "section:",
        "path:",
    )
    _UNSUPPORTED_KEYWORDS = ("暂未确定", "未明确", "本期暂不实现")
    _NAME_FIELD_HINTS = ("name", "real_name", "username", "contact", "owner", "姓名", "联系人", "用户")
    _SECRET_FIELD_HINTS = ("secret", "token", "key", "credential", "passwd", "password", "密钥", "令牌")
    _IP_FIELD_HINTS = ("ip", "host", "server", "gateway", "endpoint", "地址")
    _EMAIL_FIELD_HINTS = ("email", "mail", "邮箱")
    _PHONE_FIELD_HINTS = ("phone", "mobile", "tel", "手机号", "电话")
    _ID_FIELD_HINTS = ("id_card", "identity", "证件", "身份证")
    _GENERIC_SECRET_RE = re.compile(r"(?i)\b(?:sk|rk|pk)(?:_[a-z]+)?_[a-z0-9]{12,}\b|AKIA[0-9A-Z]{16}|(?:(?:token|secret|apikey|api_key)[=: ]+[A-Za-z0-9_\-]{12,})")

    def __init__(self):
        pass

    def _is_field_sensitive(self, field_name: str, hints: tuple[str, ...]) -> bool:
        lowered = str(field_name or "").lower()
        return any(hint in lowered for hint in hints)

    def _sanitize_sensitive_text(self, text: Any) -> Any:
        if not isinstance(text, str) or not text:
            return text
        sanitized = re.sub(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b", "test@example.com", text)
        sanitized = re.sub(r"\b1[3-9]\d{9}\b", "13800000000", sanitized)
        sanitized = re.sub(r"\b\d{17}[\dXx]\b", "11010119900307001X", sanitized)
        sanitized = self._GENERIC_SECRET_RE.sub("<REDACTED_SECRET>", sanitized)

        def _replace_ip(match: re.Match) -> str:
            candidate = match.group(0)
            try:
                ip_obj = ipaddress.ip_address(candidate)
            except ValueError:
                return candidate
            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved:
                return candidate
            return "203.0.113.10"

        sanitized = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", _replace_ip, sanitized)
        return sanitized

    def _sanitize_sensitive_value(self, field_name: str, value: Any):
        if isinstance(value, dict):
            changed = False
            sanitized_dict = {}
            for sub_key, sub_value in value.items():
                sanitized_value, sub_changed = self._sanitize_sensitive_value(str(sub_key), sub_value)
                sanitized_dict[sub_key] = sanitized_value
                changed = changed or sub_changed
            return sanitized_dict, changed
        if isinstance(value, list):
            changed = False
            sanitized_list = []
            for item in value:
                sanitized_item, item_changed = self._sanitize_sensitive_value(field_name, item)
                sanitized_list.append(sanitized_item)
                changed = changed or item_changed
            return sanitized_list, changed
        if not isinstance(value, str):
            return value, False

        original = value
        if self._is_field_sensitive(field_name, self._SECRET_FIELD_HINTS):
            sanitized = "<REDACTED_SECRET>"
        elif self._is_field_sensitive(field_name, self._EMAIL_FIELD_HINTS):
            sanitized = "test@example.com"
        elif self._is_field_sensitive(field_name, self._PHONE_FIELD_HINTS):
            sanitized = "13800000000"
        elif self._is_field_sensitive(field_name, self._ID_FIELD_HINTS):
            sanitized = "11010119900307001X"
        elif self._is_field_sensitive(field_name, self._IP_FIELD_HINTS):
            sanitized = "203.0.113.10"
        elif self._is_field_sensitive(field_name, self._NAME_FIELD_HINTS):
            sanitized = "测试用户"
        else:
            sanitized = value
        sanitized = self._sanitize_sensitive_text(sanitized)
        return sanitized, sanitized != original

    def clean_text(self, text: Any) -> str:
        if text is None:
            return ""
        text = str(text).replace("\r", "\n").strip()
        if not text:
            return ""

        text = re.sub(r"[>]+", " ", text)
        text = re.sub(r"\s+", " ", text)
        parts = [p.strip(" |;；,，") for p in re.split(r"\s*\|\s*|；|;", text) if p.strip(" |;；,，")]

        cleaned_parts = []
        for part in parts:
            lowered = part.lower()
            if part.startswith(self._DROPPED_SEGMENT_PREFIXES):
                continue
            if any(keyword in lowered for keyword in self._DROPPED_SEGMENT_KEYWORDS):
                continue
            if any(keyword in part for keyword in self._UNSUPPORTED_KEYWORDS):
                continue
            cleaned_parts.append(part)

        text = "；".join(cleaned_parts) if cleaned_parts else text
        text = re.sub(r"^(以下类型[:：]\s*)", "", text)
        text = re.sub(r"^(需求说明|需求描述|需求)\s*/\s*", "", text)
        text = re.sub(r"\s+", " ", text).strip(" |;；,，")

        if any(keyword in text for keyword in self._UNSUPPORTED_KEYWORDS):
            return ""
        if len(text) <= 2 and any(ch in text for ch in "（）()"):
            return ""
        return text

    def normalize_steps(self, steps_raw: Any, fallback_title: str = "当前业务") -> List[str]:
        if isinstance(steps_raw, str):
            raw_steps = [steps_raw]
        elif isinstance(steps_raw, list):
            raw_steps = []
            for item in steps_raw:
                if isinstance(item, dict):
                    action = self.clean_text(item.get("action", "") or item.get("step", ""))
                    result = self.clean_text(item.get("result", ""))
                    merged = f"{action}，并确认{result}" if action and result else (action or result)
                    if merged:
                        raw_steps.append(merged)
                else:
                    raw_steps.append(str(item))
        else:
            raw_steps = []

        cleaned_steps = []
        seen = set()
        for step in raw_steps:
            step = self.clean_text(step)
            step = re.sub(r"^\d+\s*[.)、．]\s*", "", step)
            if not step:
                continue
            key = step.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned_steps.append(step)

        if not cleaned_steps:
            title = self.clean_text(fallback_title) or "当前业务"
            cleaned_steps = [
                f"进入 {title} 对应功能入口",
                "输入或提交测试数据",
                "查看系统处理结果",
            ]

        return [f"{idx}. {step}" for idx, step in enumerate(cleaned_steps, start=1)]

    def validate_case(self, raw_case: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply constraints to reduce hallucinations and fix common data format issues.
        Returns the (potentially modified) case and a list of fixed issues.
        """
        td = raw_case.get("test_data", {})
        valid = td.get("valid", {})
        invalid = td.get("invalid", {})
        
        issues_fixed = []

        # 1. Regex-based Data Validation & Correction
        for field, value in list(valid.items()):
            if not isinstance(value, str):
                continue
            
            # Phone Check
            if "phone" in field.lower() or "mobile" in field.lower():
                digits = "".join([c for c in value if c.isdigit()])
                if not re.match(self.PATTERNS["phone"], digits):
                    # Attempt a simple fix: take last 11 digits or pad with 0s
                    if len(digits) >= 11:
                        fixed = digits[-11:]
                    else:
                        fixed = digits.ljust(11, "0")
                    
                    if fixed != value:
                        valid[field] = fixed
                        issues_fixed.append(f"Fixed phone format: {value} -> {fixed}")

            # Email Check
            elif "email" in field.lower():
                if not re.match(self.PATTERNS["email"], value):
                    fixed = "test@example.com"
                    valid[field] = fixed
                    issues_fixed.append(f"Fixed invalid email: {value} -> {fixed}")

            # URL Check
            elif "url" in field.lower():
                if not re.match(self.PATTERNS["url"], value):
                    fixed = "https://example.com"
                    valid[field] = fixed
                    issues_fixed.append(f"Fixed invalid URL: {value} -> {fixed}")

        # 2. Logic-based Validation (e.g., amount, ranges)
        amount_fields = ["amount", "price", "count", "balance"]
        for field, value in list(valid.items()):
            if any(af in field.lower() for af in amount_fields):
                try:
                    num_val = float(value)
                    if num_val < 0:
                        valid[field] = 0.0
                        issues_fixed.append(f"Amount cannot be negative: {value} -> 0.0")
                except (ValueError, TypeError):
                    pass

        # 3. Structural Validation
        raw_case["title"] = self.clean_text(raw_case.get("title")) or "未命名测试用例"
        raw_case["precondition"] = self.clean_text(raw_case.get("precondition")) or "系统已完成基础部署，测试数据准备完成。"
        if raw_case["title"] == "未命名测试用例":
            issues_fixed.append("Missing title, added default.")

        original_steps = raw_case.get("steps")
        raw_case["steps"] = self.normalize_steps(original_steps, raw_case["title"])
        if not isinstance(original_steps, list) or not original_steps:
            issues_fixed.append("Missing or invalid steps, rebuilt concrete fallback steps.")
        else:
            issues_fixed.append("Normalized steps to ordered numbered list.")

        expected = raw_case.get("expected_result")
        expected = self.clean_text(expected)
        if not expected:
            raw_case["expected_result"] = "系统按照需求规则处理，并返回明确结果。"
            issues_fixed.append("Missing expected_result, added concrete default.")
        elif any(phrase in expected for phrase in ["与业务描述一致", "系统能够正确完成", "System behaves as required", "系统处理成功"]):
            title = raw_case.get("title") or "当前功能"
            raw_case["expected_result"] = f"{title}执行后应返回明确的页面结果、提示信息或状态变化；若校验失败，应给出可识别的错误提示并拒绝错误数据。"
            issues_fixed.append("Expected result was too generic, replaced with actionable wording.")
        else:
            raw_case["expected_result"] = expected

        redacted = False
        raw_case["title"] = self._sanitize_sensitive_text(raw_case["title"])
        raw_case["precondition"] = self._sanitize_sensitive_text(raw_case["precondition"])
        raw_case["steps"] = [self._sanitize_sensitive_text(step) for step in raw_case["steps"]]
        raw_case["expected_result"] = self._sanitize_sensitive_text(raw_case["expected_result"])
        sanitized_valid, valid_changed = self._sanitize_sensitive_value("valid", valid)
        sanitized_invalid, invalid_changed = self._sanitize_sensitive_value("invalid", invalid)
        valid = sanitized_valid
        invalid = sanitized_invalid
        redacted = valid_changed or invalid_changed
        if raw_case["title"] != self.clean_text(raw_case.get("title")):
            redacted = True
        if raw_case["precondition"] != self.clean_text(raw_case.get("precondition")):
            redacted = True
        if redacted:
            issues_fixed.append("Redacted sensitive data placeholders from generated case.")

        if not raw_case["title"] or any(keyword in raw_case["title"] for keyword in self._UNSUPPORTED_KEYWORDS):
            raw_case["title"] = "未命名测试用例"

        # Attach metadata about fixes
        if issues_fixed:
            raw_case["validation_trace"] = issues_fixed
            logger.debug(f"Validation fixes applied: {issues_fixed}")

        td["valid"] = valid
        td["invalid"] = invalid
        raw_case["test_data"] = td
        return raw_case

    def check_logic_consistency(self, steps: List[str], expected: str) -> Optional[str]:
        """
        Heuristic check for obvious logical contradictions between steps and expected result.
        Returns a feedback string if a contradiction is found.
        """
        steps_text = " ".join(steps).lower()
        expected_text = expected.lower()

        # Case 1: Negative keywords in steps but positive in expected
        neg_keywords = ["错误", "失败", "拒绝", "非法", "无效", "error", "fail", "invalid", "reject"]
        pos_keywords = ["成功", "通过", "接收", "有效", "success", "pass", "valid", "accept"]

        # If it's an 'invalid' test (based on steps) but expects success
        if any(nk in steps_text for nk in neg_keywords) and any(pk in expected_text for pk in pos_keywords):
            if "无效" in steps_text or "非法" in steps_text:
                 return "逻辑矛盾：测试步骤包含无效输入，但预期结果却是‘成功’。"

        return None
