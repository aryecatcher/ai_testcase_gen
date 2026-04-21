import os
import re
from typing import Dict, Any, List, Optional
import httpx
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

class FeishuClient:
    def __init__(
        self,
        app_id: str = "",
        app_secret: str = "",
        app_token: str = "",
        table_id: str = "",
        spreadsheet_token: str = "",
        sheet_id: str = "",
        document_id: str = "",
        tenant_access_token: str = "",
        base_url: str = "",
    ):
        self.app_id = app_id or os.getenv("FEISHU_APP_ID", "")
        self.app_secret = app_secret or os.getenv("FEISHU_APP_SECRET", "")
        self.app_token = app_token or os.getenv("FEISHU_APP_TOKEN", "")
        self.table_id = table_id or os.getenv("FEISHU_TABLE_ID", "")
        self.spreadsheet_token = spreadsheet_token or os.getenv("FEISHU_SPREADSHEET_TOKEN", "")
        self.sheet_id = sheet_id or os.getenv("FEISHU_SHEET_ID", "")
        self.document_id = document_id or os.getenv("FEISHU_DOCUMENT_ID", "")
        self.tenant_access_token = tenant_access_token or os.getenv("FEISHU_TENANT_TOKEN", "")
        self.base_url = (base_url or os.getenv("FEISHU_OPEN_BASE_URL", "https://open.feishu.cn")).rstrip("/")
        self._cached_token: str = ""
        self.last_error: str = ""

    @staticmethod
    def _normalize_text_list(values: List[Any]) -> List[str]:
        normalized: List[str] = []
        for value in values or []:
            if value is None:
                continue
            text = str(value).strip()
            if not text or text.lower() == "none":
                continue
            normalized.append(text)
        return normalized

    def _ensure_auth(self) -> bool:
        if self.tenant_access_token:
            self.last_error = ""
            return True
        if self.app_id and self.app_secret:
            self.last_error = ""
            return True
        self.last_error = "缺少飞书认证信息：需要 tenant_access_token 或 app_id/app_secret。"
        logger.warning("Feishu credentials missing. Need tenant_access_token or app_id/app_secret.")
        return False

    def _headers(self, token: str) -> Dict[str, str]:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        }

    def _request(self, method: str, url: str, token: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with httpx.Client(timeout=60.0, trust_env=False) as client:
            resp = client.request(method, url, json=payload, headers=self._headers(token))
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu API failed: {data}")
        return data

    @staticmethod
    def compare_headers(expected: List[str], actual: List[str]) -> Dict[str, List[str]]:
        exp = FeishuClient._normalize_text_list(expected)
        act = FeishuClient._normalize_text_list(actual)
        missing = [x for x in exp if x not in act]
        extra = [x for x in act if x not in exp]
        matched = [x for x in exp if x in act]
        return {"missing": missing, "extra": extra, "matched": matched}

    def _get_tenant_access_token(self) -> Optional[str]:
        if self.tenant_access_token:
            return self.tenant_access_token
        if self._cached_token:
            return self._cached_token
        if not (self.app_id and self.app_secret):
            return None
        try:
            with httpx.Client(timeout=30.0, trust_env=False) as client:
                resp = client.post(
                    f"{self.base_url}/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": self.app_id, "app_secret": self.app_secret},
                    headers={"Content-Type": "application/json; charset=utf-8"},
                )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                self.last_error = f"飞书认证失败：{data}"
                logger.error(f"Feishu auth failed: {data}")
                return None
            self._cached_token = data.get("tenant_access_token", "")
            self.last_error = ""
            return self._cached_token
        except Exception as e:
            self.last_error = f"飞书认证异常：{e}"
            logger.error(f"Feishu auth error: {e}")
            return None

    def push_records(self, records_json: dict) -> bool:
        if not (self.app_token and self.table_id):
            logger.warning("Feishu bitable app_token/table_id not configured.")
            return False
        if not self._ensure_auth():
            return False
        token = self._get_tenant_access_token()
        if not token:
            return False
        records = records_json.get("records", []) if isinstance(records_json, dict) else []
        if not isinstance(records, list) or not records:
            logger.warning("Feishu push skipped: no records.")
            return False

        url = f"{self.base_url}/open-apis/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records/batch_create"
        success_count = 0
        try:
            for idx in range(0, len(records), 500):
                chunk = records[idx: idx + 500]
                self._request("POST", url, token, {"records": chunk})
                success_count += len(chunk)
            logger.info(f"Feishu push success: {success_count} records")
            return True
        except Exception as e:
            logger.error(f"Feishu push error: {e}")
            return False

    def detect_sheet_id(self, spreadsheet_token: str = "") -> Optional[str]:
        spreadsheet_token = spreadsheet_token or self.spreadsheet_token
        if not spreadsheet_token:
            return None
        if not self._ensure_auth():
            return None
        token = self._get_tenant_access_token()
        if not token:
            return None

        candidate_urls = [
            f"{self.base_url}/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query",
            f"{self.base_url}/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/metainfo",
        ]
        for url in candidate_urls:
            try:
                data = self._request("GET", url, token)
                payload = data.get("data", {}) or {}
                sheets = payload.get("sheets") or payload.get("sheet_list") or payload.get("items") or []
                if isinstance(sheets, list) and sheets:
                    first = sheets[0] or {}
                    sheet_id = first.get("sheet_id") or first.get("sheetId") or first.get("id")
                    if sheet_id:
                        return sheet_id
                if payload.get("sheetToken"):
                    return payload.get("sheetToken")
            except Exception:
                continue
        return None

    def create_spreadsheet(self, title: str, folder_token: str = "") -> Optional[Dict[str, str]]:
        if not self._ensure_auth():
            return None
        token = self._get_tenant_access_token()
        if not token:
            return None
        payload: Dict[str, Any] = {"title": title or "AI 测试用例导出"}
        if folder_token:
            payload["folder_token"] = folder_token
        url = f"{self.base_url}/open-apis/sheets/v3/spreadsheets"
        try:
            data = self._request("POST", url, token, payload)
            body = data.get("data", {}) or {}
            spreadsheet = body.get("spreadsheet", {}) or body
            spreadsheet_token = spreadsheet.get("spreadsheet_token") or spreadsheet.get("spreadsheetToken") or body.get("spreadsheet_token")
            sheet_id = self.detect_sheet_id(spreadsheet_token) if spreadsheet_token else None
            return {"spreadsheet_token": spreadsheet_token or "", "sheet_id": sheet_id or ""}
        except Exception as e:
            logger.error(f"Feishu create spreadsheet error: {e}")
            return None

    def get_sheet_headers(self, spreadsheet_token: str = "", sheet_id: str = "", end_col: str = "Z") -> List[str]:
        spreadsheet_token = spreadsheet_token or self.spreadsheet_token
        sheet_id = sheet_id or self.sheet_id or self.detect_sheet_id(spreadsheet_token)
        if not (spreadsheet_token and sheet_id):
            return []
        if not self._ensure_auth():
            return []
        token = self._get_tenant_access_token()
        if not token:
            return []
        range_text = f"{sheet_id}!A1:{end_col}1"
        url = f"{self.base_url}/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{range_text}"
        try:
            data = self._request("GET", url, token)
            values = (((data.get("data", {}) or {}).get("valueRange", {}) or {}).get("values", []) or [])
            return self._normalize_text_list(values[0] if values else [])
        except Exception as e:
            logger.error(f"Feishu get sheet headers error: {e}")
            return []

    def get_bitable_field_names(self, app_token: str = "", table_id: str = "") -> List[str]:
        app_token = app_token or self.app_token
        table_id = table_id or self.table_id
        if not (app_token and table_id):
            return []
        if not self._ensure_auth():
            return []
        token = self._get_tenant_access_token()
        if not token:
            return []
        url = f"{self.base_url}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields?page_size=100"
        try:
            data = self._request("GET", url, token)
            items = (((data.get("data", {}) or {}).get("items", [])) or [])
            return [str(item.get("field_name", "")).strip() for item in items if str(item.get("field_name", "")).strip()]
        except Exception as e:
            logger.error(f"Feishu get bitable fields error: {e}")
            return []

    def create_bitable_fields(
        self,
        field_names: List[str],
        app_token: str = "",
        table_id: str = "",
        option_values: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, List[str]]:
        app_token = app_token or self.app_token
        table_id = table_id or self.table_id
        result = {"created": [], "failed": []}
        if not (app_token and table_id):
            return result
        if not self._ensure_auth():
            return result
        token = self._get_tenant_access_token()
        if not token:
            return result
        url = f"{self.base_url}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        for name in [str(x).strip() for x in field_names if str(x).strip()]:
            payload = self._infer_bitable_field_payload(name, option_values=option_values or {})
            try:
                self._request(
                    "POST",
                    url,
                    token,
                    payload,
                )
                result["created"].append(name)
            except Exception as e:
                logger.error(f"Feishu create bitable field error for {name}: {e}")
                result["failed"].append(name)
        return result

    def _infer_bitable_field_payload(self, field_name: str, option_values: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
        name = str(field_name).strip()
        lower = name.lower()
        option_values = option_values or {}

        if any(key in name for key in ["生成时间", "创建时间", "更新时间"]) or "time" in lower or "date" in lower:
            return {
                "field_name": name,
                "type": 5,
                "ui_type": "DateTime",
                "property": {"auto_fill": False, "date_formatter": "yyyy/MM/dd HH:mm"},
            }

        if any(key in name for key in ["优先级"]):
            options = option_values.get(name) or ["P0", "P1", "P2", "P3"]
            return {
                "field_name": name,
                "type": 3,
                "ui_type": "SingleSelect",
                "property": {
                    "options": [
                        {"name": opt, "color": idx % 54}
                        for idx, opt in enumerate(options)
                    ]
                },
            }

        if any(key in name for key in ["用例类型"]):
            options = option_values.get(name) or ["Functional", "Interface", "Security", "Performance", "Compatibility", "Usability"]
            return {
                "field_name": name,
                "type": 3,
                "ui_type": "SingleSelect",
                "property": {
                    "options": [
                        {"name": opt, "color": idx % 54}
                        for idx, opt in enumerate(options)
                    ]
                },
            }

        if any(key in name for key in ["质量特性"]):
            options = option_values.get(name) or ["功能性", "性能效率", "兼容性", "易用性", "可靠性", "信息安全性", "维护性", "可移植性"]
            return {
                "field_name": name,
                "type": 3,
                "ui_type": "SingleSelect",
                "property": {
                    "options": [
                        {"name": opt, "color": idx % 54}
                        for idx, opt in enumerate(options)
                    ]
                },
            }

        if any(key in name for key in ["步骤", "预期结果", "前置条件", "正常数据", "异常数据"]):
            return {
                "field_name": name,
                "type": 1,
                "ui_type": "Text",
                "description": "长文本字段，承载步骤、预期或 JSON 数据。",
            }

        if any(key in name for key in ["测试策略"]):
            return {"field_name": name, "type": 1, "ui_type": "Text"}

        if any(key in name for key in ["关联需求链接"]):
            return {"field_name": name, "type": 15, "ui_type": "Url"}

        if any(key in name for key in ["用例ID", "关联需求", "用例标题"]):
            return {"field_name": name, "type": 1, "ui_type": "Text"}

        return {"field_name": name, "type": 1, "ui_type": "Text"}

    def push_sheet_values(self, values: List[List[Any]], spreadsheet_token: str = "", sheet_id: str = "", start_cell: str = "A1") -> bool:
        spreadsheet_token = spreadsheet_token or self.spreadsheet_token
        sheet_id = sheet_id or self.sheet_id or self.detect_sheet_id(spreadsheet_token)
        start_cell = (start_cell or "").strip().upper() or "A1"
        if not re.fullmatch(r"[A-Z]+[1-9]\d*", start_cell):
            start_cell = "A1"
        if not (spreadsheet_token and sheet_id):
            self.last_error = "缺少 Spreadsheet Token 或 Sheet ID。"
            logger.warning("Feishu sheet spreadsheet_token/sheet_id not configured.")
            return False
        if not self._ensure_auth():
            return False
        token = self._get_tenant_access_token()
        if not token:
            return False
        if not values:
            self.last_error = "没有可推送的数据。"
            logger.warning("Feishu sheet push skipped: no values.")
            return False

        def _col_letters(n: int) -> str:
            s = ""
            while n > 0:
                n, rem = divmod(n - 1, 26)
                s = chr(65 + rem) + s
            return s

        rows = len(values)
        cols = max(len(r) for r in values) if values else 1
        end_col = _col_letters(cols)
        end_row = rows + int(''.join(ch for ch in start_cell if ch.isdigit()) or "1") - 1
        range_text = f"{sheet_id}!{start_cell}:{end_col}{end_row}"
        url = f"{self.base_url}/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values"
        try:
            self._request("PUT", url, token, {"valueRange": {"range": range_text, "values": values}})
            self.last_error = ""
            logger.info(f"Feishu sheet push success: {rows} rows")
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Feishu sheet push error: {e}")
            return False

    def create_docx_document(self, title: str, folder_token: str = "") -> Optional[str]:
        if not self._ensure_auth():
            return None
        token = self._get_tenant_access_token()
        if not token:
            return None
        url = f"{self.base_url}/open-apis/docx/v1/documents"
        payload: Dict[str, Any] = {"title": title}
        if folder_token:
            payload["folder_token"] = folder_token
        try:
            data = self._request("POST", url, token, payload)
            doc = data.get("data", {}).get("document", {}) or data.get("data", {})
            return doc.get("document_id") or doc.get("documentId")
        except Exception as e:
            logger.error(f"Feishu doc create error: {e}")
            return None

    def push_doc_text(self, content: str, title: str = "", document_id: str = "", folder_token: str = "") -> bool:
        document_id = document_id or self.document_id
        if not document_id:
            document_id = self.create_docx_document(title or "AI 测试用例导出", folder_token=folder_token)
        if not document_id:
            return False
        if not self._ensure_auth():
            return False
        token = self._get_tenant_access_token()
        if not token:
            return False
        lines = [line for line in (content or "").splitlines() if line.strip()]
        if not lines:
            logger.warning("Feishu doc push skipped: empty content.")
            return False
        children = self._build_doc_children(lines[:500])
        url = f"{self.base_url}/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children"
        try:
            self._request("POST", url, token, {"index": 0, "children": children})
            logger.info(f"Feishu doc push success: {document_id}")
            return True
        except Exception as e:
            logger.error(f"Feishu doc push error: {e}")
            return False

    def push_doc_sections(self, sections: List[Dict[str, Any]], title: str = "", document_id: str = "", folder_token: str = "") -> bool:
        document_id = document_id or self.document_id
        if not document_id:
            document_id = self.create_docx_document(title or "AI 测试用例导出", folder_token=folder_token)
        if not document_id:
            return False
        if not self._ensure_auth():
            return False
        token = self._get_tenant_access_token()
        if not token:
            return False
        children = self._build_doc_section_children(sections[:200])
        if not children:
            return False
        url = f"{self.base_url}/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children"
        try:
            self._request("POST", url, token, {"index": 0, "children": children[:500]})
            logger.info(f"Feishu structured doc push success: {document_id}")
            return True
        except Exception as e:
            logger.error(f"Feishu structured doc push error: {e}")
            return False

    def _build_doc_children(self, lines: List[str]) -> List[Dict[str, Any]]:
        children: List[Dict[str, Any]] = []
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            content = line[:2000]
            if re.match(r"^\d+\.\s+\S+", line):
                children.append({
                    "block_type": 13,
                    "ordered": {"elements": [{"text_run": {"content": content}}]},
                })
            elif re.match(r"^(#+\s+|\d+\.\s+.+)$", line) and len(line) < 100:
                title = re.sub(r"^#+\s*", "", content)
                children.append({
                    "block_type": 3,
                    "heading1": {"elements": [{"text_run": {"content": title}}]},
                })
            elif line.endswith("：") or line.endswith(":"):
                children.append({
                    "block_type": 3,
                    "heading1": {"elements": [{"text_run": {"content": content.rstrip('：:')}}]},
                })
            else:
                children.append({
                    "block_type": 2,
                    "text": {"elements": [{"text_run": {"content": content}}]},
                })
        return children

    def _paragraph(self, content: str) -> Dict[str, Any]:
        return {"block_type": 2, "text": {"elements": [{"text_run": {"content": content[:2000]}}]}}

    def _heading(self, content: str) -> Dict[str, Any]:
        return {"block_type": 3, "heading1": {"elements": [{"text_run": {"content": content[:2000]}}]}}

    def _ordered(self, content: str) -> Dict[str, Any]:
        return {"block_type": 13, "ordered": {"elements": [{"text_run": {"content": content[:2000]}}]}}

    def _build_doc_section_children(self, sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        children: List[Dict[str, Any]] = []
        for section in sections:
            children.append(self._heading(section.get("title", "未命名用例")))
            for meta in section.get("meta", []):
                if meta:
                    children.append(self._paragraph(str(meta)))
            children.append(self._paragraph("步骤"))
            for step in section.get("steps", []):
                step_text = re.sub(r"^\d+\s*[.)、．]\s*", "", str(step).strip())
                if step_text:
                    children.append(self._ordered(step_text))
            expected = section.get("expected_result", "")
            if expected:
                children.append(self._paragraph(f"预期结果：{expected}"))
            methodology = section.get("methodology", "")
            if methodology:
                children.append(self._paragraph(f"测试策略：{methodology}"))
            valid_data = section.get("valid_data", "")
            if valid_data:
                children.append(self._paragraph(f"正常数据：{valid_data}"))
            invalid_data = section.get("invalid_data", "")
            if invalid_data:
                children.append(self._paragraph(f"异常数据：{invalid_data}"))
        return children
