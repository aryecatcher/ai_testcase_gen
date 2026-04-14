import re
from typing import Dict, Any
from ...models.domain import Requirement

class RequirementParser:
    def _is_noise_fragment(self, text: str) -> bool:
        text = (text or "").strip()
        if not text:
            return True
        if any(flag in text for flag in ["暂未确定", "未明确", "本期暂不实现"]):
            return True
        lowered = text.lower()
        if any(flag in lowered for flag in ["summary:", "section:", "path:", "mandatory"]):
            return True
        if any(flag in text for flag in ["来源文件:", "解析格式:", "当前片段:"]):
            return True
        return False

    def parse(self, req: Requirement) -> Dict[str, Any]:
        """
        Lightweight heuristic SRL-style parsing:
        Extract actors, actions, conditions, results from text.
        """
        text = (req.cleaned_text or req.original_text).strip().lower()
        text_raw = (req.cleaned_text or req.original_text).strip()
        actors = []
        actions = []
        conditions = []
        results = []
        constraints = []
        modules = []
        field_map = {}
        capabilities = []
        structured_module = None
        structured_feature = None
        if isinstance(req.extracted_entities, dict):
            structured_module = req.extracted_entities.get("module")
            structured_feature = req.extracted_entities.get("feature")
            for c in req.extracted_entities.get("constraints", []) or []:
                if isinstance(c, dict):
                    txt = c.get("text") or c.get("value") or c.get("type")
                    if txt:
                        constraints.append(str(txt))
        elif getattr(req, "extracted_entities", None) is not None:
            structured_module = getattr(req.extracted_entities, "module", None)
            structured_feature = getattr(req.extracted_entities, "feature", None)
            for c in getattr(req.extracted_entities, "constraints", []) or []:
                if isinstance(c, dict):
                    txt = c.get("text") or c.get("value") or c.get("type")
                    if txt:
                        constraints.append(str(txt))

        if structured_module:
            modules.append(structured_module)
        if structured_feature:
            actions.append(structured_feature)

        for seg in re.split(r"\s*\|\s*", text_raw):
            if ":" in seg:
                k, v = seg.split(":", 1)
                key = k.strip()
                value = v.strip()
                if key and value:
                    field_map[key] = value
            elif "：" in seg:
                k, v = seg.split("：", 1)
                key = k.strip()
                value = v.strip()
                if key and value:
                    field_map[key] = value

        regex_fields = {
            "Level_2": r"Level_2\s*[:：]\s*([^|]+)",
            "Level_3": r"Level_3\s*[:：]\s*([^|]+)",
            "Level_4": r"Level_4\s*[:：]\s*([^|]+)",
            "检验项目": r"检验项目\s*[:：]\s*([^|]+)",
            "技术要求": r"技术要求\s*[:：]\s*([^|]+)",
        }
        for key, pat in regex_fields.items():
            if key not in field_map:
                m = re.search(pat, text_raw, flags=re.IGNORECASE)
                if m:
                    field_map[key] = m.group(1).strip()

        desc_sources = []
        for k in ["技术要求", "业务描述", "需求描述", "说明", "描述"]:
            if field_map.get(k):
                desc_sources.append(field_map[k])
        if field_map.get("Level_4"):
            modules.append(field_map["Level_4"])
        if field_map.get("Level_3"):
            modules.append(field_map["Level_3"])
        if field_map.get("Level_2"):
            modules.append(field_map["Level_2"])
        if field_map.get("检验项目"):
            actions.append(field_map["检验项目"])

        for desc in desc_sources:
            parts = re.split(r"[、,，；;]\s*", desc)
            for p in parts:
                p = p.strip()
                if len(p) >= 2 and p not in capabilities and not self._is_noise_fragment(p):
                    capabilities.append(p)

        if not capabilities:
            for m in re.finditer(r"(支持|具备|具有|提供)\s*([^|。；;\n]+)", text_raw):
                segment = m.group(2).strip()
                for p in re.split(r"[、,，；;]\s*", segment):
                    p = p.strip()
                    if len(p) >= 2 and p not in capabilities and not self._is_noise_fragment(p):
                        capabilities.append(p)

        # Actors
        for kw in ["用户", "管理员", "operator", "admin", "user"]:
            if kw in text:
                actors.append(kw)

        # Actions
        for kw in ["登录", "注册", "支付", "查询", "重置", "login", "pay", "reset", "query"]:
            if kw in text:
                actions.append(kw)

        for kw in ["仓库", "出库", "入库", "盘点", "任务管理", "设备管理", "wms", "wcs", "api", "接口"]:
            if kw in text:
                modules.append(kw)

        # Conditions
        cond_patterns = [
            r"错误\s*3\s*次",
            r"锁定\s*\d+\s*分钟",
            r"响应时间\s*<\s*\d+",
            r"并发\s*\d+",
            r"\d+\s*位",
            r"长度\s*\d+\s*[-~到]\s*\d+",
            r"超时\s*\d+\s*(秒|分钟|ms)",
            r"重复.*?(失败|提示|拒绝)",
        ]
        for pat in cond_patterns:
            m = re.search(pat, text)
            if m:
                conditions.append(m.group(0))

        # Results
        res_patterns = [
            r"锁定账号",
            r"提示",
            r"成功",
            r"失败",
        ]
        for pat in res_patterns:
            m = re.search(pat, text)
            if m:
                results.append(m.group(0))

        constraint_patterns = [
            r"(必须|需|应当|不得|不能)[^，。；;\n]{0,40}",
            r"(长度|位数|并发|响应时间|超时)[^，。；;\n]{0,30}",
            r"(锁定|限流|黑名单|重复|库存不足)[^，。；;\n]{0,30}",
            r"(支持|具备|提供)[^，。；;\n]{2,40}",
        ]
        for pat in constraint_patterns:
            for m in re.finditer(pat, text_raw, flags=re.IGNORECASE):
                item = m.group(0).strip()
                if item and item not in constraints and not self._is_noise_fragment(item):
                    constraints.append(item)

        return {
            "actors": list(dict.fromkeys(actors)),
            "actions": list(dict.fromkeys(actions)),
            "conditions": conditions,
            "results": results,
            "constraints": constraints[:8],
            "modules": list(dict.fromkeys(modules))[:5],
            "field_map": field_map,
            "capabilities": capabilities[:8],
        }
