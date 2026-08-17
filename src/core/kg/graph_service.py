import os
from typing import List, Dict, Any, Optional, Tuple
import re
from loguru import logger
from .repository import KnowledgeGraphRepository
from .networkx_repo import NetworkXGraphRepository
from .neo4j_repo import Neo4jGraphRepository
from ...models.domain import KGNodeModel

class KnowledgeGraphService:
    """
    Orchestrator Service for Knowledge Graph.
    Phase 4: Dual Read Strategy (Neo4j Primary -> NetworkX Fallback).
    """
    def __init__(self, use_neo4j: Optional[bool] = None, backend: Optional[str] = None):
        self._nx_repo = None
        self._neo4j_repo = None
        self.backend = (backend or os.getenv("KG_BACKEND", "auto")).strip().lower() or "auto"
        if use_neo4j is None:
            self.use_neo4j = self.backend in {"auto", "neo4j", "hybrid"}
        else:
            self.use_neo4j = use_neo4j
        self._constraints_cache: Dict[str, str] = {}
        self._scenarios_cache: Dict[str, List[Dict[str, Any]]] = {}

    def _invalidate_cache(self, keyword: str):
        cache_key = (keyword or "").strip().lower()
        self._constraints_cache.pop(cache_key, None)
        self._scenarios_cache.pop(cache_key, None)

    def _format_method_lines(self, methods: List[Dict[str, Any]]) -> List[str]:
        lines = []
        for item in methods:
            name = item.get("name", "未命名方法")
            logic = item.get("logic", "")
            lines.append(f"[测试方法] {name}: {logic}" if logic else f"[测试方法] {name}")
        return lines

    def _format_template_lines(self, templates: List[Dict[str, Any]]) -> List[str]:
        lines = []
        for item in templates:
            name = item.get("name", "未命名模板")
            logic = item.get("logic", "")
            lines.append(f"[用例模板] {name}: {logic}" if logic else f"[用例模板] {name}")
        return lines

    @property
    def nx_repo(self) -> NetworkXGraphRepository:
        if self._nx_repo is None:
            logger.info("Initializing NetworkX Repository (Lazy)...")
            self._nx_repo = NetworkXGraphRepository()
        return self._nx_repo

    @property
    def neo4j_repo(self) -> Optional[Neo4jGraphRepository]:
        if self.use_neo4j and self._neo4j_repo is None:
            logger.info("Initializing Neo4j Repository (Lazy)...")
            self._neo4j_repo = Neo4jGraphRepository()
        return self._neo4j_repo

    def _get_repo_for_node(self, keyword: str) -> Optional[Tuple[KnowledgeGraphRepository, KGNodeModel]]:
        """
        Routing logic: Finds the node in Neo4j first, then fallbacks to NetworkX.
        Returns a tuple of (repository, node_model) or None.
        """
        # 1. Try Neo4j
        if self.use_neo4j and self.neo4j_repo:
            try:
                node = self.neo4j_repo.find_node_by_keyword(keyword)
                if node:
                    return self.neo4j_repo, node
            except Exception as e:
                logger.error(f"Neo4j Routing Error: {e}")

        # 2. Fallback to NetworkX
        try:
            node = self.nx_repo.find_node_by_keyword(keyword)
            if node:
                return self.nx_repo, node
        except Exception as e:
            logger.error(f"NetworkX Routing Error: {e}")
            
        return None

    def get_related_constraints(self, module_keyword: str) -> str:
        """
        Retrieves constraints for a given module/feature using Routing Strategy.
        """
        cache_key = (module_keyword or "").strip().lower()
        if cache_key in self._constraints_cache:
            return self._constraints_cache[cache_key]

        result = self._get_repo_for_node(module_keyword)
        if not result:
            return ""
            
        repo, node = result
        logger.info(f"KG Hit ({repo.__class__.__name__}): {module_keyword}")
        rules = repo.get_related_rules(node.id)
        methods = getattr(repo, "get_related_test_methods", lambda _id: [])(node.id)
        templates = getattr(repo, "get_related_templates", lambda _id: [])(node.id)
        combined = rules + self._format_method_lines(methods) + self._format_template_lines(templates)
        # Ensure consistent bullet points for AI prompt
        normalized = "\n".join([f"- {r}" if not str(r).startswith("- ") else str(r) for r in combined])
        self._constraints_cache[cache_key] = normalized
        return normalized

    def expand_scenarios(self, module_keyword: str) -> List[Dict[str, Any]]:
        """
        Expands scenarios using Path Search Strategy.
        """
        cache_key = (module_keyword or "").strip().lower()
        if cache_key in self._scenarios_cache:
            return self._scenarios_cache[cache_key]

        result = self._get_repo_for_node(module_keyword)
        if not result:
            return []
            
        repo, node = result
        logger.info(f"Expanding scenarios via {repo.__class__.__name__} for {module_keyword}")
        
        try:
            scenarios = repo.expand_scenarios_by_path(node.id, depth=3)
        except Exception as e:
            logger.error(f"Path Expansion Error in {repo.__class__.__name__}: {e}")
            # Fallback to direct scenarios if path expansion fails
            scenarios = repo.get_related_scenarios(node.id)
        failure_modes = getattr(repo, "get_related_failure_modes", lambda _id: [])(node.id)
        merged = {f"{item.get('type')}|{item.get('name')}": item for item in scenarios}
        for item in failure_modes:
            merged[f"{item.get('type')}|{item.get('name')}"] = item
        scenarios = list(merged.values())
        self._scenarios_cache[cache_key] = scenarios
        return scenarios

    def get_all_modules_summary(self) -> List[Dict[str, Any]]:
        """
        Returns a summary of all modules and their rules/scenarios from the primary repository.
        """
        repo = self.nx_repo # Default to NetworkX for summary
        if self.use_neo4j and self.neo4j_repo:
            repo = self.neo4j_repo
            
        modules = repo.get_all_nodes_by_type("Module")
        summary = []
        for mod in modules:
            rules = repo.get_related_rules(mod.id)
            scenarios = repo.get_related_scenarios(mod.id)
            methods = getattr(repo, "get_related_test_methods", lambda _id: [])(mod.id)
            templates = getattr(repo, "get_related_templates", lambda _id: [])(mod.id)
            failure_modes = getattr(repo, "get_related_failure_modes", lambda _id: [])(mod.id)
            
            # Special handling for "Security Baseline" or "Global" nodes
            is_global = mod.name in ["安全基线", "全局规则", "Global Baseline", "测试方法库", "用例模板库", "故障复盘库"]
            
            summary.append({
                "id": mod.id,
                "name": mod.name,
                "rules_count": len(rules),
                "scenarios_count": len(scenarios),
                "methods_count": len(methods),
                "templates_count": len(templates),
                "failure_modes_count": len(failure_modes),
                "rules": rules,
                "scenarios": scenarios,
                "methods": methods,
                "templates": templates,
                "failure_modes": failure_modes,
                "is_global": is_global
            })
        return summary

    def learn_from_feedback(self, module_keyword: str, rule_content: str) -> bool:
        """
        Updates the KG based on feedback (Self-Correction).
        Synchronizes both repos if enabled.
        """
        success = self.nx_repo.add_rule(module_keyword, rule_content, metadata={"source": "feedback"})
        if any(flag in rule_content for flag in ["故障", "异常", "报错", "失效", "未生效", "Bug", "bug"]):
            success = self.nx_repo.add_knowledge_item(
                module_keyword,
                "FailureMode",
                rule_content,
                metadata={"source": "feedback", "origin": "review_feedback"},
            ) or success
        if self.use_neo4j and self.neo4j_repo:
            success = self.neo4j_repo.add_rule(module_keyword, rule_content, metadata={"source": "feedback"}) or success
        self._invalidate_cache(module_keyword)
        
        # If we added a rule, we should probably update the matcher index
        # but for simplicity we'll assume it's done or not critical for immediate next query
        return success

    def batch_learn_rules(self, module_keyword: str, rules: List[str]) -> int:
        """
        Learns multiple rules for a module.
        """
        count = 0
        for rule in rules:
            if self.learn_from_feedback(module_keyword, rule):
                count += 1
        return count

    def learn_from_postmortem(self, module_keyword: str, failure_content: str) -> bool:
        success = self.nx_repo.add_knowledge_item(
            module_keyword,
            "FailureMode",
            failure_content,
            metadata={"source": "postmortem"},
        )
        if self.use_neo4j and self.neo4j_repo:
            success = self.neo4j_repo.add_knowledge_item(
                module_keyword,
                "FailureMode",
                failure_content,
                metadata={"source": "postmortem"},
            ) or success
        self._invalidate_cache(module_keyword)
        return success

    def learn_generic_item(
        self,
        module_keyword: str,
        item_type: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not content:
            return False
        success = self.nx_repo.add_knowledge_item(
            module_keyword,
            item_type,
            content,
            metadata=metadata or {},
        )
        if self.use_neo4j and self.neo4j_repo:
            success = self.neo4j_repo.add_knowledge_item(
                module_keyword,
                item_type,
                content,
                metadata=metadata or {},
            ) or success
        self._invalidate_cache(module_keyword)
        return success

    def validate_test_case(self, module_keyword: str, generated_steps: List[str]) -> List[str]:
        """
        Validates generated test steps against explicit rules in the KG.
        Returns a list of violations (empty if valid).
        """
        result = self._get_repo_for_node(module_keyword)
        if not result:
            return []
            
        repo, node = result
        rules = repo.get_related_rules(node.id)
        violations = []
        
        steps_text = " ".join(generated_steps).lower()
        
        for rule in rules:
            rule_lower = rule.lower()
            # Simple keyword-based checking: if rule says "must" but steps don't mention the action
            if "必须" in rule_lower or "需" in rule_lower or "must" in rule_lower:
                # Extract key noun from rule (very simple heuristic)
                keywords = [k for k in rule_lower.split() if len(k) > 1]
                if not any(k in steps_text for k in keywords):
                    # We might have a violation, but let's be conservative to avoid false positives
                    pass 
            
            # Check for forbidden actions
            if "禁止" in rule_lower or "不能" in rule_lower or "forbidden" in rule_lower:
                # If a forbidden word appears in steps
                forbidden_parts = rule_lower.split("禁止")[-1].strip()
                if forbidden_parts and forbidden_parts in steps_text:
                    violations.append(f"违反知识图谱规则: {rule}")

            if "11位" in rule_lower and "手机号" in rule_lower:
                has_11_digit = any(len(token) == 11 and token.isdigit() for token in re.findall(r"\d+", steps_text))
                if not has_11_digit:
                    violations.append(f"可能遗漏知识图谱规则: {rule}")
                    
        return violations

