from typing import List, Dict, Any
from loguru import logger
from .repository import KnowledgeGraphRepository
from .networkx_repo import NetworkXGraphRepository
from .neo4j_repo import Neo4jGraphRepository

class KnowledgeGraphService:
    """
    Orchestrator Service for Knowledge Graph.
    Phase 4: Dual Read Strategy (Neo4j Primary -> NetworkX Fallback).
    """
    def __init__(self, use_neo4j: bool = False):
        self.nx_repo = NetworkXGraphRepository()
        self.neo4j_repo = None
        self.use_neo4j = use_neo4j
        
        if self.use_neo4j:
            self.neo4j_repo = Neo4jGraphRepository()

    def _get_repo(self) -> KnowledgeGraphRepository:
        """
        Returns the active repository.
        Currently defaults to NetworkX as Phase 0/1.
        """
        # In Phase 4, we would try Neo4j first here or inside the methods
        return self.nx_repo

    def get_related_constraints(self, module_keyword: str) -> str:
        """
        Retrieves constraints for a given module/feature using Dual Read Strategy.
        """
        # 1. Try Neo4j if enabled (Phase 4)
        if self.use_neo4j and self.neo4j_repo:
            node = self.neo4j_repo.find_node_by_keyword(module_keyword)
            if node:
                logger.info(f"KG Hit (Neo4j): {module_keyword}")
                rules = self.neo4j_repo.get_related_rules(node["id"])
                return "\n".join(rules)

        # 2. Fallback to NetworkX (Phase 0/1)
        node = self.nx_repo.find_node_by_keyword(module_keyword)
        if node:
            logger.info(f"KG Hit (NetworkX): {module_keyword}")
            # Note: NetworkX repo returns dict with 'id' as the node name string
            rules = self.nx_repo.get_related_rules(node["id"])
            return "\n".join(rules)
            
        return ""

    def expand_scenarios(self, module_keyword: str) -> List[Dict[str, Any]]:
        """
        Expands scenarios using Dual Read Strategy.
        """
        # 1. Try Neo4j
        if self.use_neo4j and self.neo4j_repo:
            node = self.neo4j_repo.find_node_by_keyword(module_keyword)
            if node:
                return self.neo4j_repo.get_related_scenarios(node["id"])

        # 2. Fallback to NetworkX
        node = self.nx_repo.find_node_by_keyword(module_keyword)
        if node:
            return self.nx_repo.get_related_scenarios(node["id"])
            
        return []

    def learn_from_feedback(self, module_keyword: str, rule_content: str) -> bool:
        """
        Updates the KG based on feedback (Self-Correction).
        """
        # For Phase 0, we only update NetworkX
        return self.nx_repo.add_rule(module_keyword, rule_content)

