from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from ...models.domain import KGNodeModel

class KnowledgeGraphRepository(ABC):
    """
    Abstract Interface for Knowledge Graph Data Access.
    """
    
    @abstractmethod
    def find_node_by_keyword(self, keyword: str) -> Optional[KGNodeModel]:
        """
        Find a node by keyword (fuzzy match on name or alias).
        Returns KGNodeModel or None.
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
    def expand_scenarios_by_path(self, node_id: str, depth: int = 2) -> List[Dict[str, Any]]:
        """
        Find scenarios through path search (hidden/indirect associations).
        """
        pass

    @abstractmethod
    def add_rule(self, module_keyword: str, rule_content: str, metadata: Dict[str, Any] = None) -> bool:
        """
        Dynamically adds a rule to the graph (Self-Correction).
        """
        pass

    @abstractmethod
    def get_all_nodes_by_type(self, node_type: str) -> List[KGNodeModel]:
        """
        Get all nodes of a specific type.
        """
        pass

    def get_related_test_methods(self, node_id: str) -> List[Dict[str, Any]]:
        return []

    def get_related_templates(self, node_id: str) -> List[Dict[str, Any]]:
        return []

    def get_related_failure_modes(self, node_id: str) -> List[Dict[str, Any]]:
        return []
