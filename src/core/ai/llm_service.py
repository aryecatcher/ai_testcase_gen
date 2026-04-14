import os
import json
import re
import hashlib
import threading
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI, AsyncOpenAI
from loguru import logger
from typing import List, Dict, Any, Optional
from .prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE, REFINE_PROMPT_TEMPLATE, KG_RULE_EXTRACTION_PROMPT, AI_JUDGE_PROMPT
from ...models.domain import Requirement, RequirementType, ReqSpec
from .req_parser import RequirementParser
from .few_shots import get_examples
from data.storage import save_json, load_json

load_dotenv()

def _to_req_spec_obj(raw) -> Optional[ReqSpec]:
    """兼容 dict 和 ReqSpec 对象"""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return ReqSpec(**raw)
    return raw


_CACHE_SCHEMA_VERSION = "v4_specific_executable_cases"

def _llm_cache_key(req_id: str, kg_constraints: str, scenarios: str) -> str:
    """Stable cache key (do not use builtins.hash — it varies between Python processes)."""
    h = hashlib.sha256()
    for part in (_CACHE_SCHEMA_VERSION, kg_constraints or "", scenarios or ""):
        h.update(part.encode("utf-8"))
    return f"{req_id}_{h.hexdigest()[:24]}"


class LLMService:
    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        self.api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "ollama")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
        
        # 当前版本固定本地运行，生成与判官统一使用 deepseek-r1:7b
        self.model_gen = model or os.getenv("LLM_MODEL_GEN", "deepseek-r1:7b")
        self.model_judge = os.getenv("LLM_MODEL_JUDGE", self.model_gen)
        
        self.client = None
        self.async_client = None
        self.parser = RequirementParser()
        
        # 1. Pre-cache few-shots for performance
        self._few_shots_cache = {
            t: get_examples(t) for t in RequirementType
        }
        self._few_shots_cache[None] = "Examples: None"

        # 2. Persistent cache: Try to load from disk, fallback to in-memory
        self._cache = load_json("llm_cache") or {} 
        self._cache_lock = threading.Lock()

        base_url_lower = self.base_url.lower()
        self._is_local_compatible = any(host in base_url_lower for host in ["localhost", "127.0.0.1"])
        self._config_error: Optional[str] = None
        if self.api_key and self.api_key.lower() == "ollama" and not self._is_local_compatible:
            self._config_error = "当前 Base URL 指向云端接口，但 API Key 仍为本地占位值 'ollama'。请填写真实 DeepSeek API Key，或把 Base URL 改回本地服务。"
        
        # Always initialize client for local models (using dummy key if needed)
        if self._config_error:
            logger.warning(self._config_error)
        elif self.api_key:
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=20.0)
            self.async_client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url, timeout=20.0)
        else:
            # Fallback for local dev if env var missing but intended for local
            if self._is_local_compatible:
                self.client = OpenAI(api_key="ollama", base_url=self.base_url, timeout=20.0)
                self.async_client = AsyncOpenAI(api_key="ollama", base_url=self.base_url, timeout=20.0)
            else:
                logger.warning("No OpenAI API Key provided. Running in MOCK mode.")
        self._local_fast_mode = self._is_local_compatible and self.model_gen == self.model_judge

    def check_connection(self) -> Dict[str, Any]:
        """
        Checks if the LLM connection is valid for both models.
        """
        if self._config_error:
            return {"status": "error", "message": self._config_error}
        if not self.client:
            return {"status": "error", "message": "No API Key provided (Mock Mode)"}

        def _format_error(model_name: str, err: Exception) -> str:
            text = str(err)
            lowered = text.lower()
            if "requires more system memory" in lowered:
                return (
                    f"本地模型 `{model_name}` 加载失败：机器可用内存不足。"
                    f" 当前配置更适合云端 DeepSeek；如果坚持本地运行，请切换到本地可承载的小模型后再试。"
                )
            if "authentication fails" in lowered or "401" in lowered:
                return f"模型 `{model_name}` 鉴权失败：请检查 API Key 是否正确，并确认 Base URL 与服务商匹配。"
            if "connection refused" in lowered or "failed to connect" in lowered:
                return f"模型 `{model_name}` 连接失败：请确认服务地址可访问，或本地模型服务已经启动。"
            return f"模型 `{model_name}` 连接失败：{text}"

        for model_name in [self.model_gen, self.model_judge]:
            try:
                self.client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": "Test"}],
                    max_tokens=5
                )
            except Exception as e:
                return {"status": "error", "message": _format_error(model_name, e)}

        return {"status": "success", "message": f"Connected to {self.model_gen} and {self.model_judge}"}

    def _usage_tokens(self, response) -> int:
        try:
            usage = getattr(response, "usage", None)
            if usage is None:
                return 0
            if isinstance(usage, dict):
                return int(usage.get("total_tokens") or 0)
            return int(getattr(usage, "total_tokens", 0) or 0)
        except Exception:
            return 0

    def _record_parse_failure(self, stage: str, content: str, err: str, meta: Optional[Dict[str, Any]] = None) -> None:
        payload = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "stage": stage,
            "error": err,
            "meta": meta or {},
            "content": content if len(content) <= 50000 else content[:50000],
            "content_len": len(content),
        }
        try:
            existing = load_json("llm_parse_failures")
            if not isinstance(existing, list):
                existing = []
            existing.append(payload)
            save_json("llm_parse_failures", existing[-50:])
        except Exception as e:
            logger.warning(f"Failed to persist llm_parse_failures: {e}")

    def _extract_first_json(self, content: str) -> Any:
        import re

        fence = re.findall(r"```(?:json)?\s*([\s\S]*?)```", content, flags=re.IGNORECASE)
        for block in fence:
            block = block.strip()
            if not block:
                continue
            try:
                return json.loads(block)
            except Exception:
                continue

        def _scan(start_idx: int, open_ch: str, close_ch: str):
            in_str = False
            esc = False
            depth = 0
            for i in range(start_idx, len(content)):
                ch = content[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                    continue
                if ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        return content[start_idx : i + 1]
            return None

        for open_ch, close_ch in (("{", "}"), ("[", "]")):
            start_positions = [m.start() for m in re.finditer(re.escape(open_ch), content)]
            for start in start_positions[:20]:
                snippet = _scan(start, open_ch, close_ch)
                if not snippet:
                    continue
                try:
                    return json.loads(snippet)
                except Exception:
                    continue

        return None

    def _robust_json_load(self, content: str, stage: str = "unknown", meta: Optional[Dict[str, Any]] = None) -> Any:
        """
        Robustly extract JSON from LLM output, handling think tags, markdown fences, etc.
        """
        if not content:
            return {}
            
        # 1. Strip think tags (DeepSeek-R1)
        import re
        content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
        
        # 2. Try direct load
        try:
            return json.loads(content)
        except Exception:
            pass

        extracted = self._extract_first_json(content)
        if extracted is not None:
            return extracted

        logger.error(f"JSON extraction failed at stage={stage}")
        logger.warning(f"Raw LLM content that failed to parse (first 500 chars): {content[:500]}")
        self._record_parse_failure(stage=stage, content=content, err="json_parse_failed", meta=meta)
        
        return {}

    def _normalize_cases(self, data: Any) -> List[Dict[str, Any]]:
        """
        Normalize the extracted JSON data to a list of test cases.
        """
        if isinstance(data, list):
            return data
            
        if isinstance(data, dict):
            # Case-insensitive key matching
            normalized = { (k.strip().lower() if isinstance(k, str) else k): v for k, v in data.items() }
            return normalized.get("test_cases") or normalized.get("cases") or normalized.get("testcases") or []
            
        return []

    def _build_generation_prompt(
        self,
        req: Requirement,
        project_name: str,
        req_spec_obj: Optional[ReqSpec],
        parsed_struct: str,
        kg_constraints: str,
        scenarios: str,
        few_shots: str,
    ) -> str:
        context = (req.cleaned_text or req.original_text or "").strip()
        module_path = req_spec_obj.module_path if req_spec_obj else "Unknown"
        priority = req_spec_obj.priority if req_spec_obj else "P2"
        if self._local_fast_mode:
            compact_constraints = kg_constraints[:800] if kg_constraints else "None"
            compact_scenarios = scenarios[:600] if scenarios else "None"
            return f"""
你是一名测试分析师。请基于以下需求，输出 3~5 条高质量、具体、可执行的中文测试用例。

项目: {project_name}
模块: {module_path}
优先级: {priority}

需求原文:
{context[:1200]}

结构化解析:
{parsed_struct[:1200]}

业务约束:
{compact_constraints}

场景:
{compact_scenarios}

要求:
1. 必须覆盖: 1 条主成功路径，1 条边界/长度/次数限制，1 条异常或负向路径。
2. 步骤必须具体，不要写空话，不要写“验证系统正常”这种泛化句子。
3. 预期结果必须和步骤强关联，明确提示文案/状态变化/拦截结果。
4. 如果需求里出现“11位/长度/锁定/重复/超时/权限”等词，必须体现在用例里。
5. 只返回 JSON，不要解释。

输出格式:
{{
  "test_cases": [
    {{
      "title": "中文标题",
      "precondition": "前置条件",
      "steps": ["步骤1", "步骤2"],
      "expected_result": "预期结果",
      "test_data": {{"valid": {{}}, "invalid": {{}}}},
      "priority": "P1",
      "type": "Functional",
      "methodology": ["Positive", "Boundary", "Negative"]
    }}
  ]
}}
""".strip()

        return USER_PROMPT_TEMPLATE.format(
            project_name=project_name,
            module_path=module_path,
            priority=priority,
            kg_constraints=kg_constraints,
            parsed_struct=parsed_struct,
            scenarios=scenarios,
            few_shots=few_shots,
            context=context[:4000],
        )

    def _looks_generic_case(self, case: Dict[str, Any]) -> bool:
        title = str(case.get("title", "")).strip().lower()
        steps = case.get("steps", [])
        expected = str(case.get("expected_result", "")).strip().lower()
        if not title or "generated case" in title or "test case" in title or "verify" in title:
            return True
        if not steps or len(steps) < 2:
            return True
        if expected in {"success", "system behaves as required.", "success."}:
            return True
        return False

    def _clean_phrase(self, text: str) -> str:
        text = (text or "").strip()
        if any(flag in text for flag in ["暂未确定", "未明确", "本期暂不实现"]):
            return ""
        text = re.sub(r"^(Path|Section|Summary)\s*[:：]\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^(以下类型[:：]\s*)", "", text)
        text = re.sub(r"^(支持|具备|具有|提供)", "", text)
        text = re.sub(r"(来源文件|解析格式|当前片段)\s*[:：]\s*[^|；;]+", "", text)
        text = re.sub(r"\bmandatory\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text)
        text = text.replace("功能功能", "功能")
        text = text.strip(" ,，;；|")
        if len(text) <= 2 and any(ch in text for ch in "()（）"):
            return ""
        return text

    def _dedupe_segments(self, parts: List[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for part in parts:
            p = self._clean_phrase(part)
            if not p:
                continue
            key = p.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
        return out

    def _compact_path(self, *parts: str) -> str:
        cleaned = self._dedupe_segments([p for p in parts if p])
        return "/".join(cleaned)

    def _subject_family(self, subject: str, capabilities: List[str]) -> str:
        joined = f"{subject}|{'|'.join(capabilities)}"
        if any(k in joined for k in ["设备", "监控地图", "报警日志"]):
            return "device"
        if any(k in joined for k in ["任务", "工单", "平库"]):
            return "task"
        if any(k in joined for k in ["日志", "运行记录", "操作记录"]):
            return "log"
        if any(k in joined for k in ["报表", "统计"]):
            return "report"
        if any(k in joined for k in ["用户", "角色", "菜单", "系统管理"]):
            return "system"
        return "generic"

    def _case_budget(self, subject: str, capabilities: List[str], constraints: List[str]) -> int:
        family = self._subject_family(subject, capabilities)
        budget = 2
        if len(capabilities) >= 2:
            budget += 1
        if len(capabilities) >= 4:
            budget += 1
        if len(constraints) >= 2:
            budget += 1
        if family in {"device", "task", "system"}:
            budget += 1
        return max(2, min(budget, 6))

    def _pick_capabilities(self, capabilities: List[str], limit: int = 2) -> List[str]:
        cleaned = self._dedupe_segments(capabilities)
        if len(cleaned) <= limit:
            return cleaned
        priority_keywords = ["列表", "查询", "日志", "详情", "状态", "地图", "任务", "工单", "报表", "用户", "角色", "菜单"]
        scored = []
        for cap in cleaned:
            score = 0
            for i, kw in enumerate(priority_keywords):
                if kw in cap:
                    score += 100 - i
            score += min(len(cap), 20)
            scored.append((score, cap))
        scored.sort(reverse=True)
        return [cap for _, cap in scored[:limit]]

    def _capability_case(self, subject: str, capability: str, priority: str, pre_base: str) -> Dict[str, Any]:
        capability = self._clean_phrase(capability)
        title = f"{capability}校验"
        steps = [
            f"进入 {subject} 对应页面或模块",
            f"执行与“{capability}”直接相关的操作",
            "观察页面展示、返回结果或状态变化",
        ]
        expected = f"系统正确完成“{capability}”，并在页面、详情或返回结果中展示可核对的业务信息；无异常报错。"
        valid_data: Dict[str, Any] = {"capability": capability}

        if "列表" in capability:
            steps = [
                f"进入 {subject} 列表页面",
                "不输入任何筛选条件，执行默认查询或页面初始化加载",
                "检查列表字段、分页信息和首屏数据展示",
            ]
            expected = "列表加载成功，页面展示关键字段、分页控件和首屏记录；无报错、无空白页。"
            valid_data = {"query": "default"}
        elif "报警日志" in capability or ("日志" in capability and "操作" not in capability):
            steps = [
                f"进入 {subject} 的日志查询页面",
                "输入时间范围或日志级别条件后执行查询",
                "打开一条日志详情并核对关键信息",
            ]
            expected = "日志查询成功，仅返回符合条件的记录；日志详情可打开并展示时间、级别、来源和内容等关键信息。"
            valid_data = {"time_range": "today", "level": "ERROR"}
        elif "操作运行记录" in capability or "操作记录" in capability:
            steps = [
                f"进入 {subject} 操作记录页面",
                "按系统来源或时间范围执行筛选查询",
                "核对查询结果并查看任意一条运行记录详情",
            ]
            expected = "仅展示符合筛选条件的运行记录；详情中能看到系统来源、操作类型、时间和结果等信息。"
            valid_data = {"system": "WMS", "time_range": "today"}
        elif "地图" in capability or "监控" in capability:
            steps = [
                f"进入 {subject} 监控地图页面",
                "检查地图是否完成加载，并观察设备点位或状态标记",
                "任选一个设备点位查看状态详情",
            ]
            expected = "监控地图加载成功，设备点位可见；状态颜色或图标与设备实际状态一致，详情信息可正常查看。"
            valid_data = {"view": "map"}
        elif "任务" in capability and "平库" not in capability and "分步" not in capability:
            steps = [
                f"进入 {subject} 任务列表页面",
                "按任务编号或状态执行查询",
                "打开一条任务详情并核对任务状态流转信息",
            ]
            expected = "任务列表查询成功，结果与筛选条件一致；任务详情可查看任务编号、状态、执行时间和关联对象等信息。"
            valid_data = {"task_no": "TASK-001", "status": "处理中"}
        elif "工单" in capability:
            steps = [
                f"进入 {subject} 工单管理页面",
                "按工单编号或状态执行查询",
                "打开一条工单详情并核对工单内容",
            ]
            expected = "工单列表与筛选条件一致；工单详情页可查看工单编号、状态、创建时间和处理信息。"
            valid_data = {"work_order_no": "WO-001"}
        elif "平库任务" in capability:
            steps = [
                f"进入 {subject} 平库任务页面",
                "创建或查询一条平库任务",
                "查看任务状态与执行结果",
            ]
            expected = "平库任务可正常创建或查询，任务状态流转清晰可见，执行结果可追踪。"
            valid_data = {"task_type": "平库"}
        elif "报表" in capability or "查询功能" in capability:
            steps = [
                f"进入 {subject} 报表查询页面",
                "输入查询条件并执行报表查询",
                "检查报表数据、汇总信息或导出入口",
            ]
            expected = "报表查询成功，结果与筛选条件一致；页面展示报表数据及必要汇总信息，导出入口可见。"
            valid_data = {"date_range": "2025-01-01~2025-01-31"}
        elif capability in {"用户", "角色", "菜单"}:
            steps = [
                f"进入 {subject} 页面",
                f"执行“{capability}”相关查询或列表查看操作",
                "检查列表数据与详情信息是否正确",
            ]
            expected = f"{capability}列表可正常展示，相关详情信息可查看，页面无异常提示。"
            valid_data = {"keyword": capability}

        return {
            "title": title,
            "precondition": pre_base,
            "steps": steps,
            "expected_result": expected,
            "test_data": {"valid": valid_data, "invalid": {}},
            "priority": priority,
            "type": "Functional",
            "methodology": ["Positive", "Scenario-Based"],
        }

    def _negative_case(self, subject: str, priority: str, rule_like: str = "", capabilities: Optional[List[str]] = None) -> Dict[str, Any]:
        capabilities = capabilities or []
        title = f"{subject}异常输入校验"
        steps = [
            "构造非法、缺失、重复或不存在的数据输入",
            "提交请求并查看错误提示、状态码或业务状态",
        ]
        expected = "系统拒绝异常输入，不产生脏数据，并返回清晰、可理解的失败提示。"
        invalid = {"input": "非法/缺失/重复数据"}

        joined = f"{subject}|{'|'.join(capabilities)}|{rule_like}"
        if "列表" in joined or "查询" in joined or "报表" in joined or "日志" in joined:
            title = f"{subject}无效筛选条件校验"
            steps = [
                f"进入 {subject} 查询页面",
                "输入非法时间范围、错误关键字或不存在的筛选条件后执行查询",
                "观察页面提示与结果区域",
            ]
            expected = "系统提示筛选条件非法或结果为空；查询失败或返回空结果时，页面状态清晰，无异常崩溃。"
            invalid = {"date_range": "结束时间早于开始时间"}
        elif "设备" in joined or "用户" in joined or "角色" in joined or "菜单" in joined or "工单" in joined:
            title = f"{subject}重复/非法标识校验"
            steps = [
                f"进入 {subject} 新增或保存页面",
                "输入重复编号、非法字符或必填项缺失的数据后提交",
                "观察提示信息与保存结果",
            ]
            expected = "系统阻止保存，给出重复/非法/必填缺失提示，且数据库无新增或脏数据写入。"
            invalid = {"code": "@@@", "required_field": ""}
        elif "任务" in joined:
            title = f"{subject}非法状态流转校验"
            steps = [
                f"进入 {subject} 页面",
                "对不满足前置条件的任务执行启动、完成或取消操作",
                "观察页面提示、任务状态和执行结果",
            ]
            expected = "系统拒绝非法状态流转，提示操作不允许，任务状态保持不变。"
            invalid = {"task_status": "已完成后再次启动"}
        elif rule_like:
            title = f"{subject}规则约束异常校验"
            steps = [
                f"构造违反“{rule_like}”的数据",
                "提交请求并观察校验结果",
            ]
            expected = f"系统拦截违反“{rule_like}”的数据，并给出明确提示；请求不落库。"
            invalid = {"rule": f"违反{rule_like}"}

        return {
            "title": title,
            "precondition": f"{subject} 功能可正常访问",
            "steps": steps,
            "expected_result": expected,
            "test_data": {"valid": {}, "invalid": invalid},
            "priority": priority,
            "type": "Functional",
            "methodology": ["Negative", "Error Guessing"],
        }

    def _module_specific_cases(self, subject: str, capabilities: List[str], priority: str, pre_base: str, rule_like: str = "", max_cases: int = 3) -> List[Dict[str, Any]]:
        family = self._subject_family(subject, capabilities)
        caps = self._pick_capabilities(capabilities, limit=max(2, min(max_cases, 4)))
        cases: List[Dict[str, Any]] = []

        if family == "device":
            cases.append({
                "title": f"{subject}列表默认加载校验",
                "precondition": pre_base,
                "steps": [
                    f"进入 {subject} 列表页面",
                    "不输入筛选条件，执行默认查询或等待页面初始化加载",
                    "检查列表字段、分页控件和首屏记录展示",
                ],
                "expected_result": "列表加载成功，页面展示设备编号、设备名称、设备状态等关键字段；分页控件可见且首屏无异常报错。",
                "test_data": {"valid": {"query": "default"}, "invalid": {}},
                "priority": priority,
                "type": "Functional",
                "methodology": ["Positive", "Scenario-Based"],
            })
            if any("日志" in c for c in caps):
                cases.append({
                    "title": f"{subject}报警日志联动校验",
                    "precondition": pre_base,
                    "steps": [
                        f"进入 {subject} 页面并定位一台处于报警状态的设备",
                        "点击设备详情或日志入口，进入报警日志页面",
                        "核对日志列表与设备关联信息",
                    ],
                    "expected_result": "页面成功跳转或打开报警日志区域，仅显示该设备相关报警记录；日志详情可查看时间、级别和报警内容。",
                    "test_data": {"valid": {"device_status": "报警"}, "invalid": {}},
                    "priority": priority,
                    "type": "Functional",
                    "methodology": ["Positive", "Traceability"],
                })
            elif any("地图" in c or "监控" in c for c in caps):
                cases.append({
                    "title": f"{subject}监控地图渲染校验",
                    "precondition": pre_base,
                    "steps": [
                        f"进入 {subject} 监控地图页面",
                        "等待地图和设备点位加载完成",
                        "任选一个设备点位查看状态详情",
                    ],
                    "expected_result": "监控地图加载成功，设备点位和状态标记可见；点击点位后可查看设备详情，状态显示与地图标记一致。",
                    "test_data": {"valid": {"view": "map"}, "invalid": {}},
                    "priority": priority,
                    "type": "Functional",
                    "methodology": ["Positive", "UI-State"],
                })
            cases.append(self._negative_case(subject, priority, rule_like=rule_like or "", capabilities=["设备", *caps]))
            extras = [c for c in caps if all(k not in c for k in ["日志", "地图", "监控", "列表"])]
            for cap in extras:
                if len(cases) >= max_cases:
                    break
                cases.append(self._capability_case(subject, cap, priority, pre_base))
            return cases[:max_cases]

        if family == "task":
            cases.append({
                "title": f"{subject}任务列表查询校验",
                "precondition": pre_base,
                "steps": [
                    f"进入 {subject} 任务列表页面",
                    "输入任务编号或选择任务状态后执行查询",
                    "打开任意一条任务详情并核对任务基础信息",
                ],
                "expected_result": "查询结果仅返回符合条件的任务；任务详情页可查看任务编号、状态、创建时间和关联对象等关键信息。",
                "test_data": {"valid": {"task_no": "TASK-001", "status": "处理中"}, "invalid": {}},
                "priority": priority,
                "type": "Functional",
                "methodology": ["Positive", "Scenario-Based"],
            })
            cases.append({
                "title": f"{subject}状态流转校验",
                "precondition": pre_base,
                "steps": [
                    "选择一条满足前置条件的任务",
                    "执行开始、完成或关闭等状态流转操作",
                    "刷新页面并核对任务状态变化及操作记录",
                ],
                "expected_result": "任务状态按预期完成流转，页面状态与操作记录同步更新；不允许的流转操作不会被执行。",
                "test_data": {"valid": {"from_status": "待执行", "to_status": "执行中"}, "invalid": {}},
                "priority": priority,
                "type": "Functional",
                "methodology": ["Positive", "State Transition"],
            })
            cases.append(self._negative_case(subject, priority, rule_like=rule_like or "", capabilities=["任务", *caps]))
            extras = [c for c in caps if all(k not in c for k in ["任务", "工单", "平库"])]
            for cap in extras:
                if len(cases) >= max_cases:
                    break
                cases.append(self._capability_case(subject, cap, priority, pre_base))
            return cases[:max_cases]

        if family == "log":
            cases.append({
                "title": f"{subject}条件筛选查询校验",
                "precondition": pre_base,
                "steps": [
                    f"进入 {subject} 页面",
                    "按系统来源、时间范围或日志级别执行筛选查询",
                    "核对返回结果并查看任意一条日志详情",
                ],
                "expected_result": "列表仅显示符合筛选条件的日志；日志详情可展示来源系统、操作类型、时间、结果和日志内容。",
                "test_data": {"valid": {"system": "WMS", "time_range": "today"}, "invalid": {}},
                "priority": priority,
                "type": "Functional",
                "methodology": ["Positive", "Filter"],
            })
            cases.append(self._negative_case(subject, priority, rule_like=rule_like or "", capabilities=["日志", *caps]))
            return cases[:max_cases]

        if family == "report":
            cases.append({
                "title": f"{subject}报表查询结果校验",
                "precondition": pre_base,
                "steps": [
                    f"进入 {subject} 页面",
                    "输入日期范围、关键字或业务筛选条件后执行查询",
                    "核对报表明细、汇总信息及导出入口",
                ],
                "expected_result": "报表查询成功，结果与筛选条件一致；页面展示明细数据及必要汇总信息，导出入口可见且可点击。",
                "test_data": {"valid": {"date_range": "2025-01-01~2025-01-31"}, "invalid": {}},
                "priority": priority,
                "type": "Functional",
                "methodology": ["Positive", "Report"],
            })
            cases.append(self._negative_case(subject, priority, rule_like=rule_like or "", capabilities=["报表", *caps]))
            return cases[:max_cases]

        if family == "system":
            primary = caps[0] if caps else "用户"
            cases.append({
                "title": f"{subject}{primary}列表校验",
                "precondition": pre_base,
                "steps": [
                    f"进入 {subject} 页面",
                    f"打开“{primary}”管理列表并执行查询",
                    "检查列表字段和详情信息展示",
                ],
                "expected_result": f"{primary}列表可正常展示，查询结果与筛选条件一致；详情页可查看关键属性信息。",
                "test_data": {"valid": {"keyword": primary}, "invalid": {}},
                "priority": priority,
                "type": "Functional",
                "methodology": ["Positive", "Admin"],
            })
            if len(caps) > 1:
                secondary = caps[1]
                cases.append({
                    "title": f"{subject}{secondary}关联校验",
                    "precondition": pre_base,
                    "steps": [
                        f"进入 {subject} 页面",
                        f"打开“{secondary}”相关列表或详情页",
                        "检查列表数据、关联关系或可操作项是否正确",
                    ],
                    "expected_result": f"{secondary}相关页面可正常展示，关联数据清晰可见，页面无异常提示。",
                    "test_data": {"valid": {"target": secondary}, "invalid": {}},
                    "priority": priority,
                    "type": "Functional",
                    "methodology": ["Positive", "Association"],
                })
            cases.append(self._negative_case(subject, priority, rule_like=rule_like or "", capabilities=["用户", "角色", "菜单", *caps]))
            return cases[:max_cases]

        return []

    def _heuristic_generate_cases(self, req: Requirement, parsed: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        parsed = parsed or self.parser.parse(req)
        req_spec_obj = _to_req_spec_obj(req.req_spec)
        priority = req_spec_obj.priority if req_spec_obj else "P2"
        module = "Unknown"
        if isinstance(req.extracted_entities, dict):
            module = req.extracted_entities.get("module") or "Unknown"
        elif req.extracted_entities is not None:
            module = getattr(req.extracted_entities, "module", "Unknown") or "Unknown"
        feature = ""
        if isinstance(req.extracted_entities, dict):
            feature = req.extracted_entities.get("feature") or ""
        elif req.extracted_entities is not None:
            feature = getattr(req.extracted_entities, "feature", "") or ""
        field_map = parsed.get("field_map") or {}
        capabilities = [self._clean_phrase(c) for c in (parsed.get("capabilities") or []) if len(c.strip()) >= 2]
        if not capabilities:
            tech_desc = field_map.get("技术要求", "")
            if tech_desc:
                capabilities = [self._clean_phrase(p) for p in re.split(r"[、,，；;]\s*", tech_desc) if len(p.strip()) >= 2]
        capabilities = self._dedupe_segments(capabilities)
        constraints = [
            self._clean_phrase(c) for c in (parsed.get("constraints") or parsed.get("conditions") or [])
            if c and self._clean_phrase(c) not in {"支持", "查询", "显示", "可以"}
        ]
        level2 = field_map.get("Level_2") or module
        level3 = field_map.get("Level_3") or feature or module
        level4 = field_map.get("Level_4") or feature or level3
        check_item = field_map.get("检验项目")
        subject = level4 or check_item or feature or module or "业务功能"
        subject = self._clean_phrase(subject)
        title_base = subject if len(subject) <= 30 else subject[:30]
        pre_path = self._compact_path(level2, level3, subject)
        pre_base = f"{pre_path}模块已部署且基础数据准备完成"
        case_budget = self._case_budget(subject, capabilities, constraints)

        rule_like = next((c for c in constraints if any(k in c for k in ["11位", "长度", "锁定", "超时", "重复", "权限"])), "")
        cases = []
        selected_capabilities = self._pick_capabilities(capabilities, limit=max(2, min(case_budget, 4)))
        specific_cases = self._module_specific_cases(subject, capabilities, priority, pre_base, rule_like=rule_like, max_cases=case_budget)
        if specific_cases:
            cases.extend(specific_cases)
        elif selected_capabilities:
            for cap in selected_capabilities:
                if len(cases) >= case_budget:
                    break
                cases.append(self._capability_case(subject, cap, priority, pre_base))
        else:
            constraint_text = "；".join(constraints[:3]) if constraints else "按需求约束执行"
            cases.append(
                {
                    "title": f"{title_base}成功路径校验",
                    "precondition": pre_base,
                    "steps": [
                        f"进入 {subject} 对应页面或接口",
                        f"按正常业务规则执行操作，重点满足：{constraint_text}",
                        "提交后查看返回结果或页面状态",
                    ],
                    "expected_result": f"系统按 {subject} 业务规则处理成功，页面状态、返回结果或业务记录更新应清晰可见。",
                    "test_data": {"valid": {"input": "满足规则的正常数据"}, "invalid": {}},
                    "priority": priority,
                    "type": "Functional",
                    "methodology": ["Positive", "Rule-Based"],
                }
            )

        if rule_like and len(cases) < case_budget:
            cases.append(
                {
                    "title": f"{title_base}规则约束校验",
                    "precondition": f"{subject} 功能可正常访问",
                    "steps": [
                        f"构造覆盖约束“{rule_like}”的边界或异常输入",
                        "提交请求并观察系统校验结果",
                    ],
                    "expected_result": f"系统严格按照“{rule_like}”进行校验，符合规则的数据可通过，不符合规则的数据被拦截并提示原因。",
                    "test_data": {"valid": {"rule": rule_like}, "invalid": {"rule": f"违反{rule_like}"}},
                    "priority": priority,
                    "type": "Functional",
                    "methodology": ["Boundary", "Equivalence"],
                }
            )

        need_negative = bool(rule_like) or any(
            kw in f"{subject}|{' '.join(capabilities)}|{' '.join(constraints)}"
            for kw in ["登录", "导入", "查询", "检验", "任务", "设备", "权限", "报表"]
        )
        if need_negative and len(cases) < case_budget and not any(any(k in str(c.get("title", "")) for k in ["异常", "非法", "无效", "重复"]) for c in cases):
            cases.append(self._negative_case(subject, priority, rule_like=rule_like, capabilities=selected_capabilities or capabilities))
        dedup = []
        seen = set()
        for c in cases:
            key = (c["title"], c["expected_result"])
            if key in seen:
                continue
            seen.add(key)
            dedup.append(c)
        return dedup[:case_budget]

    def _post_process_cases(self, req: Requirement, cases: List[Dict[str, Any]], parsed: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        normalized = [c for c in cases if isinstance(c, dict)]
        if not normalized:
            return self._heuristic_generate_cases(req, parsed=parsed)
        if len(normalized) < 2 or sum(1 for c in normalized if self._looks_generic_case(c)) >= max(1, len(normalized) // 2):
            heuristic = self._heuristic_generate_cases(req, parsed=parsed)
            merged = normalized + heuristic
            dedup = []
            seen = set()
            for c in merged:
                key = (str(c.get("title", "")).strip(), str(c.get("expected_result", "")).strip())
                if key in seen:
                    continue
                seen.add(key)
                dedup.append(c)
            fallback_budget = self._case_budget(
                getattr(req, "original_text", ""),
                parsed.get("capabilities", []) if parsed else [],
                parsed.get("constraints", []) if parsed else [],
            )
            return dedup[:fallback_budget]
        return normalized[:6]

    def generate_cases(self, req: Requirement, project_name: str = "Demo Project", kg_constraints: str = "None", scenarios: str = "None") -> List[Dict[str, Any]]:
        """
        Generates test cases based on the requirement using LLM.
        """
        # 1. Early Cache Check: Before any processing
        # Use a simpler cache key for early check (before expensive prompt building)
        cache_key = _llm_cache_key(req.id, kg_constraints, scenarios)
        with self._cache_lock:
            if cache_key in self._cache:
                logger.info(f"Cache Hit for requirement: {req.id}")
                cached_data = self._cache[cache_key]
                if isinstance(cached_data, dict) and "test_cases" in cached_data:
                    return cached_data["test_cases"]
                return cached_data # Fallback for old cache format

        # 2. Extract context - Prefer structured data
        parsed = self.parser.parse(req)
        parsed_struct = json.dumps(parsed, ensure_ascii=False, indent=2)
        if self._local_fast_mode:
            return self._heuristic_generate_cases(req, parsed=parsed)
        
        # Use cached few-shots
        req_spec_obj = _to_req_spec_obj(req.req_spec)
        req_type = req_spec_obj.type if req_spec_obj else None
        few_shots = self._few_shots_cache.get(req_type, self._few_shots_cache[None])

        if not self.client:
            return self._mock_generate(req, parsed=parsed, scenarios=scenarios)

        prompt = self._build_generation_prompt(
            req=req,
            project_name=project_name,
            req_spec_obj=req_spec_obj,
            parsed_struct=parsed_struct,
            kg_constraints=kg_constraints,
            scenarios=scenarios,
            few_shots=few_shots,
        )
        
        try:
            logger.info(f"Sending request to LLM for requirement: {req.id}")
            response = self.client.chat.completions.create(
                model=self.model_gen,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0, # Deterministic for speed and stability
                response_format={"type": "json_object"}
            )
            tokens = self._usage_tokens(response)
            content = response.choices[0].message.content
            data = self._robust_json_load(content, stage="generate_cases", meta={"req_id": req.id})
            test_cases = self._post_process_cases(req, self._normalize_cases(data), parsed=parsed)

            # 4. Update cache and persist with Thread Lock
            with self._cache_lock:
                self._cache[cache_key] = {
                    "test_cases": test_cases,
                    "tokens": tokens
                }
                save_json("llm_cache", self._cache)
            return test_cases
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            return self._heuristic_generate_cases(req, parsed=parsed)

    async def async_generate_cases(self, req: Requirement, project_name: str = "Demo Project", kg_constraints: str = "None", scenarios: str = "None") -> tuple:
        """
        Asynchronous generation of test cases using LLM.
        Includes 3 retries for transient errors.
        Returns (test_cases, total_tokens).
        """
        cache_key = _llm_cache_key(req.id, kg_constraints, scenarios)
        if cache_key in self._cache:
            cached_data = self._cache[cache_key]
            if isinstance(cached_data, dict) and "test_cases" in cached_data:
                return cached_data["test_cases"], cached_data.get("tokens", 0)
            return cached_data, 0

        parsed = self.parser.parse(req)
        parsed_struct = json.dumps(parsed, ensure_ascii=False, indent=2)
        if self._local_fast_mode:
            return self._heuristic_generate_cases(req, parsed=parsed), 0
        if not self.async_client:
            return self._heuristic_generate_cases(req, parsed=parsed), 0
        
        req_spec_obj = _to_req_spec_obj(req.req_spec)
        few_shots = self._few_shots_cache.get(req_spec_obj.type if req_spec_obj else None, self._few_shots_cache[None])
        user_prompt = self._build_generation_prompt(
            req=req,
            project_name=project_name,
            req_spec_obj=req_spec_obj,
            parsed_struct=parsed_struct,
            kg_constraints=kg_constraints,
            scenarios=scenarios,
            few_shots=few_shots,
        )

        max_attempts = 1 if self._local_fast_mode else 3
        for attempt in range(max_attempts):
            try:
                response = await self.async_client.chat.completions.create(
                    model=self.model_gen,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.0
                )
                
                tokens = self._usage_tokens(response)
                content = response.choices[0].message.content
                data = self._robust_json_load(content, stage="async_generate_cases", meta={"req_id": req.id, "attempt": attempt + 1})
                test_cases = self._post_process_cases(req, self._normalize_cases(data), parsed=parsed)
                
                # Update Cache
                with self._cache_lock:
                    self._cache[cache_key] = {
                        "test_cases": test_cases,
                        "tokens": tokens
                    }
                    save_json("llm_cache", self._cache)
                    
                return test_cases, tokens
            except Exception as e:
                logger.warning(f"LLM Attempt {attempt+1} failed for {req.id}: {e}")
                if attempt == max_attempts - 1:
                    return self._heuristic_generate_cases(req, parsed=parsed), 0
                await asyncio.sleep(0.3)
        return [], 0

    def refine_cases(self, failed_cases: List[Dict[str, Any]], feedback: str) -> List[Dict[str, Any]]:
        """
        Refines failed test cases based on user feedback.
        """
        if not self.client:
            return failed_cases # Return original in mock mode

        prompt = REFINE_PROMPT_TEMPLATE.format(
            feedback=feedback,
            failed_cases=json.dumps(failed_cases, ensure_ascii=False)
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model_gen,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0, # Faster and more deterministic
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content
            data = self._robust_json_load(content, stage="refine_cases", meta={"count": len(failed_cases)})
            return self._normalize_cases(data)
        except Exception as e:
            logger.error(f"LLM refinement failed: {e}")
            return []

    async def async_refine_cases(self, current_cases: List[Dict[str, Any]], feedback: str) -> List[Dict[str, Any]]:
        """
        Refine existing cases based on feedback.
        """
        if not self.async_client or not current_cases:
            return current_cases

        refine_prompt = REFINE_PROMPT_TEMPLATE.format(
            feedback=feedback,
            failed_cases=json.dumps(current_cases, ensure_ascii=False)
        )

        for attempt in range(2):
            try:
                response = await self.async_client.chat.completions.create(
                    model=self.model_gen,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": refine_prompt}
                    ],
                    response_format={"type": "json_object"}
                )
                content = response.choices[0].message.content
                data = self._robust_json_load(content, stage="async_refine_cases", meta={"attempt": attempt + 1, "count": len(current_cases)})
                test_cases = self._normalize_cases(data)
                return test_cases or current_cases
            except Exception as e:
                logger.warning(f"Refinement attempt {attempt+1} failed: {e}")
                if attempt == 1:
                    return current_cases
                await asyncio.sleep(1)
        return current_cases

    async def async_judge_cases(self, kg_constraints: str, test_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Use LLM as a judge to verify if test cases follow KG constraints.
        Returns a dict with 'violations', 'gaps', and 'passed'.
        """
        if not self.async_client or not test_cases:
            return {"violations": [], "gaps": [], "passed": True, "tokens": 0}

        judge_prompt = AI_JUDGE_PROMPT.format(
            kg_constraints=kg_constraints,
            test_cases=json.dumps(test_cases, ensure_ascii=False, indent=2)
        )

        try:
            response = await self.async_client.chat.completions.create(
                model=self.model_judge,
                messages=[
                    {"role": "system", "content": "You are a senior QA expert. Be strict and precise."},
                    {"role": "user", "content": judge_prompt}
                ],
                response_format={"type": "json_object"}
            )
            tokens = self._usage_tokens(response)
            content = response.choices[0].message.content
            data = self._robust_json_load(content, stage="async_judge_cases")
            if isinstance(data, dict):
                data["tokens"] = tokens
            
            return data
        except Exception as e:
            logger.error(f"AI Judge failed: {e}")
            return {"violations": [f"判官节点运行异常: {str(e)}"], "gaps": [], "passed": False, "tokens": 0}

    async def extract_kg_rules(self, title: str, steps: List[str], expected: str) -> List[str]:
        """
        Extract atomic rules from a test case for KG storage.
        """
        if not self.async_client:
            return [f"Rule extracted from {title}"]

        prompt = KG_RULE_EXTRACTION_PROMPT.format(
            title=title,
            steps="\n".join(steps),
            expected=expected
        )

        try:
            response = await self.async_client.chat.completions.create(
                model=self.model_judge,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content
            data = self._robust_json_load(content, stage="extract_kg_rules", meta={"title": title})
            if not isinstance(data, dict):
                return []
            return data.get("rules", [])
        except Exception as e:
            logger.error(f"Rule extraction failed: {e}")
            return []

    async def extract_rules_from_feedback_history(self, history: List[Dict[str, Any]]) -> List[str]:
        """
        Extract rules from a collection of user feedback comments.
        """
        if not self.async_client or not history:
            return []
            
        feedback_texts = "\n".join([f"- {h.get('feedback')}" for h in history if h.get("feedback")])
        if not feedback_texts:
            return []
            
        prompt = f"""
        你是一位知识工程师。请从以下用户对测试用例的反馈意见中，提取出通用的业务规则或约束。
        
        === 反馈历史 ===
        {feedback_texts}
        
        === 要求 ===
        1. 提取原子化的、通用的规则。
        2. 忽略特定数据，提取逻辑。
        3. 输出 JSON 格式: {{"rules": ["规则1", "规则2"]}}
        """
        
        try:
            response = await self.async_client.chat.completions.create(
                model=self.model_judge,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            data = self._robust_json_load(response.choices[0].message.content, stage="extract_rules_from_feedback_history", meta={"count": len(history)})
            if not isinstance(data, dict):
                return []
            return data.get("rules", [])
        except Exception as e:
            logger.error(f"Batch rule extraction failed: {e}")
            return []

    def _mock_generate(self, req: Requirement, parsed=None, scenarios=None, error=None) -> List[Dict[str, Any]]:
        """
        Smart Mock Logic:
        If the requirement text already looks like a test case (contains Steps, Expected Result),
        extract and use that content instead of generic placeholders.
        """
        logger.info("Generating Mock Test Cases (Smart Fallback)")
        title_suffix = " (Mock)"
        if error:
            title_suffix += " [Error Fallback]"
            
        text = req.original_text
        
        # 1. Try to extract columns if text is pipe-separated (from Markdown table)
        # Assuming format: ID | Module | Priority | Title | Precondition | Steps | Expected | Data
        parts = [p.strip() for p in text.split('|')]
        
        extracted_steps = []
        extracted_expected = ""
        extracted_pre = "System initialized"
        extracted_title = f"Verify {text[:30].strip()}..."
        extracted_priority = "P2"
        
        req_spec_obj = _to_req_spec_obj(req.req_spec)
        extracted_type = req_spec_obj.type.value if req_spec_obj and req_spec_obj.type else "functional"
        
        # Heuristic mapping based on standard columns seen in user data
        if len(parts) >= 6:
            # Try to find which column is which based on content length or keywords
            # Typical: ID | Module | Prio | Title | Pre | Steps | Expected | Data
            # Index:   0  | 1      | 2    | 3     | 4   | 5     | 6        | 7
            
            # Title is usually 4th (index 3)
            if len(parts) > 3 and len(parts[3]) > 5:
                extracted_title = parts[3]
                
            # Priority usually short P0/P1 at index 2
            if len(parts) > 2 and parts[2].upper() in ["P0", "P1", "P2", "P3"]:
                extracted_priority = parts[2].upper()
                
            # Steps usually index 5 or 6 (long text)
            # Let's look for numbered lists "1. " in parts
            for p in parts:
                if "1." in p and "2." in p:
                    extracted_steps = [s.strip() for s in p.split('\n') if s.strip()]
                    # Cleanup: remove empty strings
                    extracted_steps = [s for s in extracted_steps if len(s) > 2]
                    
            # If no numbered list found, take the longest part as steps
            if not extracted_steps and len(parts) > 5:
                extracted_steps = [parts[5]] # Fallback to index 5
                
            # Expected result usually next to steps
            if len(parts) > 6:
                extracted_expected = parts[6]
                
            # Precondition usually before steps
            if len(parts) > 4:
                extracted_pre = parts[4]

        # 2. Fallback if no table structure found
        if not extracted_steps:
            extracted_steps = [
                "1. Navigate to the relevant module.",
                "2. Perform the action described: " + text[:50] + "...",
                "3. Validate the output."
            ]
            extracted_expected = "System behaves as required."

        return [
            {
                "title": f"{extracted_title}{title_suffix}",
                "precondition": extracted_pre,
                "steps": extracted_steps,
                "expected_result": extracted_expected,
                "test_data": {
                    "valid": {"input": "See extracted constraints"},
                    "invalid": {"input": "Invalid input per constraints"}
                },
                "priority": extracted_priority,
                "type": extracted_type,
                "methodology": ["Heuristic Extraction (Mock)"]
            }
        ]
