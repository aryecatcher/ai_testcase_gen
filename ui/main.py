import streamlit as st
import pandas as pd
import os
import sys
from pathlib import Path
import json

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from src.models.domain import ProjectContext, Requirement, TestCase
from src.core.ingestion.ingestor import RequirementIngestor
from src.core.ai.llm_service import LLMService
from src.core.kg.graph_service import KnowledgeGraphService
from src.core.generation.generator import TestCaseGenerator
from src.core.feedback.manager import FeedbackManager
from src.core.output.exporter import TestCaseExporter
from src.core.output.postman_exporter import PostmanExporter
from src.core.output.feishu_client import FeishuClient
from data.storage import save_json, load_json

# --- Page Config & Styling ---
st.set_page_config(
    page_title="AI 测试用例生成器 V2",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for Professional UI
st.markdown("""
<style>
    .main .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
    h1, h2, h3 {
        font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif;
        font-weight: 600;
        color: #0E1117;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 24px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        white-space: pre-wrap;
        background-color: transparent;
        border-radius: 4px 4px 0px 0px;
        gap: 1px;
        padding-top: 10px;
        padding-bottom: 10px;
        color: #555;
    }
    .stTabs [aria-selected="true"] {
        background-color: transparent;
        color: #0F52BA;
        border-bottom: 2px solid #0F52BA;
    }
    .stCard {
        background-color: #F0F2F6;
        padding: 1.5rem;
        border-radius: 0.5rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.12);
    }
    .req-box {
        background-color: #f8f9fa;
        border-left: 5px solid #0F52BA;
        padding: 15px;
        border-radius: 5px;
        margin-bottom: 10px;
        color: #333333; /* Ensure text is dark enough against light background */
    }
    .case-box {
        background-color: #ffffff;
        border: 1px solid #e0e0e0;
        padding: 15px;
        border-radius: 5px;
        margin-bottom: 10px;
    }
    .metric-card {
        background-color: white;
        padding: 15px;
        border-radius: 8px;
        text-align: center;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
</style>
""", unsafe_allow_html=True)

# --- Session State Init ---
if "context" not in st.session_state:
    st.session_state.context = ProjectContext()

if "services" not in st.session_state:
    st.session_state.services = {}

# --- Sidebar ---
with st.sidebar:
    st.title("系统配置")
    st.caption("v2.0.0 企业版")
    
    with st.expander("LLM 设置", expanded=True):
        api_key = st.text_input("API Key", type="password", value=os.getenv("OPENAI_API_KEY", "ollama"), help="本地模型默认使用 'ollama'")
        base_url = st.text_input("Base URL", value=os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1"))
        model = st.selectbox("模型选择", ["deepseek-r1:7b (Local)", "deepseek-r1:14b (Local)", "deepseek-chat", "gpt-4", "gpt-3.5-turbo"], index=0)
        
        # Auto-fill for Local DeepSeek
        if "(Local)" in model and "api.openai.com" in base_url:
            st.info("检测到本地模型。推荐 Base URL: `http://localhost:11434/v1` (Ollama)")
        
        # vLLM Hint
        if "vllm" in base_url.lower() or "8000" in base_url:
            st.caption("🚀 vLLM 加速模式已就绪 (兼容 OpenAI API)")

        # Clean model name for API (remove ' (Local)')
        api_model_name = model.replace(" (Local)", "").strip()

        if st.button("测试连接", use_container_width=True):
            with st.spinner(f"正在连接 {api_model_name}..."):
                try:
                    test_service = LLMService(api_key=api_key, base_url=base_url, model=api_model_name)
                    result = test_service.check_connection()
                    if result["status"] == "success":
                        st.success(result["message"])
                    else:
                        st.error(f"连接失败: {result['message']}")
                except Exception as e:
                    st.error(f"错误: {e}")
    
    if st.button("初始化服务", use_container_width=True, type="primary"):
        with st.spinner("正在连接核心服务..."):
            try:
                llm_service = LLMService(api_key=api_key, base_url=base_url, model=api_model_name)
                kg_service = KnowledgeGraphService()
                st.session_state.services["ingestor"] = RequirementIngestor()
                st.session_state.services["generator"] = TestCaseGenerator(llm_service, kg_service)
                st.session_state.services["feedback"] = FeedbackManager(llm_service)
                st.success("服务已上线")
            except Exception as e:
                st.error(f"初始化失败: {e}")

    st.divider()
    st.markdown("### 项目状态")
    req_count = len(st.session_state.context.requirements) if st.session_state.context.requirements else 0
    case_count = len(st.session_state.context.test_cases) if st.session_state.context.test_cases else 0
    st.metric("需求数量", req_count)
    st.metric("测试用例", case_count)

# --- Main Content ---
st.title("AI 测试用例生成器")
st.markdown("自动化需求分析与测试生成系统")

tab1, tab2, tab3 = st.tabs(["1. 需求导入", "2. AI 生成", "3. 评审与追溯"])

# --- Tab 1: Ingestion ---
with tab1:
    col1, col2 = st.columns([2, 1])
    with col1:
        st.subheader("文档上传")
        uploaded_file = st.file_uploader("拖拽需求文档到此处", type=["docx", "xlsx", "txt", "json", "md"], help="支持格式: DOCX, XLSX, TXT, JSON, MD (上限 200MB)")
    
    with col2:
        st.subheader("操作")
        if uploaded_file and "ingestor" in st.session_state.services:
            if st.button("处理文档", type="primary", use_container_width=True):
                # Save temp file
                temp_path = Path("temp_upload")
                temp_path.mkdir(exist_ok=True)
                file_path = temp_path / uploaded_file.name
                with open(file_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                
                with st.spinner("正在使用 NLP 引擎解析需求..."):
                    reqs = st.session_state.services["ingestor"].ingest(str(file_path))
                    st.session_state.context.requirements = reqs
                    st.success(f"成功解析 {len(reqs)} 条需求！")
        elif not "ingestor" in st.session_state.services:
            st.info("请先在左侧边栏初始化服务。")

    # Dashboard
    if st.session_state.context.requirements:
        st.divider()
        st.subheader("需求分析看板")
        
        # Metrics
        total = len(st.session_state.context.requirements)
        high_conf = sum(1 for r in st.session_state.context.requirements if r.ingestion_metadata.parsing_confidence >= 0.8)
        low_conf = total - high_conf
        
        m1, m2, m3 = st.columns(3)
        m1.metric("需求总数", total)
        m2.metric("高质量需求 (Ready)", high_conf, delta_color="normal")
        m3.metric("需人工复核 (Blocked)", low_conf, delta_color="inverse")

        # Data Table
        req_data = []
        for r in st.session_state.context.requirements:
            req_data.append({
                "ID": r.id,
                "内容预览": r.original_text[:80] + "...",
                "模块": r.extracted_entities.module or "N/A",
                "功能": r.extracted_entities.feature or "N/A",
                "置信度": r.ingestion_metadata.parsing_confidence,
                "状态": "已就绪" if r.ingestion_metadata.parsing_confidence >= 0.8 else "需复核"
            })
        
        df = pd.DataFrame(req_data)
        st.dataframe(
            df,
            column_config={
                "置信度": st.column_config.ProgressColumn(
                    "AI 置信度",
                    help="AI 解析的置信度评分",
                    format="%.2f",
                    min_value=0,
                    max_value=1,
                ),
            },
            use_container_width=True,
            hide_index=True
        )

        if low_conf > 0:
            st.warning(f"{low_conf} 条需求需要人工完善。请查看“评审与追溯”标签页或编辑源文档。")

# --- Tab 2: Generation ---
with tab2:
    st.subheader("AI 测试用例生成引擎")
    
    if st.session_state.context.requirements:
        ready_reqs = [r for r in st.session_state.context.requirements if r.ingestion_metadata.parsing_confidence >= 0.8]
        
        col_info, col_action = st.columns([3, 1])
        with col_info:
            st.info(f"准备为 **{len(ready_reqs)}** 条已验证需求生成用例。")
            st.markdown("""
            **生成策略：**
            - **功能测试**: 边界值分析、等价类划分
            - **安全测试**: SQL 注入、XSS、越权访问 (如适用)
            - **性能测试**: 响应时间约束检查
            """)
        
        with col_action:
            if st.button("开始生成", type="primary", use_container_width=True, disabled=len(ready_reqs)==0):
                if "generator" in st.session_state.services:
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    with st.spinner("AI 正在生成测试用例..."):
                        # We can improve generator to yield progress, but for now just call it
                        status_text.text("分析语义图谱...")
                        progress_bar.progress(20)
                        
                        cases = st.session_state.services["generator"].generate(ready_reqs)
                        
                        status_text.text("合成测试数据...")
                        progress_bar.progress(60)
                        
                        st.session_state.context.test_cases = cases
                        progress_bar.progress(100)
                        status_text.text("完成！")
                        
                    st.success(f"成功生成 {len(cases)} 条测试用例！")
                else:
                    st.error("服务未初始化。")
    else:
        st.warning("请先导入需求文档。")

# --- Tab 3: Review (Traceability) ---
with tab3:
    st.subheader("评审与追溯")
    
    if st.session_state.context.test_cases and st.session_state.context.requirements:
        
        # 1. Selector
        req_options = {r.id: f"[{r.id}] {r.original_text[:50]}..." for r in st.session_state.context.requirements}
        selected_req_id = st.selectbox("选择需求进行评审:", options=list(req_options.keys()), format_func=lambda x: req_options[x])
        
        # 2. Split View
        col_req, col_cases = st.columns([1, 1], gap="medium")
        
        # Left: Requirement
        selected_req = next((r for r in st.session_state.context.requirements if r.id == selected_req_id), None)
        with col_req:
            st.markdown("### 需求详情")
            if selected_req:
                st.markdown(f"""
                <div class="req-box">
                    <strong>ID:</strong> {selected_req.id}<br>
                    <strong>模块:</strong> {selected_req.extracted_entities.module}<br>
                    <strong>置信度:</strong> {selected_req.ingestion_metadata.parsing_confidence:.2f}
                    <hr>
                    <strong>原始内容:</strong><br>
                    {selected_req.original_text}
                </div>
                """, unsafe_allow_html=True)
                
                with st.expander("提取的元数据", expanded=False):
                    st.json(selected_req.extracted_entities.model_dump())

        # Right: Test Cases
        linked_cases = [tc for tc in st.session_state.context.test_cases if tc.related_req_id == selected_req_id]
        with col_cases:
            st.markdown(f"### 关联用例 ({len(linked_cases)})")
            
            if not linked_cases:
                st.info("该需求暂无生成的测试用例。")
            
            for i, tc in enumerate(linked_cases):
                with st.expander(f"{tc.priority} | {tc.title}", expanded=True):
                    # Editable Fields
                    new_title = st.text_input("标题", value=tc.title, key=f"title_{tc.test_case_id}")
                    new_steps = st.text_area("步骤", value="\n".join(tc.test_instruction.steps), height=100, key=f"steps_{tc.test_case_id}")
                    new_expected = st.text_area("预期结果", value=tc.test_instruction.expected_result, height=70, key=f"exp_{tc.test_case_id}")
                    
                    # Update Button (In-memory update)
                    if st.button("更新用例", key=f"btn_{tc.test_case_id}"):
                        tc.title = new_title
                        tc.test_instruction.steps = new_steps.split("\n")
                        tc.test_instruction.expected_result = new_expected
                        st.toast(f"已更新用例 {tc.test_case_id}")

        st.divider()
        
        # 3. Bulk Actions & Export
        st.subheader("导出与集成")
        
        col_e1, col_e2, col_e3, col_e4 = st.columns(4)
        
        exporter = TestCaseExporter(st.session_state.context.test_cases)
        
        with col_e1:
            st.download_button(
                "下载 Excel (标准版)",
                data=exporter.to_excel(),
                file_name="test_cases.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        
        with col_e2:
            st.download_button(
                "下载 Excel (飞书版)",
                data=exporter.to_feishu_excel(),
                file_name="feishu_cases.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
            
        with col_e3:
            pm = PostmanExporter(st.session_state.context.test_cases)
            st.download_button(
                "下载 Postman 集合",
                data=pm.to_collection(),
                file_name="postman_collection.json",
                mime="application/json",
                use_container_width=True
            )
            
        with col_e4:
            if st.button("推送到飞书", use_container_width=True):
                client = FeishuClient()
                records = {"records": [{"fields": {"title": tc.title, "expected": tc.test_instruction.expected_result}} for tc in st.session_state.context.test_cases]}
                ok = client.push_records(records)
                if ok:
                    st.success("推送成功！")
                else:
                    st.warning("推送失败，请检查配置。")

    else:
        st.info("请先生成测试用例以进行评审。")
