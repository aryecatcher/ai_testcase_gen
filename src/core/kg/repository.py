from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class KnowledgeGraphRepository(ABC):
    """
    Abstract Interface for Knowledge Graph Data Access.
    Phase 1: Abstraction for seamless migration.
    """
    
    @abstractmethod
    def find_node_by_keyword(self, keyword: str) -> Optional[Dict[str, Any]]:
        """
        Find a node by keyword (fuzzy match on name or alias).
        Returns dict with 'id', 'labels', 'properties'.
        """
        pass

    @abstractmethod
    def get_related_rules(self, node_id: str) -> List[str]:
        """
        Get text content of related Rule nodes.
        """
        pass

    @abstractmethod
    def get_related_scenarios(self, node_id: str) -> List[Dict[str, Any]]:
        """
        Get detailed scenario dictionaries.
        """
        pass

    @abstractmethod
    def add_rule(self, module_keyword: str, rule_content: str) -> bool:
        """
        Dynamically adds a rule to the graph (Self-Correction).
        """
        pass
