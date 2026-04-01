import os
import json
import threading
from typing import List, Dict, Any, Optional
import networkx as nx
from loguru import logger
from .repository import KnowledgeGraphRepository
from .matcher import KeywordMatcher
from ...models.domain import KGNodeModel, KGNodeType

class NetworkXGraphRepository(KnowledgeGraphRepository):
    """
    Phase 0: In-Memory NetworkX Implementation.
    Acts as the legacy/fallback data source.
    """
    def __init__(self, storage_path: str = "data/kg_graph.json", audit_path: str = "data/kg_audit.json"):
        self.graph = nx.DiGraph()
        self.storage_path = storage_path
        self.audit_path = audit_path
        self.audit_log = []
        self._lock = threading.Lock()
        self.matcher = KeywordMatcher() # Initialize KeywordMatcher
        
        # 1. Try loading from disk
        if not self.load_from_disk():
            # 2. Fallback to hardcoded initial graph
            self._build_initial_graph()
            self.save_to_disk()
        self.load_audit_log()
        self._build_matcher_index()

    def _build_matcher_index(self):
        """Builds semantic index for KeywordMatcher from current graph nodes."""
        with self._lock:
            all_nodes = list(self.graph.nodes())
            self.matcher.build_index(all_nodes)

    def save_to_disk(self):
        """Persist the graph structure and audit log to disk."""
        try:
            with self._lock:
                # Save Graph
                data = nx.node_link_data(self.graph)
                os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
                with open(self.storage_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                # Save Audit Log
                with open(self.audit_path, "w", encoding="utf-8") as f:
                    json.dump(self.audit_log, f, ensure_ascii=False, indent=2)
                    
                logger.info(f"Knowledge Graph and Audit Log saved.")
        except Exception as e:
            logger.error(f"Failed to save KG to disk: {e}")

    def load_audit_log(self):
        if os.path.exists(self.audit_path):
            try:
                with open(self.audit_path, "r", encoding="utf-8") as f:
                    self.audit_log = json.load(f)
            except:
                self.audit_log = []

    def load_from_disk(self) -> bool:
        """Load the graph structure from a JSON file."""
        if not os.path.exists(self.storage_path):
            return False
        try:
            with self._lock:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.graph = nx.node_link_graph(data)
                logger.info(f"Knowledge Graph loaded from {self.storage_path} (Nodes: {self.graph.number_of_nodes()})")
                return True
        except Exception as e:
            logger.warning(f"Failed to load KG from disk: {e}")
            return False

    def _build_initial_graph(self):
        """
        Populates the graph with domain knowledge.
        """
        # 1. User Center Domain
        self.graph.add_node("用户中心", type="Module")
        self.graph.add_node("Login", type="Feature", alias=["登录", "Sign In", "客户录入", "Customer Entry"])
        self.graph.add_edge("用户中心", "Login", relation="HAS_FEATURE")
        
        # Rules
        self.graph.add_node("PwdLengthRule", type="Rule", content="Password length must be 8-16 characters")
        self.graph.add_node("LockoutRule", type="Rule", content="Lock account after 3 failed attempts within 1 hour")
        self.graph.add_node("MobileFormatRule", type="Rule", content="手机号必须为11位数字，且符合国家标准")
        self.graph.add_node("NameLengthRule", type="Rule", content="姓名长度限制为2-50个字符")
        self.graph.add_edge("Login", "PwdLengthRule", relation="HAS_RULE")
        self.graph.add_edge("Login", "LockoutRule", relation="HAS_RULE")
        self.graph.add_edge("Login", "MobileFormatRule", relation="HAS_RULE")
        self.graph.add_edge("Login", "NameLengthRule", relation="HAS_RULE")
        
        # Scenarios
        self.graph.add_node("ForgetPwd", type="Exception", content="User clicks forget password flow")
        self.graph.add_node("SQLInjection", type="Security", content="Input SQL in password field")
        self.graph.add_node("DuplicateMobile", type="Exception", content="手机号已存在，提示重复")
        self.graph.add_node("BlacklistCheck", type="Business", content="客户在黑名单中，禁止录入")
        self.graph.add_edge("Login", "ForgetPwd", relation="HAS_SCENARIO")
        self.graph.add_edge("Login", "SQLInjection", relation="HAS_SCENARIO")
        self.graph.add_edge("Login", "DuplicateMobile", relation="HAS_SCENARIO")
        self.graph.add_edge("Login", "BlacklistCheck", relation="HAS_SCENARIO")

        # 2. Payment Domain
        self.graph.add_node("交易中心", type="Module")
        self.graph.add_node("Payment", type="Feature", alias=["支付", "Pay"])
        self.graph.add_edge("交易中心", "Payment", relation="HAS_FEATURE")
        
        # Rules
        self.graph.add_node("PositiveAmount", type="Rule", content="Payment amount must be positive")
        self.graph.add_node("BalanceCheck", type="Rule", content="Check user balance before transaction")
        self.graph.add_edge("Payment", "PositiveAmount", relation="HAS_RULE")
        self.graph.add_edge("Payment", "BalanceCheck", relation="HAS_RULE")
        
        # Scenarios
        self.graph.add_node("InsufficientFunds", type="Exception", content="Balance is less than amount")
        self.graph.add_node("Timeout", type="Exception", content="Payment gateway timeout")
        self.graph.add_edge("Payment", "InsufficientFunds", relation="HAS_SCENARIO")
        self.graph.add_edge("Payment", "Timeout", relation="HAS_SCENARIO")

        # 3. Order Management
        self.graph.add_node("订单中心", type="Module")
        self.graph.add_node("OrderCreate", type="Feature", alias=["下单", "Create Order"])
        self.graph.add_edge("订单中心", "OrderCreate", relation="HAS_FEATURE")

        self.graph.add_node("StockCheck", type="Rule", content="库存必须大于购买数量")
        self.graph.add_edge("OrderCreate", "StockCheck", relation="HAS_RULE")

        self.graph.add_node("StockInsufficient", type="Exception", content="库存不足，下单失败")
        self.graph.add_edge("OrderCreate", "StockInsufficient", relation="HAS_SCENARIO")

        # 4. API Gateway
        self.graph.add_node("API网关", type="Module")
        self.graph.add_node("ApiCall", type="Feature", alias=["接口", "API"])
        self.graph.add_edge("API网关", "ApiCall", relation="HAS_FEATURE")
        
        self.graph.add_node("RateLimit", type="Rule", content="单IP每分钟请求不超过60次")
        self.graph.add_edge("ApiCall", "RateLimit", relation="HAS_RULE")
        
        self.graph.add_node("RateLimitExceeded", type="Exception", content="触发限流，返回 HTTP 429")
        self.graph.add_edge("ApiCall", "RateLimitExceeded", relation="HAS_SCENARIO")

        # 5. Global Baseline (For Path Search Demo)
        self.graph.add_node("安全基线", type="Module")
        self.graph.add_node("CommonSQLi", type="Security", content="通用 SQL 注入 payload 探测")
        self.graph.add_node("CommonXSS", type="Security", content="通用 XSS 脚本注入探测")
        self.graph.add_edge("安全基线", "CommonSQLi", relation="GLOBAL_RULE")
        self.graph.add_edge("安全基线", "CommonXSS", relation="GLOBAL_RULE")
        
        # Link Modules to Baseline
        self.graph.add_edge("用户中心", "安全基线", relation="FOLLOWS")
        self.graph.add_edge("交易中心", "安全基线", relation="FOLLOWS")
        self.graph.add_edge("订单中心", "安全基线", relation="FOLLOWS")
        self.graph.add_edge("API网关", "安全基线", relation="FOLLOWS")

    def find_node_by_keyword(self, keyword: str) -> Optional[KGNodeModel]:
        with self._lock:
            for node, data in self.graph.nodes(data=True):
                if self.matcher.is_match(node, keyword, data.get("alias", [])):
                    return KGNodeModel(
                        id=node,
                        type=data.get("type", KGNodeType.MODULE),
                        name=node,
                        content=data.get("content", ""),
                        alias=data.get("alias", []),
                        metadata=data.get("metadata", {})
                    )
        return None

    def get_related_rules(self, node_id: str) -> List[str]:
        with self._lock:
            if not self.graph.has_node(node_id): return []
            rules = []
            for neighbor in self.graph.successors(node_id):
                node_data = self.graph.nodes[neighbor]
                if node_data.get("type") == "Rule":
                    rules.append(node_data.get("content", ""))
            return rules

    def get_related_scenarios(self, node_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            scenarios = []
            if self.graph.has_node(node_id):
                 for neighbor in self.graph.successors(node_id):
                    node_data = self.graph.nodes[neighbor]
                    edge_data = self.graph.get_edge_data(node_id, neighbor)
                    
                    if edge_data.get("relation") == "HAS_SCENARIO" or node_data.get("type") in ["Exception", "Security", "Business"]:
                        scenarios.append({
                            "type": node_data.get("type", "General"),
                            "name": neighbor,
                            "logic": node_data.get("content", "")
                        })
            return scenarios

    def expand_scenarios_by_path(self, node_id: str, depth: int = 2) -> List[Dict[str, Any]]:
        """
        Implementation of Path Search for Hidden Scenarios.
        Explores the graph up to 'depth' to find related scenarios from parents or siblings.
        """
        with self._lock:
            if not self.graph.has_node(node_id):
                return []

            expanded_scenarios = {} # Use dict to deduplicate by name
            
            # 1. Direct Scenarios
            # Internal call within lock: manually get direct scenarios to avoid nested lock calls
            for neighbor in self.graph.successors(node_id):
                node_data = self.graph.nodes[neighbor]
                if node_data.get("type") in ["Exception", "Security", "Business"]:
                    expanded_scenarios[neighbor] = {
                        "type": node_data.get("type", "General"),
                        "name": neighbor,
                        "logic": node_data.get("content", "")
                    }

            # 2. Path Search (BFS up to depth)
            visited = {node_id}
            queue = [(node_id, 0)]
            
            while queue:
                current_node, current_depth = queue.pop(0)
                if current_depth >= depth:
                    continue
                    
                all_neighbors = list(self.graph.successors(current_node)) + list(self.graph.predecessors(current_node))
                
                for neighbor in all_neighbors:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        node_data = self.graph.nodes[neighbor]
                        
                        if node_data.get("type") in ["Exception", "Security", "Business"]:
                            if neighbor not in expanded_scenarios:
                                expanded_scenarios[neighbor] = {
                                    "type": f"Indirect-{node_data.get('type')}",
                                    "name": neighbor,
                                    "logic": node_data.get("content", "")
                                }
                        
                        if node_data.get("type") in ["Module", "Feature", "Rule"]:
                            queue.append((neighbor, current_depth + 1))
                            
            return list(expanded_scenarios.values())

    def add_rule(self, module_keyword: str, rule_content: str) -> bool:
        """
        Dynamically adds a rule to the graph with audit logging.
        """
        node = self.find_node_by_keyword(module_keyword)
        from datetime import datetime
        
        with self._lock:
            if node:
                target_node = node.id
            else:
                # Create a new module node if not found
                target_node = module_keyword
                self.graph.add_node(target_node, type="Module")
                logger.info(f"Created new module node: {target_node}")

            rule_id = f"Rule_{hash(rule_content)}"
            self.graph.add_node(rule_id, type="Rule", content=rule_content)
            self.graph.add_edge(target_node, rule_id, relation="HAS_RULE")
            
            # Audit Log
            self.audit_log.append({
                "timestamp": datetime.now().isoformat(),
                "action": "ADD_RULE",
                "module": target_node,
                "content": rule_content
            })
            
            logger.info(f"Added rule to {target_node}: {rule_content[:20]}...")
        
        self.save_to_disk()
        return True
