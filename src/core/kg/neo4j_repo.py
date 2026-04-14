import os
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from loguru import logger
from .repository import KnowledgeGraphRepository
from ...models.domain import KGNodeModel, KGNodeType

load_dotenv()

# Optional import to avoid crashing if neo4j is not installed
try:
    from neo4j import GraphDatabase
    NEO4J_AVAILABLE = True
except ImportError:
    NEO4J_AVAILABLE = False

class Neo4jGraphRepository(KnowledgeGraphRepository):
    """
    Phase 2/3: Production Neo4j Implementation.
    Connects to a real Neo4j database.
    """
    def __init__(self, uri: str = None, user: str = None, password: str = None):
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "password")
        self.driver = None
        
        if NEO4J_AVAILABLE:
            try:
                self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
                logger.info(f"Connected to Neo4j at {self.uri}")
            except Exception as e:
                logger.warning(f"Failed to connect to Neo4j: {e}")
        else:
            logger.warning("Neo4j driver not installed. Install with `pip install neo4j`")

    def close(self):
        if self.driver:
            self.driver.close()

    def find_node_by_keyword(self, keyword: str) -> Optional[KGNodeModel]:
        if not self.driver: return None
        
        query = """
        MATCH (n)
        WHERE toLower(n.name) CONTAINS toLower($keyword) 
           OR ANY(alias IN n.alias WHERE toLower(alias) CONTAINS toLower($keyword))
        RETURN n
        LIMIT 1
        """
        try:
            with self.driver.session() as session:
                result = session.run(query, keyword=keyword)
                record = result.single()
                if record:
                    node = record["n"]
                    props = dict(node)
                    return KGNodeModel(
                        id=node.element_id,
                        type=props.get("type", KGNodeType.MODULE),
                        name=props.get("name", "Unknown"),
                        content=props.get("content", ""),
                        alias=props.get("alias", []),
                        metadata=props.get("metadata", {})
                    )
        except Exception as e:
            logger.error(f"Neo4j Query Error: {e}")
        return None

    def get_related_rules(self, node_id: str) -> List[str]:
        if not self.driver: return []
        
        query = """
        MATCH (n)-[:HAS_RULE]->(r:Rule)
        WHERE elementId(n) = $node_id
        RETURN r.content
        """
        try:
            with self.driver.session() as session:
                result = session.run(query, node_id=node_id)
                # Remove leading "- " since graph_service will handle joining
                return [record['r.content'] for record in result]
        except Exception as e:
            logger.error(f"Neo4j Query Error: {e}")
        return []

    def get_related_scenarios(self, node_id: str) -> List[Dict[str, Any]]:
        if not self.driver: return []
        
        query = """
        MATCH (n)-[:HAS_SCENARIO]->(s)
        WHERE elementId(n) = $node_id
        RETURN s
        """
        try:
            with self.driver.session() as session:
                result = session.run(query, node_id=node_id)
                scenarios = []
                for record in result:
                    node = record["s"]
                    props = dict(node)
                    scenarios.append({
                        "type": props.get("type", "General"),
                        "name": props.get("name", "Unknown"),
                        "logic": props.get("content", "")
                    })
                return scenarios
        except Exception as e:
            logger.error(f"Neo4j Query Error: {e}")
        return []

    def expand_scenarios_by_path(self, node_id: str, depth: int = 2) -> List[Dict[str, Any]]:
        """
        Neo4j Implementation of Path Search.
        Uses Cypher to find nodes labeled Exception/Security/Business within 'depth' hops.
        """
        if not self.driver: return []
        
        # Find all nodes within 'depth' hops that are scenarios
        # We look for nodes with types that imply scenarios or relations that imply them
        query = f"""
        MATCH (n)-[*1..{depth}]-(s)
        WHERE elementId(n) = $node_id 
          AND (s:Exception OR s:Security OR s:Business OR s.type IN ['Exception', 'Security', 'Business'])
        RETURN DISTINCT s
        """
        try:
            with self.driver.session() as session:
                result = session.run(query, node_id=node_id)
                scenarios = []
                for record in result:
                    node = record["s"]
                    props = dict(node)
                    scenarios.append({
                        "type": f"Path-{props.get('type', 'General')}",
                        "name": props.get("name", props.get("id", "Unknown")),
                        "logic": props.get("content", "")
                    })
                return scenarios
        except Exception as e:
            logger.error(f"Neo4j Path Search Error: {e}")
        return []

    def add_rule(self, module_keyword: str, rule_content: str, metadata: Dict[str, Any] = None) -> bool:
        """
        Dynamically adds a rule to Neo4j.
        """
        if not self.driver: return False
        
        query = """
        MERGE (n:Module {name: $module})
        CREATE (r:Rule {content: $content, timestamp: datetime()})
        CREATE (n)-[:HAS_RULE]->(r)
        """
        try:
            with self.driver.session() as session:
                session.run(query, module=module_keyword, content=rule_content)
                logger.info(f"Rule added to Neo4j: {module_keyword} -> {rule_content[:20]}...")
                return True
        except Exception as e:
            logger.error(f"Neo4j Add Rule Error: {e}")
        return False

    def get_related_test_methods(self, node_id: str) -> List[Dict[str, Any]]:
        return []

    def get_related_templates(self, node_id: str) -> List[Dict[str, Any]]:
        return []

    def get_related_failure_modes(self, node_id: str) -> List[Dict[str, Any]]:
        return []

    def get_all_nodes_by_type(self, node_type: str) -> List[KGNodeModel]:
        if not self.driver:
            return []
        query = """
        MATCH (n)
        WHERE n.type = $node_type OR $node_type IN labels(n)
        RETURN n
        """
        try:
            with self.driver.session() as session:
                result = session.run(query, node_type=node_type)
                nodes = []
                for record in result:
                    node = record["n"]
                    props = dict(node)
                    node_type_value = props.get("type", node_type or KGNodeType.MODULE)
                    try:
                        resolved_type = KGNodeType(node_type_value)
                    except Exception:
                        resolved_type = KGNodeType.MODULE
                    nodes.append(
                        KGNodeModel(
                            id=node.element_id,
                            type=resolved_type,
                            name=props.get("name", "Unknown"),
                            content=props.get("content", ""),
                            alias=props.get("alias", []),
                            metadata=props.get("metadata", {}),
                        )
                    )
                return nodes
        except Exception as e:
            logger.error(f"Neo4j Query Error: {e}")
        return []

    def add_knowledge_item(
        self,
        module_keyword: str,
        item_type: str,
        content: str,
        metadata: Dict[str, Any] = None,
        relation: str = None,
        alias: List[str] = None,
    ) -> bool:
        if item_type == "Rule":
            return self.add_rule(module_keyword, content, metadata=metadata)
        return False
