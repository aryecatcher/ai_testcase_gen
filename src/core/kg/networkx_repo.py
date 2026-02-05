from typing import List, Dict, Any, Optional
import networkx as nx
from loguru import logger
from .repository import KnowledgeGraphRepository

class NetworkXGraphRepository(KnowledgeGraphRepository):
    """
    Phase 0: In-Memory NetworkX Implementation.
    Acts as the legacy/fallback data source.
    """
    def __init__(self):
        self.graph = nx.DiGraph()
        self._build_initial_graph()

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

    def find_node_by_keyword(self, keyword: str) -> Optional[Dict[str, Any]]:
        keyword = keyword.lower()
        for node, data in self.graph.nodes(data=True):
            if keyword in node.lower():
                return {"id": node, "props": data}
            aliases = data.get("alias", [])
            for alias in aliases:
                if keyword in alias.lower():
                    return {"id": node, "props": data}
        return None

    def get_related_rules(self, node_id: str) -> List[str]:
        rules = []
        if self.graph.has_node(node_id):
            for neighbor in self.graph.successors(node_id):
                node_data = self.graph.nodes[neighbor]
                edge_data = self.graph.get_edge_data(node_id, neighbor)
                if node_data.get("type") == "Rule" or edge_data.get("relation") == "HAS_RULE":
                    rules.append(f"- {node_data.get('content')}")
        return rules

    def get_related_scenarios(self, node_id: str) -> List[Dict[str, Any]]:
        scenarios = []
        if self.graph.has_node(node_id):
             for neighbor in self.graph.successors(node_id):
                node_data = self.graph.nodes[neighbor]
                edge_data = self.graph.get_edge_data(node_id, neighbor)
                
                if edge_data.get("relation") == "HAS_SCENARIO":
                    scenarios.append({
                        "type": node_data.get("type", "General"),
                        "name": neighbor,
                        "logic": node_data.get("content", "")
                    })
        return scenarios

    def add_rule(self, module_keyword: str, rule_content: str) -> bool:
        """
        Dynamically adds a rule to the graph.
        """
        node = self.find_node_by_keyword(module_keyword)
        if not node:
            logger.warning(f"Cannot add rule, module not found: {module_keyword}")
            return False
            
        node_id = node["id"]
        rule_id = f"Rule_{hash(rule_content)}"
        
        self.graph.add_node(rule_id, type="Rule", content=rule_content)
        self.graph.add_edge(node_id, rule_id, relation="HAS_RULE")
        logger.info(f"Learned new rule for {node_id}: {rule_content}")
        return True
