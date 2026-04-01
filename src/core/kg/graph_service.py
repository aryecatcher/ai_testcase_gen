from typing import List, Dict, Any, Optional, Tuple
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
    def __init__(self, use_neo4j: bool = False):
        self._nx_repo = None
        self._neo4j_repo = None
        self.use_neo4j = use_neo4j

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
        result = self._get_repo_for_node(module_keyword)
        if not result:
            return ""
            
        repo, node = result
        logger.info(f"KG Hit ({repo.__class__.__name__}): {module_keyword}")
        rules = repo.get_related_rules(node.id)
        return "\n".join(rules)

    def expand_scenarios(self, module_keyword: str) -> List[Dict[str, Any]]:
        """
        Expands scenarios using Path Search Strategy.
        """
        result = self._get_repo_for_node(module_keyword)
        if not result:
            return []
            
        repo, node = result
        logger.info(f"Expanding scenarios via {repo.__class__.__name__} for {module_keyword}")
        
        try:
            return repo.expand_scenarios_by_path(node.id, depth=3)
        except Exception as e:
            logger.error(f"Path Expansion Error in {repo.__class__.__name__}: {e}")
            # Fallback to direct scenarios if path expansion fails
            return repo.get_related_scenarios(node.id)

    def learn_from_feedback(self, module_keyword: str, rule_content: str) -> bool:
        """
        Updates the KG based on feedback (Self-Correction).
        Synchronizes both repos if enabled.
        """
        success = self.nx_repo.add_rule(module_keyword, rule_content)
        if self.use_neo4j and self.neo4j_repo:
            success = self.neo4j_repo.add_rule(module_keyword, rule_content) or success
        return success

    def validate_test_case(self, feature_name: str, generated_steps: List[str]) -> List[str]:
        """
        Validates generated test steps against explicit rules in the KG.
        """
        conflicts = []
        rules_text = self.get_related_constraints(feature_name)
        if not rules_text:
            return []
            
        rules = [r.strip("- ") for r in rules_text.split("\n") if r.strip()]
        steps_combined = " ".join(generated_steps).lower()
        import re
        
        for rule in rules:
            rule_lower = rule.lower()
            
            # 1. Phone number length (11 digits)
            if "手机号" in rule_lower and "11位" in rule_lower:
                nums = re.findall(r'\d+', steps_combined)
                if "手机号" in steps_combined:
                    has_valid_mobile = any(len(n) == 11 for n in nums)
                    if not has_valid_mobile and nums:
                        conflicts.append(f"违反规则: {rule} (检测到非法长度数字: {', '.join(nums)})")
            
            # 2. General length constraints (e.g., "长度限制为2-50个字符")
            len_match = re.search(r'长度限制为(\d+)-(\d+)个字符', rule_lower)
            if len_match:
                min_len, max_len = int(len_match.group(1)), int(len_match.group(2))
                # Heuristic: Find quoted strings in steps or typical values
                quoted = re.findall(r"['\"](.*?)['\"]", steps_combined)
                for s in quoted:
                    if len(s) < min_len or len(s) > max_len:
                        conflicts.append(f"违反规则: {rule} (检测到不合规长度字符串: '{s}', 长度 {len(s)})")

            # 3. Numeric constraints (e.g., "金额必须为正数")
            if "金额" in rule_lower and "正数" in rule_lower:
                # Find negative numbers or zero
                neg_nums = re.findall(r'-\d+\.?\d*|0\.0*', steps_combined)
                if neg_nums:
                    conflicts.append(f"违反规则: {rule} (检测到非正数金额: {', '.join(neg_nums)})")
                
        return conflicts

