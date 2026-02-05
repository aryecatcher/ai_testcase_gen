import re
from typing import Dict, Any
from ...models.domain import Requirement

class RequirementParser:
    def parse(self, req: Requirement) -> Dict[str, Any]:
        """
        Lightweight heuristic SRL-style parsing:
        Extract actors, actions, conditions, results from text.
        """
        text = (req.cleaned_text or req.original_text).strip().lower()
        actors = []
        actions = []
        conditions = []
        results = []

        # Actors
        for kw in ["用户", "管理员", "operator", "admin", "user"]:
            if kw in text:
                actors.append(kw)

        # Actions
        for kw in ["登录", "注册", "支付", "查询", "重置", "login", "pay", "reset", "query"]:
            if kw in text:
                actions.append(kw)

        # Conditions
        cond_patterns = [
            r"错误\s*3\s*次",
            r"锁定\s*\d+\s*分钟",
            r"响应时间\s*<\s*\d+",
            r"并发\s*\d+",
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

        return {
            "actors": actors,
            "actions": actions,
            "conditions": conditions,
            "results": results
        }
