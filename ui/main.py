import os
import sys
from pathlib import Path

import streamlit as st

_UI_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _UI_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from data.storage import load_json, save_json
from src.models.domain import ProjectContext

_PROJECT_CONTEXT_PATH = _PROJECT_ROOT / "data" / "project_context.json"

# --- Inline SVG icons (flat stroke, no emoji) ---
_S = 'xmlns="http://www.w3.org/2000/svg"'
_VB = 'width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round'


def _svg(name: str) -> str:
    """Return full <svg> element for sidebar / headings."""
    inner = {
        "book": '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
        "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
        "layers": '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
        "clipboard": '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/><path d="M9 12h6M9 16h6"/>',
        "sliders": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
        "trash": '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
        "layout": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/>',
        "package": '<path d="M16.5 9.4 7.55 4.24"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
        "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
        "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
        "info": '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
        "list-check": '<path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><path d="M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v0a2 2 0 0 1-2 2H11a2 2 0 0 1-2-2z"/><path d="m9 12 2 2 4-4"/>',
    }[name]
    return f'<svg {_S} {_VB}">{inner}</svg>'


def _heading_html(label: str, icon: str) -> str:
    return (
        f'<div class="app-section-head">'
        f'<span class="app-section-icon">{_svg(icon)}</span>'
        f'<span class="app-section-label">{label}</span>'
        f"</div>"
    )


def _render_heading(label: str, icon: str) -> None:
    st.markdown(_heading_html(label, icon), unsafe_allow_html=True)


APP_CSS = """
<style>
    .main .block-container { padding-top: 1.25rem; padding-bottom: 2rem; }
    h1 { font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-weight: 600; color: #111827; font-size: 1.75rem; margin-bottom: 0.25rem; }
    .app-subtitle { color: #4b5563; font-size: 0.95rem; margin-bottom: 1.25rem; }
    h2, h3 { font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-weight: 600; color: #111827; }
    .app-section-head { display: flex; align-items: center; gap: 0.5rem; margin: 0.5rem 0 0.75rem 0; }
    .app-section-icon { display: inline-flex; color: #1e40af; align-items: center; justify-content: center; }
    .app-section-icon svg { display: block; }
    .app-section-label { font-size: 1.05rem; font-weight: 600; color: #111827; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        height: 44px; background: #f3f4f6; border-radius: 6px 6px 0 0;
        color: #374151; border: 1px solid #e5e7eb; border-bottom: none;
    }
    .stTabs [aria-selected="true"] {
        background: #ffffff; color: #1e3a8a;
        border-color: #e5e7eb; border-bottom: 2px solid #1e40af;
    }
    .tutorial-step { display: flex; gap: 0.75rem; align-items: flex-start; margin: 0.6rem 0; padding: 0.5rem 0; border-bottom: 1px solid #e5e7eb; }
    .tutorial-step:last-child { border-bottom: none; }
    .tutorial-num {
        flex-shrink: 0; width: 1.75rem; height: 1.75rem; border-radius: 4px;
        background: #1e40af; color: #fff; font-size: 0.85rem; font-weight: 600;
        display: flex; align-items: center; justify-content: center;
    }
    .tutorial-body { color: #374151; font-size: 0.92rem; line-height: 1.5; }
    .tutorial-note { background: #f9fafb; border-left: 3px solid #1e40af; padding: 0.75rem 1rem; margin-top: 1rem; color: #4b5563; font-size: 0.88rem; }
    .stButton > button[kind="primary"] {
        background-color: #1e40af !important; color: #ffffff !important; border: 1px solid #1e3a8a !important;
    }
    .stButton > button[kind="primary"]:hover { background-color: #1d4ed8 !important; }
    [data-testid="stSidebar"] { background-color: #f9fafb; border-right: 1px solid #e5e7eb; }
</style>
"""


def get_test_case_exporter():
    from src.core.output.exporter import TestCaseExporter

    return TestCaseExporter


def get_postman_exporter():
    from src.core.output.postman_exporter import PostmanExporter

    return PostmanExporter


def get_feishu_client():
    from src.core.output.feishu_client import FeishuClient

    return FeishuClient()


def get_testlink_importer():
    from src.core.integration.testlink_service import TestLinkImporter

    return TestLinkImporter


@st.cache_resource
def init_services(api_key, base_url, model):
    # 延迟导入：避免打开页面时加载 Docling / NetworkX / OpenAI 等大依赖
    from src.core.ai.llm_service import LLMService
    from src.core.feedback.manager import FeedbackManager
    from src.core.generation.generator import TestCaseGenerator
    from src.core.ingestion.ingestor import RequirementIngestor
    from src.core.kg.graph_service import KnowledgeGraphService

    llm_service = LLMService(api_key=api_key, base_url=base_url, model=model)
    kg_service = KnowledgeGraphService()
    return {
        "ingestor": RequirementIngestor(),
        "generator": TestCaseGenerator(llm_service, kg_service),
        "feedback": FeedbackManager(llm_service),
    }


def save_context() -> None:
    st.session_state.req_count = len(st.session_state.context.requirements)
    st.session_state.case_count = len(st.session_state.context.test_cases)
    st.session_state.case_map = {}
    for tc in st.session_state.context.test_cases:
        st.session_state.case_map.setdefault(tc.related_req_id, []).append(tc)
    save_json("project_context", st.session_state.context.model_dump(mode="json"))


def _init_session() -> None:
    if "context" not in st.session_state:
        # 用文件体积预判，避免对大 JSON 先 parse 再 str 导致卡顿
        if (
            _PROJECT_CONTEXT_PATH.is_file()
            and _PROJECT_CONTEXT_PATH.stat().st_size < 2_000_000
        ):
            saved_data = load_json("project_context")
            if saved_data:
                try:
                    st.session_state.context = ProjectContext.model_validate(saved_data)
                except Exception as e:
                    st.error(f"无法恢复历史项目数据: {e}")
                    st.session_state.context = ProjectContext()
            else:
                st.session_state.context = ProjectContext()
        elif _PROJECT_CONTEXT_PATH.is_file() and _PROJECT_CONTEXT_PATH.stat().st_size >= 2_000_000:
            st.session_state.context = ProjectContext()
            st.warning("项目快照超过 2MB，已跳过自动加载。请手动清理 data/project_context.json 或拆分项目。")
        else:
            st.session_state.context = ProjectContext()

    if "req_count" not in st.session_state:
        st.session_state.req_count = len(st.session_state.context.requirements)
    if "case_count" not in st.session_state:
        st.session_state.case_count = len(st.session_state.context.test_cases)
    if "case_map" not in st.session_state:
        st.session_state.case_map = {}
        for tc in st.session_state.context.test_cases:
            st.session_state.case_map.setdefault(tc.related_req_id, []).append(tc)

    if "services" not in st.session_state:
        st.session_state.services = {}


def render_sidebar() -> None:
    st.markdown(_heading_html("连接与环境", "sliders"), unsafe_allow_html=True)
    st.caption("V2")

    with st.expander("LLM 参数", expanded=True):
        api_key = st.text_input(
            "API Key",
            type="password",
            value=os.getenv("OPENAI_API_KEY", "ollama"),
            help="本地 Ollama 等可填 ollama",
        )
        base_url = st.text_input(
            "Base URL",
            value=os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1"),
        )
        model = st.selectbox(
            "模型",
            [
                "deepseek-r1:7b (Local)",
                "deepseek-r1:14b (Local)",
                "deepseek-chat",
                "gpt-4",
                "gpt-3.5-turbo",
            ],
            index=0,
        )
        if "(Local)" in model and "api.openai.com" in base_url:
            st.info("本地模型建议 Base URL: `http://localhost:11434/v1`")
        if "vllm" in base_url.lower() or "8000" in base_url:
            st.caption("检测到可能为 vLLM / 自定义 OpenAI 兼容服务。")

        api_model_name = model.replace(" (Local)", "").strip()

        if st.button("测试连接", use_container_width=True):
            from src.core.ai.llm_service import LLMService

            with st.spinner(f"正在连接 {api_model_name}…"):
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
        with st.spinner("正在初始化…"):
            try:
                st.session_state.services = init_services(api_key, base_url, api_model_name)
                st.success("核心服务已就绪")
            except Exception as e:
                st.error(f"初始化失败: {e}")

    st.divider()
    _render_heading("项目概览", "layout")
    st.metric("需求条数", st.session_state.req_count)
    st.metric("测试用例", st.session_state.case_count)

    if st.button("清空项目数据", use_container_width=True, help="清空需求与用例并写入空快照"):
        st.session_state.context = ProjectContext()
        st.session_state.req_count = 0
        st.session_state.case_count = 0
        save_json("project_context", {})
        st.toast("已清空")
        st.rerun()

def render_tab_guide() -> None:
    _render_heading("使用说明", "book")
    st.markdown(
        """
        <div class="tutorial-step"><div class="tutorial-num">1</div><div class="tutorial-body">
        在左侧边栏配置 <strong>API Key</strong>、<strong>Base URL</strong> 与模型，先「测试连接」再点 <strong>初始化服务</strong>。未完成初始化时无法解析文档或生成用例。
        </div></div>
        <div class="tutorial-step"><div class="tutorial-num">2</div><div class="tutorial-body">
        打开 <strong>导入需求</strong>：上传 DOCX / XLSX / TXT / JSON / MD，执行「解析文档」。表格类内容通常置信度更高；长文档会按片段拆分。
        </div></div>
        <div class="tutorial-step"><div class="tutorial-num">3</div><div class="tutorial-body">
        打开 <strong>评审与导出</strong>：对置信度低于 0.8 的条目补全模块、功能与正文，保存或「标记为就绪」。只有就绪需求会进入批量生成（可在单条上强制生成）。
        </div></div>
        <div class="tutorial-step"><div class="tutorial-num">4</div><div class="tutorial-body">
        在 <strong>生成用例</strong> 中对就绪需求批量生成。生成会替换<strong>本次涉及需求</strong>的旧用例，其他需求的用例保留。
        </div></div>
        <div class="tutorial-step"><div class="tutorial-num">5</div><div class="tutorial-body">
        回到 <strong>评审与导出</strong> 校对用例，下载 Excel / 飞书版 Excel / Postman，或配置飞书、TestLink 推送。
        </div></div>
        <div class="tutorial-note">
        <strong>提示：</strong>侧边栏指标与本地 <code>data/project_context.json</code> 同步；LLM 缓存在 <code>data/llm_cache.json</code>。若结果异常，可先清空项目数据后重试。
        </div>
        """,
        unsafe_allow_html=True,
    )

    ready = sum(
        1 for r in st.session_state.context.requirements if r.ingestion_metadata.parsing_confidence >= 0.8
    )
    c1, c2, c3 = st.columns(3)
    c1.metric("已导入需求", len(st.session_state.context.requirements))
    c2.metric("就绪可生成", ready)
    c3.metric("已生成用例", len(st.session_state.context.test_cases))

    if not st.session_state.services.get("ingestor"):
        st.warning("尚未初始化服务，请从左侧边栏完成初始化。")
    elif not st.session_state.context.requirements:
        st.info("下一步：在「导入需求」中上传并解析文档。")
    elif ready == 0:
        st.info("下一步：在「评审与导出」中完善低置信度需求并标记就绪。")
    elif not st.session_state.context.test_cases:
        st.info("下一步：在「生成用例」中执行批量生成。")
    else:
        st.success("可进行用例校对与导出。")


def render_tab_import() -> None:
    import pandas as pd

    _render_heading("导入需求", "upload")
    col_a, col_b = st.columns([2, 1])
    with col_a:
        uploaded_files = st.file_uploader(
            "选择文件",
            type=["docx", "xlsx", "txt", "json", "md"],
            help="支持 DOCX、XLSX、TXT、JSON、MD",
            accept_multiple_files=True,
        )
    with col_b:
        st.markdown("<br>", unsafe_allow_html=True)
        can_run = bool(uploaded_files and st.session_state.services.get("ingestor"))
        if st.button("解析文档", type="primary", use_container_width=True, disabled=not can_run):
            temp_path = _PROJECT_ROOT / "temp_upload"
            temp_path.mkdir(exist_ok=True)
            all_reqs = []
            ingest_errors = []
            progress_text = st.empty()
            for i, uploaded_file in enumerate(uploaded_files):
                file_path = temp_path / uploaded_file.name
                with open(file_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                progress_text.text(f"解析中 ({i+1}/{len(uploaded_files)}): {uploaded_file.name}")
                try:
                    reqs = st.session_state.services["ingestor"].ingest(str(file_path))
                    all_reqs.extend(reqs)
                except Exception as e:
                    ingest_errors.append((uploaded_file.name, str(e)))
                    st.error(f"{uploaded_file.name}: {e}")
            if all_reqs:
                st.session_state.context.requirements = all_reqs
                st.session_state.req_count = len(all_reqs)
                save_context()
                if ingest_errors:
                    st.warning(
                        f"共 {len(uploaded_files)} 个文件：得到 {len(all_reqs)} 条需求；{len(ingest_errors)} 个文件失败。"
                    )
                else:
                    st.success(f"已解析 {len(uploaded_files)} 个文件，共 {len(all_reqs)} 条需求。")
            elif ingest_errors:
                st.error("全部文件解析失败，需求列表未更改。")
            progress_text.empty()
        elif not st.session_state.services.get("ingestor"):
            st.info("请先在侧栏初始化服务。")

    if not st.session_state.context.requirements:
        return

    st.divider()
    _render_heading("需求列表", "clipboard")
    total = len(st.session_state.context.requirements)
    high_conf = sum(1 for r in st.session_state.context.requirements if r.ingestion_metadata.parsing_confidence >= 0.8)
    low_conf = total - high_conf
    m1, m2, m3 = st.columns(3)
    m1.metric("总数", total)
    m2.metric("就绪 (≥0.8)", high_conf)
    m3.metric("待复核", low_conf)

    req_data = []
    for r in st.session_state.context.requirements:
        req_data.append(
            {
                "ID": r.id,
                "内容预览": (r.original_text[:80] + "…") if len(r.original_text) > 80 else r.original_text,
                "模块": r.extracted_entities.module or "—",
                "功能": r.extracted_entities.feature or "—",
                "置信度": r.ingestion_metadata.parsing_confidence,
                "状态": "就绪" if r.ingestion_metadata.parsing_confidence >= 0.8 else "待复核",
            }
        )
    st.dataframe(
        pd.DataFrame(req_data),
        column_config={
            "置信度": st.column_config.ProgressColumn(
                "置信度", format="%.2f", min_value=0, max_value=1
            ),
        },
        use_container_width=True,
        hide_index=True,
    )
    if low_conf > 0:
        st.warning(f"{low_conf} 条需求待复核，请到「评审与导出」处理。")


def render_tab_generate() -> None:
    _render_heading("生成用例", "layers")
    if not st.session_state.context.requirements:
        st.warning("请先完成「导入需求」。")
        return

    ready_reqs = [
        r for r in st.session_state.context.requirements if r.ingestion_metadata.parsing_confidence >= 0.8
    ]
    st.markdown(
        f"就绪需求 **{len(ready_reqs)}** 条（置信度 ≥ 0.8）。批量生成采用功能 / 安全 / 性能等策略，并结合知识图谱约束。"
    )
    col_info, col_go = st.columns([4, 1])
    with col_go:
        start = st.button("开始生成", type="primary", use_container_width=True, disabled=len(ready_reqs) == 0)
    with col_info:
        if len(ready_reqs) == 0:
            st.info("无就绪需求。请在「评审与导出」中编辑并标记就绪。")

    if not start:
        return
    if "generator" not in st.session_state.services:
        st.error("服务未初始化。")
        return

    progress_bar = st.progress(0)
    status_text = st.empty()

    def update_progress(current: int, total: int) -> None:
        if total <= 0:
            progress_bar.progress(100)
            status_text.text("无可生成需求。")
            return
        pct = min(100, int((current / total) * 100))
        progress_bar.progress(pct)
        status_text.text(f"进度 {current}/{total}（{pct}%）")

    with st.spinner("生成中…"):
        status_text.text("准备调用模型与图谱…")
        try:
            cases = st.session_state.services["generator"].generate(
                ready_reqs, progress_callback=update_progress
            )
        except Exception as gen_err:
            progress_bar.progress(0)
            status_text.text("已中断。")
            st.error(f"生成失败: {gen_err}")
            return

    status_text.text("合并与去重…")
    progress_bar.progress(100)
    ready_ids = {r.id for r in ready_reqs}
    kept = [tc for tc in st.session_state.context.test_cases if tc.related_req_id not in ready_ids]
    st.session_state.context.test_cases = kept + cases
    st.session_state.case_count = len(st.session_state.context.test_cases)
    save_context()
    status_text.text(f"本次新增 {len(cases)} 条；保留其他需求用例 {len(kept)} 条。")
    st.success(
        f"完成：本次 {len(ready_reqs)} 条需求 → {len(cases)} 条用例；项目共 {len(st.session_state.context.test_cases)} 条。"
    )


def render_tab_review_export() -> None:
    _render_heading("评审与导出", "package")
    if not st.session_state.context.requirements:
        st.info("请先在「导入需求」中解析文档。")
        return

    col_filter, _ = st.columns([2, 1])
    with col_filter:
        only_low = st.checkbox("仅显示待复核（置信度 < 0.8）", value=True)

    if only_low:
        filtered_reqs = [
            r for r in st.session_state.context.requirements if r.ingestion_metadata.parsing_confidence < 0.8
        ]
    else:
        filtered_reqs = list(st.session_state.context.requirements)

    if only_low and not filtered_reqs:
        st.success("当前没有待复核需求，所有条目置信度均 ≥ 0.8。")
        st.caption("取消勾选上方选项可浏览全部需求。")
    elif not filtered_reqs:
        st.caption("无需求可显示。")
    else:
        req_options = {r.id: f"[{r.id}] {(r.original_text[:50] + '…') if len(r.original_text) > 50 else r.original_text}" for r in filtered_reqs}
        selected_req_id = st.selectbox(
            "选择需求",
            options=list(req_options.keys()),
            format_func=lambda x: req_options[x],
        )
        col_req, col_cases = st.columns(2, gap="medium")
        selected_req = next((r for r in st.session_state.context.requirements if r.id == selected_req_id), None)

        with col_req:
            _render_heading("需求编辑", "clipboard")
            if selected_req:
                with st.container(border=True):
                    st.caption(f"ID: {selected_req.id} | 置信度: {selected_req.ingestion_metadata.parsing_confidence:.2f}")
                    new_module = st.text_input(
                        "模块",
                        value=selected_req.extracted_entities.module or "",
                        key=f"mod_{selected_req.id}",
                    )
                    new_feature = st.text_input(
                        "功能",
                        value=selected_req.extracted_entities.feature or "",
                        key=f"feat_{selected_req.id}",
                    )
                    new_text = st.text_area(
                        "需求原文",
                        value=selected_req.original_text,
                        height=200,
                        key=f"txt_{selected_req.id}",
                    )
                    b1, b2 = st.columns(2)
                    if b1.button("保存修改", key=f"save_{selected_req.id}", use_container_width=True):
                        selected_req.extracted_entities.module = new_module
                        selected_req.extracted_entities.feature = new_feature
                        selected_req.original_text = new_text
                        save_context()
                        st.toast("已保存")
                        st.rerun()
                    if b2.button("保存并标记就绪", key=f"approve_{selected_req.id}", type="primary", use_container_width=True):
                        selected_req.extracted_entities.module = new_module
                        selected_req.extracted_entities.feature = new_feature
                        selected_req.original_text = new_text
                        selected_req.ingestion_metadata.parsing_confidence = 1.0
                        save_context()
                        st.toast("已标记就绪")
                        st.rerun()

        with col_cases:
            _render_heading("关联用例", "list-check")
            if not st.session_state.context.test_cases:
                st.warning("尚无测试用例，请使用「生成用例」批量生成。")
            else:
                linked = st.session_state.case_map.get(selected_req_id, [])
                if not linked:
                    st.info("该需求下暂无自动用例。")
                    if st.button("仅为此需求生成", key=f"gen_single_{selected_req_id}", use_container_width=True):
                        if "generator" not in st.session_state.services:
                            st.error("服务未初始化。")
                        else:
                            with st.spinner("生成中…"):
                                try:
                                    new_cases = st.session_state.services["generator"].generate([selected_req])
                                except Exception as gen_err:
                                    st.error(f"失败: {gen_err}")
                                else:
                                    if new_cases:
                                        rid = selected_req.id
                                        st.session_state.context.test_cases = [
                                            tc for tc in st.session_state.context.test_cases if tc.related_req_id != rid
                                        ]
                                        st.session_state.context.test_cases.extend(new_cases)
                                        st.session_state.case_count = len(st.session_state.context.test_cases)
                                        save_context()
                                        st.success(f"已生成 {len(new_cases)} 条。")
                                        st.rerun()
                                    else:
                                        st.warning("未返回用例，请检查模型或需求内容。")
                else:
                    st.caption(f"共 {len(linked)} 条")
                    for tc in linked:
                        with st.expander(f"{tc.priority} | {tc.title}", expanded=False):
                            if tc.methodology:
                                st.caption("策略: " + ", ".join(tc.methodology))
                            new_title = st.text_input("标题", value=tc.title, key=f"title_{tc.test_case_id}")
                            new_steps = st.text_area(
                                "步骤（每行一条）",
                                value="\n".join(tc.test_instruction.steps),
                                height=100,
                                key=f"steps_{tc.test_case_id}",
                            )
                            new_expected = st.text_area(
                                "预期",
                                value=tc.test_instruction.expected_result,
                                height=70,
                                key=f"exp_{tc.test_case_id}",
                            )
                            if st.button("保存用例", key=f"btn_{tc.test_case_id}", use_container_width=True):
                                tc.title = new_title
                                tc.test_instruction.steps = new_steps.split("\n")
                                tc.test_instruction.expected_result = new_expected
                                save_context()
                                st.toast(f"已更新 {tc.test_case_id}")

    st.divider()
    _render_heading("导出与集成", "download")
    st.caption("需已存在测试用例。")
    e1, e2, e3, e4 = st.columns(4)
    cases = st.session_state.context.test_cases
    with e1:
        if cases:
            Exporter = get_test_case_exporter()
            exp = Exporter(cases)
            st.download_button(
                "Excel（标准）",
                data=exp.to_excel(),
                file_name="test_cases.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        else:
            st.button("Excel（标准）", disabled=True, use_container_width=True)
    with e2:
        if cases:
            Exporter = get_test_case_exporter()
            exp = Exporter(cases)
            st.download_button(
                "Excel（飞书列）",
                data=exp.to_feishu_excel(),
                file_name="feishu_cases.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        else:
            st.button("Excel（飞书列）", disabled=True, use_container_width=True)
    with e3:
        if cases:
            PM = get_postman_exporter()
            pm = PM(cases)
            st.download_button(
                "Postman 集合",
                data=pm.to_collection(),
                file_name="postman_collection.json",
                mime="application/json",
                use_container_width=True,
            )
        else:
            st.button("Postman 集合", disabled=True, use_container_width=True)
    with e4:
        if st.button("推送到飞书", use_container_width=True, disabled=not cases):
            client = get_feishu_client()
            records = {
                "records": [
                    {"fields": {"title": tc.title, "expected": tc.test_instruction.expected_result}}
                    for tc in cases
                ]
            }
            if client.push_records(records):
                st.success("已提交推送请求。")
            else:
                st.warning("推送失败，请检查飞书配置。")

    st.divider()
    st.markdown(_heading_html("TestLink", "link"), unsafe_allow_html=True)
    with st.expander("连接与导入", expanded=False):
        tl_url = st.text_input(
            "TestLink XML-RPC URL",
            value="http://localhost/testlink/lib/api/xmlrpc/v1/xmlrpc.php",
        )
        tl_key = st.text_input(
            "API Key",
            value=os.getenv("TESTLINK_API_KEY", ""),
            type="password",
            help="或通过环境变量 TESTLINK_API_KEY",
        )
        if "tl_projects" not in st.session_state:
            st.session_state.tl_projects = []
        if st.button("测试连接并拉取项目"):
            if not tl_key:
                st.error("请填写 API Key")
            else:
                try:
                    TLI = get_testlink_importer()
                    temp = TLI(tl_url, tl_key, "", "admin")
                    projects = temp.get_projects_list()
                    st.session_state.tl_projects = projects
                    st.success(f"已连接，项目数: {len(projects)}")
                except Exception as e:
                    st.error(f"失败: {e}")
                    st.session_state.tl_projects = []

        if st.session_state.tl_projects:
            tl_project = st.selectbox("项目", st.session_state.tl_projects)
        else:
            tl_project = st.text_input("项目名称", value="AI_Generated_Project")
        tl_user = st.text_input("作者用户名", value="admin")
        if st.button("导入到 TestLink", disabled=not cases):
            if not tl_key:
                st.error("请填写 API Key")
            else:
                with st.spinner("导入中…"):
                    try:
                        TLI = get_testlink_importer()
                        importer = TLI(tl_url, tl_key, tl_project, tl_user)
                        success, fail = importer.import_test_cases(cases)
                        if success > 0:
                            st.success(f"成功 {success}，失败 {fail}")
                        else:
                            st.warning(f"成功 {success}，失败 {fail}，请检查项目名与权限。")
                    except Exception as e:
                        st.error(f"导入异常: {e}")


def main() -> None:
    icon_path = _UI_DIR / "assets" / "icon.svg"
    st.set_page_config(
        page_title="AI 测试用例生成",
        page_icon=str(icon_path) if icon_path.exists() else None,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(APP_CSS, unsafe_allow_html=True)

    _init_session()

    st.markdown("# AI 测试用例生成")
    st.markdown('<p class="app-subtitle">需求导入、批量生成、评审与导出（单机数据保存在 data 目录）</p>', unsafe_allow_html=True)

    with st.sidebar:
        render_sidebar()

    tab_guide, tab_in, tab_gen, tab_out = st.tabs(
        ["使用说明", "导入需求", "生成用例", "评审与导出"]
    )
    with tab_guide:
        render_tab_guide()
    with tab_in:
        render_tab_import()
    with tab_gen:
        render_tab_generate()
    with tab_out:
        render_tab_review_export()


if __name__ == "__main__":
    main()
