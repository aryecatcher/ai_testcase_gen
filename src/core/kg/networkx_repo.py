import os
import json
import threading
import re
from typing import List, Dict, Any, Optional
import networkx as nx
from loguru import logger
from .repository import KnowledgeGraphRepository
from .matcher import KeywordMatcher
from ...models.domain import KGNodeModel, KGNodeType
from ...config.runtime import KG_AUDIT_PATH, KG_STORAGE_PATH

class NetworkXGraphRepository(KnowledgeGraphRepository):
    """
    Phase 0: In-Memory NetworkX Implementation.
    Acts as the legacy/fallback data source.
    """
    def __init__(self, storage_path: str = KG_STORAGE_PATH, audit_path: str = KG_AUDIT_PATH):
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
        if self._ensure_enhanced_ontology():
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

    def _upsert_node(self, node_id: str, node_type: str, content: str = "", alias: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None):
        data = self.graph.nodes[node_id] if self.graph.has_node(node_id) else {}
        merged_alias = list(dict.fromkeys((data.get("alias", []) or []) + (alias or [])))
        merged_meta = dict(data.get("metadata", {}) or {})
        merged_meta.update(metadata or {})
        self.graph.add_node(
            node_id,
            type=node_type,
            content=content or data.get("content", ""),
            alias=merged_alias,
            metadata=merged_meta,
        )

    def _ensure_edge(self, source: str, target: str, relation: str):
        if not self.graph.has_edge(source, target):
            self.graph.add_edge(source, target, relation=relation)

    def _slugify(self, text: str, fallback: str = "Node") -> str:
        cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", str(text or "").strip())
        cleaned = cleaned.strip("_")
        return cleaned[:48] or fallback

    def _infer_feature_name(self, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        metadata = metadata or {}
        candidates = [
            metadata.get("feature"),
            metadata.get("feature_name"),
            metadata.get("module_feature"),
        ]
        text = str(content or "")
        for pattern in [
            r"([A-Za-z0-9_\-\u4e00-\u9fff]{2,20})(?:功能|流程|模块|接口|页面)",
            r"(登录|支付|审批|权限|下单|注册|导出|导入|查询|创建|删除|编辑|同步)",
        ]:
            match = re.search(pattern, text)
            if match:
                candidates.append(match.group(1))
        for candidate in candidates:
            candidate = str(candidate or "").strip()
            if len(candidate) >= 2:
                return candidate
        return "通用能力"

    def _infer_global_domains(self, item_type: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> List[str]:
        metadata = metadata or {}
        text = f"{item_type} {content} {' '.join(map(str, metadata.values()))}".lower()
        domains = []
        if item_type in {"TestMethod"}:
            domains.append("测试方法库")
        if item_type in {"Template"}:
            domains.append("用例模板库")
        if item_type in {"FailureMode"}:
            domains.append("故障复盘库")
        if item_type in {"Rule", "Security"} and any(flag in text for flag in ["安全", "越权", "注入", "xss", "sqli", "token", "权限"]):
            domains.append("安全基线")
        if item_type in {"Rule", "Business", "Exception"} and any(flag in text for flag in ["审批", "驳回", "会签", "流转"]):
            domains.extend(["测试方法库", "用例模板库"])
        return list(dict.fromkeys(domains))

    def _auto_upgrade_ontology(
        self,
        module_keyword: str,
        item_type: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        item_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        metadata = metadata or {}
        upgrades: List[Dict[str, Any]] = []
        module_id = module_keyword or "Unknown"
        feature_name = self._infer_feature_name(content, metadata)
        feature_alias = [a for a in [metadata.get("feature"), metadata.get("feature_name")] if a]
        feature_id = self._slugify(feature_name, fallback="Feature")
        now = metadata.get("created_at") or metadata.get("timestamp")

        if not self.graph.has_node(module_id):
            self.graph.add_node(module_id, type="Module", metadata={"created_at": now} if now else {})
            upgrades.append({"action": "ADD_MODULE", "node": module_id})

        if feature_name and feature_name not in {"通用能力", module_id}:
            before = self.graph.has_node(feature_id)
            self._upsert_node(
                feature_id,
                "Feature",
                content=f"自动从 {item_type} 候选中提炼的功能节点",
                alias=[feature_name] + feature_alias,
                metadata={"source": "auto_ontology_upgrade", "module": module_id},
            )
            if not before:
                upgrades.append({"action": "ADD_FEATURE", "node": feature_id, "module": module_id})
            if not self.graph.has_edge(module_id, feature_id):
                self._ensure_edge(module_id, feature_id, "HAS_FEATURE")
                upgrades.append({"action": "LINK_MODULE_FEATURE", "source": module_id, "target": feature_id})
            attach_node = feature_id
        else:
            attach_node = module_id

        if item_id and attach_node and not self.graph.has_edge(attach_node, item_id):
            relation_map = {
                "Rule": "HAS_RULE",
                "Business": "HAS_SCENARIO",
                "Exception": "HAS_SCENARIO",
                "Security": "HAS_SCENARIO",
                "FailureMode": "HAS_FAILURE_MODE",
                "Template": "HAS_TEMPLATE",
                "TestMethod": "USES_METHOD",
            }
            relation = relation_map.get(item_type, "HAS_SCENARIO")
            self._ensure_edge(attach_node, item_id, relation)
            upgrades.append({"action": "RELINK_ITEM", "source": attach_node, "target": item_id, "relation": relation})

        for domain in self._infer_global_domains(item_type, content, metadata):
            if not self.graph.has_node(domain):
                continue
            if not self.graph.has_edge(module_id, domain):
                self._ensure_edge(module_id, domain, "FOLLOWS")
                upgrades.append({"action": "LINK_GLOBAL_DOMAIN", "source": module_id, "target": domain})
            if item_id and domain in {"测试方法库", "用例模板库", "故障复盘库"}:
                relation = {
                    "测试方法库": "USES_METHOD",
                    "用例模板库": "HAS_TEMPLATE",
                    "故障复盘库": "HAS_FAILURE_MODE",
                }.get(domain)
                if relation and item_type in {"TestMethod", "Template", "FailureMode"} and not self.graph.has_edge(domain, item_id):
                    self._ensure_edge(domain, item_id, relation)
                    upgrades.append({"action": "REGISTER_GLOBAL_ITEM", "source": domain, "target": item_id, "relation": relation})
        return upgrades

    def _ensure_enhanced_ontology(self) -> bool:
        changed = False
        with self._lock:
            baseline_nodes = set(self.graph.nodes())

            def add_node(node_id: str, node_type: str, content: str = "", alias: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None):
                nonlocal changed
                before = self.graph.has_node(node_id)
                prev_data = dict(self.graph.nodes[node_id]) if before else {}
                self._upsert_node(node_id, node_type, content=content, alias=alias, metadata=metadata)
                after_data = dict(self.graph.nodes[node_id])
                if (not before) or after_data != prev_data:
                    changed = True

            def add_edge(source: str, target: str, relation: str):
                nonlocal changed
                before = self.graph.has_edge(source, target)
                self._ensure_edge(source, target, relation)
                if not before:
                    changed = True

            # Global modules
            add_node("测试方法库", "Module", metadata={"category": "global", "dimension": "methodology"})
            add_node("用例模板库", "Module", metadata={"category": "global", "dimension": "template"})
            add_node("故障复盘库", "Module", metadata={"category": "global", "dimension": "failure"})

            # Core pilot modules
            add_node("权限中心", "Module", alias=["权限管理", "角色权限"])
            add_node("审批中心", "Module", alias=["审批流", "流程审批"])
            add_node("PermissionControl", "Feature", content="角色、菜单、接口权限控制", alias=["权限控制", "角色权限", "菜单权限"])
            add_node("ApprovalFlow", "Feature", content="审批流程发起、流转、驳回、会签", alias=["审批流", "审批流程"])
            add_edge("权限中心", "PermissionControl", "HAS_FEATURE")
            add_edge("审批中心", "ApprovalFlow", "HAS_FEATURE")

            # Test methods
            methods = [
                ("EquivalencePartition", "等价类划分", "按有效/无效输入集合划分测试数据，覆盖正常值与非法值。"),
                ("BoundaryValue", "边界值分析", "围绕最小值、最大值、临界点、次数阈值设计测试。"),
                ("CauseEffect", "因果图", "把输入条件与业务结果映射成约束组合，适合复杂规则。"),
                ("ErrorGuessing", "错误猜测法", "基于历史缺陷、异常输入、空格、特殊字符和重复提交构造测试。"),
                ("StateTransition", "状态迁移", "针对登录锁定、审批流转、任务状态变化等设计测试。"),
            ]
            for node_id, name, content in methods:
                add_node(node_id, "TestMethod", content=content, alias=[name], metadata={"display_name": name})
                add_edge("测试方法库", node_id, "USES_METHOD")

            # Templates
            templates = [
                ("AuthCaseTemplate", "认证功能模板", "字段: ID、模块、前置条件、步骤、预期、有效数据、无效数据。适用于登录、验证码、忘记密码。"),
                ("ApiCaseTemplate", "接口校验模板", "字段: 请求方法、请求头、请求体、状态码、响应结构、异常提示。"),
                ("ApprovalCaseTemplate", "审批流模板", "字段: 发起条件、流转动作、节点状态、通知结果、审计记录。"),
            ]
            for node_id, name, content in templates:
                add_node(node_id, "Template", content=content, alias=[name], metadata={"display_name": name})
                add_edge("用例模板库", node_id, "HAS_TEMPLATE")

            # Failure / postmortem patterns
            failure_modes = [
                ("LoginLockFailure", "登录三次失败未锁定", "连续输错密码达到阈值后未锁定账号，属于安全控制失效。"),
                ("CaptchaMissingFailure", "验证码触发条件失效", "连续失败后未触发验证码，或验证码错误时未给出明确提示。"),
                ("PasswordStorageFailure", "密码明文传输或存储", "登录链路中未加密传输密码，或后端存在明文存储风险。"),
                ("ApprovalSkipFailure", "审批节点被跳过", "审批流某节点被绕过，导致状态与审计记录不一致。"),
            ]
            for node_id, name, content in failure_modes:
                add_node(node_id, "FailureMode", content=content, alias=[name], metadata={"display_name": name, "source": "postmortem"})
                add_edge("故障复盘库", node_id, "HAS_FAILURE_MODE")

            # Attach methods/templates/failure modes to pilot features
            for method_id in ["EquivalencePartition", "BoundaryValue", "ErrorGuessing", "StateTransition"]:
                add_edge("Login", method_id, "USES_METHOD")
            add_edge("Login", "AuthCaseTemplate", "HAS_TEMPLATE")
            add_edge("Login", "LoginLockFailure", "HAS_FAILURE_MODE")
            add_edge("Login", "CaptchaMissingFailure", "HAS_FAILURE_MODE")
            add_edge("Login", "PasswordStorageFailure", "HAS_FAILURE_MODE")

            for method_id in ["EquivalencePartition", "BoundaryValue", "ErrorGuessing"]:
                add_edge("Payment", method_id, "USES_METHOD")
            add_edge("Payment", "ApiCaseTemplate", "HAS_TEMPLATE")

            for method_id in ["StateTransition", "CauseEffect", "ErrorGuessing"]:
                add_edge("ApprovalFlow", method_id, "USES_METHOD")
            add_edge("ApprovalFlow", "ApprovalCaseTemplate", "HAS_TEMPLATE")
            add_edge("ApprovalFlow", "ApprovalSkipFailure", "HAS_FAILURE_MODE")

            for method_id in ["EquivalencePartition", "ErrorGuessing"]:
                add_edge("PermissionControl", method_id, "USES_METHOD")
            add_edge("PermissionControl", "AuthCaseTemplate", "HAS_TEMPLATE")

            # Business scenarios
            scenario_nodes = [
                ("ThirdPartyLogin", "Business", "第三方登录回调、绑定和取消绑定流程", ["第三方登录", "微信登录", "钉钉登录"]),
                ("PermissionEscalation", "Security", "低权限用户尝试访问高权限菜单或接口", ["越权访问", "权限提升"]),
                ("ApprovalRejectFlow", "Business", "审批单被驳回后重新提交与记录追踪", ["驳回重提", "审批回退"]),
            ]
            for node_id, node_type, content, alias in scenario_nodes:
                add_node(node_id, node_type, content=content, alias=alias)
            add_edge("Login", "ThirdPartyLogin", "HAS_SCENARIO")
            add_edge("PermissionControl", "PermissionEscalation", "HAS_SCENARIO")
            add_edge("ApprovalFlow", "ApprovalRejectFlow", "HAS_SCENARIO")

            # Link pilot modules to global knowledge domains
            for module in ["用户中心", "交易中心", "订单中心", "API网关", "权限中心", "审批中心"]:
                add_edge(module, "测试方法库", "FOLLOWS")
                add_edge(module, "用例模板库", "FOLLOWS")
                add_edge(module, "故障复盘库", "FOLLOWS")

            if baseline_nodes != set(self.graph.nodes()):
                changed = True

        return changed

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
            # Stage 1: Fast Literal Match (O(N) but string only)
            for node, data in self.graph.nodes(data=True):
                if self.matcher._literal_match(node, keyword, data.get("alias", [])):
                    return self._create_node_model(node, data)
            
            # Stage 2: Semantic Match (O(1) search via index)
            if self.matcher._use_semantic:
                # find_best_matches will use the index built in _build_matcher_index
                best_matches = self.matcher.find_best_matches(keyword, list(self.graph.nodes()), top_k=1)
                if best_matches:
                    node_id, score = best_matches[0]
                    if score >= self.matcher.SEMANTIC_THRESHOLD:
                        logger.debug(f"Semantic match found: '{keyword}' ~ '{node_id}' (score={score:.3f})")
                        data = self.graph.nodes[node_id]
                        return self._create_node_model(node_id, data)
        return None

    def _create_node_model(self, node_id: str, data: Dict[str, Any]) -> KGNodeModel:
        """Helper to create KGNodeModel from graph data."""
        return KGNodeModel(
            id=node_id,
            type=data.get("type", KGNodeType.MODULE),
            name=node_id,
            content=data.get("content", ""),
            alias=data.get("alias", []),
            metadata=data.get("metadata", {})
        )

    def _context_nodes(self, node_id: str) -> List[str]:
        nodes = {node_id}
        if not self.graph.has_node(node_id):
            return []
        for predecessor in self.graph.predecessors(node_id):
            nodes.add(predecessor)
            for inherited in self.graph.successors(predecessor):
                edge = self.graph.get_edge_data(predecessor, inherited) or {}
                if edge.get("relation") == "FOLLOWS":
                    nodes.add(inherited)
        for successor in self.graph.successors(node_id):
            edge = self.graph.get_edge_data(node_id, successor) or {}
            if edge.get("relation") == "FOLLOWS":
                nodes.add(successor)
        return list(nodes)

    def _collect_related(self, node_id: str, relations: List[str], allowed_types: List[str]) -> List[Dict[str, Any]]:
        related: Dict[str, Dict[str, Any]] = {}
        for root in self._context_nodes(node_id):
            if not self.graph.has_node(root):
                continue
            for neighbor in self.graph.successors(root):
                edge = self.graph.get_edge_data(root, neighbor) or {}
                node_data = self.graph.nodes[neighbor]
                if edge.get("relation") not in relations:
                    continue
                if node_data.get("type") not in allowed_types:
                    continue
                related[neighbor] = {
                    "id": neighbor,
                    "type": node_data.get("type", "General"),
                    "name": node_data.get("metadata", {}).get("display_name", neighbor),
                    "logic": node_data.get("content", ""),
                    "metadata": node_data.get("metadata", {}) or {},
                }
        return list(related.values())

    def get_related_rules(self, node_id: str) -> List[str]:
        with self._lock:
            if not self.graph.has_node(node_id): return []
            rules = []
            for item in self._collect_related(node_id, ["HAS_RULE", "GLOBAL_RULE"], ["Rule", "Security"]):
                content = item.get("logic", "")
                meta = item.get("metadata", {})
                if meta.get("is_priority"):
                    content = f"⚠️ [高优先/重复违规] {content}"
                if meta.get("source") == "postmortem":
                    content = f"[故障复盘] {content}"
                rules.append(content)
            return rules

    def get_related_scenarios(self, node_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            if not self.graph.has_node(node_id):
                return []
            return self._collect_related(
                node_id,
                ["HAS_SCENARIO", "HAS_FAILURE_MODE"],
                ["Exception", "Security", "Business", "FailureMode"],
            )

    def get_related_test_methods(self, node_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            if not self.graph.has_node(node_id):
                return []
            return self._collect_related(node_id, ["USES_METHOD"], ["TestMethod"])

    def get_related_templates(self, node_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            if not self.graph.has_node(node_id):
                return []
            return self._collect_related(node_id, ["HAS_TEMPLATE"], ["Template"])

    def get_related_failure_modes(self, node_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            if not self.graph.has_node(node_id):
                return []
            return self._collect_related(node_id, ["HAS_FAILURE_MODE"], ["FailureMode"])

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
                if node_data.get("type") in ["Exception", "Security", "Business", "FailureMode", "Template", "TestMethod"]:
                    expanded_scenarios[neighbor] = {
                        "type": node_data.get("type", "General"),
                        "name": node_data.get("metadata", {}).get("display_name", neighbor),
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
                        
                        if node_data.get("type") in ["Exception", "Security", "Business", "FailureMode", "Template", "TestMethod"]:
                            if neighbor not in expanded_scenarios:
                                expanded_scenarios[neighbor] = {
                                    "type": f"Indirect-{node_data.get('type')}",
                                    "name": node_data.get("metadata", {}).get("display_name", neighbor),
                                    "logic": node_data.get("content", "")
                                }
                        
                        if node_data.get("type") in ["Module", "Feature", "Rule", "TestMethod", "Template"]:
                            queue.append((neighbor, current_depth + 1))
                            
            return list(expanded_scenarios.values())

    def get_all_nodes_by_type(self, node_type: str) -> List[KGNodeModel]:
        with self._lock:
            nodes = []
            for node, data in self.graph.nodes(data=True):
                if data.get("type") == node_type:
                    nodes.append(self._create_node_model(node, data))
            return nodes

    def add_rule(self, module_keyword: str, rule_content: str, metadata: Dict[str, Any] = None) -> bool:
        """
        Dynamically adds a rule to the graph with audit logging.
        """
        node = self.find_node_by_keyword(module_keyword)
        from datetime import datetime
        
        with self._lock:
            # 1. Check if rule already exists (Self-Healing Check)
            existing_rule_id = None
            for n, d in self.graph.nodes(data=True):
                if d.get("type") == "Rule" and d.get("content") == rule_content:
                    existing_rule_id = n
                    break
            
            if existing_rule_id:
                # Rule exists, upgrade its priority/metadata
                current_meta = self.graph.nodes[existing_rule_id].get("metadata", {})
                current_meta["violation_count"] = current_meta.get("violation_count", 0) + 1
                current_meta["is_priority"] = True # Mark as priority if reported again
                current_meta["last_updated"] = datetime.now().isoformat()
                
                self.graph.nodes[existing_rule_id]["metadata"] = current_meta
                logger.info(f"Upgraded rule priority: {rule_content[:30]}... (Violations: {current_meta['violation_count']})")
                self.save_to_disk()
                return True

            # 2. Add new rule
            if node:
                target_node = node.id
            else:
                # Create a new module node if not found
                target_node = module_keyword
                self.graph.add_node(target_node, type="Module")
                logger.info(f"Created new module node: {target_node}")

            import hashlib
            rule_id = f"Rule_{hashlib.md5(rule_content.encode()).hexdigest()[:8]}"
            
            initial_meta = metadata or {}
            initial_meta.update({
                "created_at": datetime.now().isoformat(),
                "violation_count": 0,
                "is_priority": False
            })
            
            self.graph.add_node(rule_id, type="Rule", content=rule_content, metadata=initial_meta)
            self.graph.add_edge(target_node, rule_id, relation="HAS_RULE")
            
            # Audit Log
            self.audit_log.append({
                "timestamp": datetime.now().isoformat(),
                "action": "ADD_RULE",
                "module": target_node,
                "content": rule_content,
                "metadata": initial_meta
            })
            
            logger.info(f"Added new rule to {target_node}: {rule_content[:20]}...")
            upgrades = self._auto_upgrade_ontology(
                target_node,
                "Rule",
                rule_content,
                metadata=initial_meta,
                item_id=rule_id,
            )
            if upgrades:
                self.audit_log.append({
                    "timestamp": datetime.now().isoformat(),
                    "action": "AUTO_UPGRADE_ONTOLOGY",
                    "module": target_node,
                    "item_type": "Rule",
                    "content": rule_content,
                    "upgrades": upgrades,
                })

        self._build_matcher_index()
        self.save_to_disk()
        return True

    def add_knowledge_item(
        self,
        module_keyword: str,
        item_type: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        relation: Optional[str] = None,
        alias: Optional[List[str]] = None,
    ) -> bool:
        node = self.find_node_by_keyword(module_keyword)
        if not content:
            return False

        type_relation_map = {
            "Rule": "HAS_RULE",
            "Business": "HAS_SCENARIO",
            "Exception": "HAS_SCENARIO",
            "Security": "HAS_SCENARIO",
            "FailureMode": "HAS_FAILURE_MODE",
            "Template": "HAS_TEMPLATE",
            "TestMethod": "USES_METHOD",
        }
        relation = relation or type_relation_map.get(item_type, "HAS_SCENARIO")
        target_node = node.id if node else module_keyword

        from datetime import datetime
        import hashlib

        with self._lock:
            if not self.graph.has_node(target_node):
                self.graph.add_node(target_node, type="Module")

            existing_id = None
            for n, d in self.graph.nodes(data=True):
                if d.get("type") == item_type and d.get("content") == content:
                    existing_id = n
                    break

            if existing_id:
                item_id = existing_id
                current_meta = dict(self.graph.nodes[item_id].get("metadata", {}) or {})
                current_meta.update(metadata or {})
                current_meta["last_updated"] = datetime.now().isoformat()
                self.graph.nodes[item_id]["metadata"] = current_meta
            else:
                item_id = f"{item_type}_{hashlib.md5(content.encode('utf-8')).hexdigest()[:10]}"
                self.graph.add_node(
                    item_id,
                    type=item_type,
                    content=content,
                    alias=alias or [],
                    metadata={
                        "created_at": datetime.now().isoformat(),
                        **(metadata or {}),
                    },
                )
            self._ensure_edge(target_node, item_id, relation)
            self.audit_log.append({
                "timestamp": datetime.now().isoformat(),
                "action": "ADD_KNOWLEDGE_ITEM",
                "module": target_node,
                "item_type": item_type,
                "content": content,
                "relation": relation,
                "metadata": metadata or {},
            })
            upgrades = self._auto_upgrade_ontology(
                target_node,
                item_type,
                content,
                metadata=metadata or {},
                item_id=item_id,
            )
            if upgrades:
                self.audit_log.append({
                    "timestamp": datetime.now().isoformat(),
                    "action": "AUTO_UPGRADE_ONTOLOGY",
                    "module": target_node,
                    "item_type": item_type,
                    "content": content,
                    "upgrades": upgrades,
                })

        self._build_matcher_index()
        self.save_to_disk()
        return True
