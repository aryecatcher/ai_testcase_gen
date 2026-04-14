from typing import List, Dict, Any, TypedDict, Annotated
import asyncio
import operator
from langgraph.graph import StateGraph, END
from loguru import logger

from ...models.domain import Requirement, TestCase, TestCaseStatus, TestDataSets, TestInstruction, ExtractedEntities
from ..ai.llm_service import LLMService
from ..kg.graph_service import KnowledgeGraphService
from .validators import ValidationInterceptor
from .data_synthesizer import DataSynthesizer
from ..ai.optimizer import CaseOptimizer


def _get_module(req: Requirement) -> str:
    """兼容 extracted_entities 为 dict 或对象两种情况"""
    entities = req.extracted_entities
    if entities is None:
        return "Unknown"
    if isinstance(entities, dict):
        return entities.get("module") or "Unknown"
    return getattr(entities, "module", None) or "Unknown"


class GenerationState(TypedDict):
    """State for the generation workflow."""
    requirement: Requirement
    kg_constraints: str
    scenarios: str
    raw_cases: List[Dict[str, Any]]
    final_cases: List[TestCase]
    iteration: int
    feedback: str
    status: str
    trace: Annotated[List[str], operator.add]
    kg_hit: bool

class GenerationWorkflow:
    def __init__(self, llm_service: LLMService, kg_service: KnowledgeGraphService):
        self.llm_service = llm_service
        self.kg_service = kg_service
        self.validator = ValidationInterceptor()
        self.synth = DataSynthesizer()
        self.optimizer = CaseOptimizer()
        
        # Build the graph
        workflow = StateGraph(GenerationState)
        
        # Add nodes
        workflow.add_node("retrieve_context", self.node_retrieve_context)
        workflow.add_node("generate_initial", self.node_generate_initial)
        workflow.add_node("validate_and_augment", self.node_validate_and_augment)
        workflow.add_node("ai_judge", self.node_ai_judge)
        workflow.add_node("optimize", self.node_optimize)
        
        # Add edges
        workflow.set_entry_point("retrieve_context")
        workflow.add_edge("retrieve_context", "generate_initial")
        workflow.add_edge("generate_initial", "validate_and_augment")
        workflow.add_edge("validate_and_augment", "ai_judge")
        
        # Conditional edge for optimization
        workflow.add_conditional_edges(
            "ai_judge",
            self.should_optimize,
            {
                "continue": "optimize",
                "end": END
            }
        )
        
        # Loop back to validation after optimization
        workflow.add_edge("optimize", "validate_and_augment")
        
        self.app = workflow.compile()

    def _local_fast_mode(self) -> bool:
        return bool(
            getattr(self.llm_service, "_is_local_compatible", False)
            and getattr(self.llm_service, "model_gen", "") == getattr(self.llm_service, "model_judge", "")
        )

    async def node_retrieve_context(self, state: GenerationState) -> Dict[str, Any]:
        req = state["requirement"]
        logger.debug(f"node_retrieve_context: req.id={req.id}, type(req.req_spec)={type(req.req_spec)}, req.req_spec={req.req_spec}")
        module = _get_module(req)
        logger.info(f"Step 1: Retrieving KG context for module: {module}")
        
        constraints = self.kg_service.get_related_constraints(module)
        scenarios_list = self.kg_service.expand_scenarios(module)
        scenarios_text = ""
        if scenarios_list:
            scenarios_text = "\n".join([f"- {s.get('type')}: {s.get('name')} ({s.get('logic')})" for s in scenarios_list])
        
        kg_hit = bool(constraints or scenarios_text)
        constraint_count = len([line for line in constraints.splitlines() if line.strip()]) if constraints else 0
        trace_msg = f"💡 [知识图谱] 已定位模块 '{module}'，提取到 {constraint_count} 条业务约束和相关场景。"
        return {
            "kg_constraints": constraints,
            "scenarios": scenarios_text,
            "status": f"已获取知识图谱上下文 ({module})",
            "trace": [trace_msg],
            "kg_hit": kg_hit
        }

    async def node_generate_initial(self, state: GenerationState) -> Dict[str, Any]:
        logger.info(f"Step 2: LLM generating initial cases for {state['requirement'].id}")
        result = await self.llm_service.async_generate_cases(
            req=state["requirement"],
            kg_constraints=state["kg_constraints"],
            scenarios=state["scenarios"]
        )
        tokens = 0
        if isinstance(result, tuple) and len(result) == 2:
            raw_cases, tokens = result
        else:
            raw_cases = result

        count = len(raw_cases) if raw_cases else 0
        model_name = getattr(self.llm_service, "model_gen", "LLM")
        return {
            "raw_cases": raw_cases or [], 
            "iteration": 1,
            "tokens": tokens,
            "status": "已完成初稿生成",
            "trace": [f"📝 [生成] {model_name} 思考中... 已初步生成 {count} 条测试用例草稿。"]
        }

    async def node_validate_and_augment(self, state: GenerationState) -> Dict[str, Any]:
        logger.info(f"Step 3: Validating and converting cases")
        # 优先使用 raw_cases (由优化节点产生)
        raw_cases = state.get("raw_cases") or []
        req = state["requirement"]
        module = _get_module(req)
        
        # 1. Structural and Data Validation
        validated_raw = []
        all_violations = []
        for rc in raw_cases:
            fixed_rc = self.validator.validate_case(rc)
            
            steps = self.validator.normalize_steps(
                fixed_rc.get("steps", []),
                fixed_rc.get("title", "当前业务"),
            )
            fixed_rc["steps"] = steps
            kg_violations = self.kg_service.validate_test_case(module, steps)
            if kg_violations:
                all_violations.extend(kg_violations)
            
            logic_err = self.validator.check_logic_consistency(steps, fixed_rc.get("expected_result", ""))
            if logic_err:
                all_violations.append(f"用例 '{fixed_rc.get('title')}' 逻辑矛盾: {logic_err}")
                
            validated_raw.append(fixed_rc)
        
        # 2. Conversion to TestCase objects
        final_cases = self._convert_to_objects(req.id, validated_raw)
        
        # 3. Gap Analysis
        gap_feedback = self.optimizer.evaluate_gaps(validated_raw)
        
        total_feedback = ""
        if not validated_raw:
            total_feedback += "### 生成结果为空:\n- 模型未返回可转换的测试用例 JSON，请重新生成并至少给出 3 条结构化用例。"
        if all_violations:
            if total_feedback:
                total_feedback += "\n\n"
            total_feedback += "### 逻辑与规则校验失败:\n" + "\n".join([f"- {v}" for v in all_violations])
        if gap_feedback:
            if total_feedback: total_feedback += "\n\n"
            total_feedback += "### 覆盖率建议:\n" + gap_feedback

        status = "初步校验完成"
        trace_logs = [f"⚖️ [校验] 已对 {len(validated_raw)} 条用例进行合规性检查（如数据格式、逻辑矛盾）。"]
        
        if total_feedback:
            status = "发现生成缺陷，准备优化..."
            trace_logs.append(f"🔍 [发现] 自动评估反馈：\n{total_feedback}")
            
        return {
            "final_cases": final_cases,
            "feedback": total_feedback,
            "status": status,
            "trace": trace_logs
        }

    async def node_ai_judge(self, state: GenerationState) -> Dict[str, Any]:
        logger.info(f"Step 4: AI Judge reviewing cases for {state['requirement'].id}")
        if self._local_fast_mode():
            return {
                "status": "跳过 AI 审计 (本地快速模式)",
                "trace": ["⏭️ [审计] 当前为本地 7b 快速模式，跳过 AI 判官以提升速度与稳定性。"],
                "feedback": state["feedback"],
                "final_cases": state["final_cases"],
            }
        
        if not state["kg_constraints"]:
            return {
                "status": "跳过 AI 审计 (无图谱约束)",
                "trace": ["⏭️ [审计] 知识图谱中无相关约束，跳过判官节点。"],
                "feedback": state["feedback"], # Keep feedback
                "final_cases": state["final_cases"] # Ensure final_cases is passed to the next state
            }

        judge_fn = getattr(self.llm_service, "async_judge_cases", None)
        if not callable(judge_fn):
            return {
                "status": "跳过 AI 审计 (Judge 不可用)",
                "trace": ["⏭️ [审计] 当前 LLM 服务未提供 async_judge_cases，跳过判官节点。"],
                "feedback": state["feedback"],
                "final_cases": state["final_cases"]
            }
        
        # 兼容 test_instruction 为 dict 或对象
        def _get_ti_field(ti, field, default):
            if isinstance(ti, dict):
                return ti.get(field, default)
            return getattr(ti, field, default)

        cases_to_judge = [
            {
                "title": c.title,
                "steps": _get_ti_field(c.test_instruction, "steps", []),
                "expected": _get_ti_field(c.test_instruction, "expected_result", "")
            }
            for c in state["final_cases"]
        ]
        
        judge_result = await judge_fn(
            kg_constraints=state["kg_constraints"],
            test_cases=cases_to_judge
        )
        
        violations = judge_result.get("violations", [])
        gaps = judge_result.get("gaps", [])
        passed = judge_result.get("passed", True)
        tokens = judge_result.get("tokens", 0) if isinstance(judge_result, dict) else 0
        model_name = getattr(self.llm_service, "model_judge", "LLM")
        
        current_feedback = state.get("feedback", "")
        new_trace = []
        
        if violations or gaps:
            judge_feedback = ""
            if violations:
                judge_feedback += "### AI 判官发现违规:\n" + "\n".join([f"- {v}" for v in violations])
            if gaps:
                if judge_feedback: judge_feedback += "\n"
                judge_feedback += "### AI 判官覆盖建议:\n" + "\n".join([f"- {g}" for g in gaps])
            
            # Combine previous feedback (from validator) and new judge feedback
            combined_feedback = (current_feedback + "\n\n" + judge_feedback).strip()
            new_trace.append(f"👨‍⚖️ [判官] {model_name} 发现 {len(violations)} 处合规性问题和 {len(gaps)} 处覆盖缺失。")
            status = "审计未通过，准备优化..."
        else:
            combined_feedback = current_feedback
            new_trace.append(f"👨‍⚖️ [判官] {model_name} 审计通过，用例完全符合知识图谱业务约束。")
            status = "审计通过"
            
        return {
            "feedback": combined_feedback,
            "status": status,
            "trace": new_trace,
            "tokens": tokens,
            "final_cases": state["final_cases"] # Pass cases through
        }

    def should_optimize(self, state: GenerationState) -> str:
        if self._local_fast_mode():
            if not state.get("final_cases") and state.get("iteration", 0) < 2:
                return "continue"
            return "end"
        # 允许最多 2 次优化迭代 (Iteration 1 为初稿，Iteration 2/3 为优化)
        if state.get("feedback") and state.get("iteration", 0) < 3:
            return "continue"
        return "end"

    async def node_optimize(self, state: GenerationState) -> Dict[str, Any]:
        logger.info(f"Optimizing cases for {state['requirement'].id} based on feedback. Iteration: {state['iteration']}")
        # Convert final_cases back to dicts for LLM processing
        raw_cases = []
        source_cases = state["final_cases"] or []
        for c in source_cases:
            ti = c.get_test_instruction()
            raw_cases.append({
                "title": c.title,
                "precondition": ti.pre_condition,
                "steps": ti.steps,
                "expected_result": ti.expected_result,
                "test_data": ti.test_data_sets.model_dump() if ti.test_data_sets else {},
                "priority": c.priority,
                "type": c.dimension
            })

        if not raw_cases:
            raw_cases = state.get("raw_cases") or []
            
        refine_fn = getattr(self.llm_service, "async_refine_cases", None)
        if not callable(refine_fn):
            status = "优化不可用"
            return {
                "iteration": 3,
                "status": status,
                "trace": ["⚠️ [优化] 当前 LLM 服务未提供 async_refine_cases，跳过优化并结束迭代。"],
                "final_cases": state["final_cases"]
            }

        try:
            refined_result = refine_fn(raw_cases, state["feedback"])
            if asyncio.iscoroutine(refined_result):
                refined = await refined_result
            else:
                status = "优化不可用"
                return {
                    "iteration": 3,
                    "status": status,
                    "trace": ["⚠️ [优化] async_refine_cases 返回了非协程结果，跳过优化并结束迭代。"],
                    "final_cases": state["final_cases"]
                }
        except Exception as e:
            status = "优化失败"
            return {
                "iteration": 3,
                "status": status,
                "trace": [f"⚠️ [优化] 调用 async_refine_cases 失败，跳过优化并结束迭代。err={e}"],
                "final_cases": state["final_cases"]
            }
        
        status = "优化尝试完成"
        trace_logs = [f"🔄 [优化] 第 {state['iteration']} 次迭代：已根据反馈自动补充了缺失的路径。"]
        
        current_iteration = state["iteration"]
        if refined and isinstance(refined, list):
            # Update raw_cases for the next node (validate_and_augment)
            return {
                "raw_cases": refined, 
                "iteration": current_iteration + 1,
                "status": status,
                "trace": trace_logs,
                "feedback": "" # Clear feedback for next round
            }
        else:
            status = "优化未返回结果"
            trace_logs.append("⚠️ [优化失败] 模型未能返回修正后的有效 JSON，结束迭代。")
            return {
                "iteration": 3, # Force end
                "status": status,
                "trace": trace_logs,
                "final_cases": state["final_cases"]
            }

    def _convert_to_objects(self, req_id: str, raw_cases: List[Dict[str, Any]]) -> List[TestCase]:
        final_cases = []
        for raw in raw_cases:
            if not isinstance(raw, dict): continue
            try:
                td_raw = raw.get("test_data", {})
                steps_final = self.validator.normalize_steps(
                    raw.get("steps", []),
                    raw.get("title", "Generated Case"),
                )

                tc_title = self.validator.clean_text(raw.get("title", "Generated Case")) or "Generated Case"
                tc_status = TestCaseStatus.COMPLETE
                if "PENDING_LOGIC" in tc_title:
                    tc_status = TestCaseStatus.PENDING
                
                tc = TestCase(
                    related_req_id=req_id,
                    title=tc_title,
                    test_instruction={
                        "pre_condition": self.validator.clean_text(raw.get("precondition", "None")) or "系统已完成基础部署，测试数据准备完成。",
                        "steps": steps_final,
                        "expected_result": self.validator.clean_text(raw.get("expected_result", "Success")) or "系统按照需求规则处理，并返回明确结果。",
                        "test_data_sets": {
                            "valid": td_raw.get("valid", {}) if isinstance(td_raw, dict) else {},
                            "invalid": td_raw.get("invalid", {}) if isinstance(td_raw, dict) else {}
                        }
                    },
                    methodology=raw.get("methodology", ["LLM"]),
                    dimension=raw.get("type", "Functional"),
                    priority=raw.get("priority", "P2"),
                    status=tc_status
                )
                final_cases.append(tc)
            except Exception as e:
                logger.warning(f"Error converting case: {e}")
        return final_cases

    async def run_with_updates(self, req: Requirement):
        initial_state = {
            "requirement": req,
            "iteration": 0,
            "raw_cases": [],
            "final_cases": [],
            "feedback": "",
            "status": "启动工作流...",
            "trace": ["🚀 正在初始化 Agent 工作空间..."],
            "kg_hit": False
        }
        
        async for event in self.app.astream(initial_state):
            for node_name, updates in event.items():
                # Extract final cases from state if available, to ensure we catch them even on the last node
                final_cases = updates.get("final_cases")
                
                yield {
                    "node": node_name,
                    "status": updates.get("status", ""),
                    "trace": updates.get("trace", []),
                    "final_cases": final_cases,
                    "kg_hit": updates.get("kg_hit", False),
                    "tokens": updates.get("tokens", 0),
                    "iteration": updates.get("iteration", 0)
                }

    async def run_refine_with_updates(self, tc_list: List[TestCase], feedback: str):
        yield {
            "node": "manual_refine_init",
            "status": "接收修正意见",
            "trace": [f"📝 收到用户修正意见: {feedback}", f"🧪 待优化用例数: {len(tc_list)}"]
        }
        
        raw_cases = [tc.model_dump(mode="json") for tc in tc_list]
        
        yield {
            "node": "llm_refining",
            "status": "AI 正在重构用例",
            "trace": ["🤖 LLM 正在根据反馈重新平衡测试路径..."]
        }
        
        refined_raw = await self.llm_service.async_refine_cases(raw_cases, feedback)
        
        yield {
            "node": "validation",
            "status": "校验修正结果",
            "trace": ["⚖️ 正在对修正后的用例进行二次合规性检查..."]
        }
        
        req_id = tc_list[0].related_req_id if tc_list else "Unknown"
        # 使用更鲁棒的 _convert_to_objects 进行转换，而不是直接 model_validate
        final_results = self._convert_to_objects(req_id, refined_raw)
        
        yield {
            "node": "complete",
            "status": "修正完成",
            "trace": [f"✅ 成功重构 {len(final_results)} 条用例。"],
            "final_cases": final_results
        }
