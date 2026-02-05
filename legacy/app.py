import streamlit as st
import os
import pandas as pd
from src.input_layer.doc_parser import DocParser
from src.ai_layer.llm_client import LLMClient
from src.output_layer.exporter import TestCaseExporter

st.set_page_config(page_title="AI 测试用例生成助手", layout="wide")

st.title("🤖 AI 自动编写应用软件委托测试用例系统")
st.markdown("""
本系统旨在通过 AI 技术实现测试用例的自动化生成，提升编写效率和覆盖率。
流程：**上传需求文档 -> AI 解析与生成 -> 人工审核 -> 导出用例**
""")

# Sidebar Configuration
with st.sidebar:
    st.header("⚙️ 系统配置")
    api_key = st.text_input("OpenAI API Key", type="password", help="如果不填则使用 Mock 模式演示")
    base_url = st.text_input("Base URL (Optional)", placeholder="https://api.openai.com/v1")
    model = st.selectbox("模型选择", ["gpt-4-turbo", "gpt-3.5-turbo", "deepseek-chat", "qwen-turbo"])
    st.info("提示：支持兼容 OpenAI 接口的模型（如 DeepSeek, Moonshot, Qwen 等）")

# Main Area
st.subheader("📂 1. 需求输入层")
uploaded_file = st.file_uploader("请上传需求文档 (支持 .docx, .xlsx)", type=['docx', 'xlsx'])

if uploaded_file:
    # Save temp file
    temp_path = f"temp_{uploaded_file.name}"
    with open(temp_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    
    # Parse
    try:
        parser = DocParser(temp_path)
        doc_content = parser.parse()
        st.success(f"文件 `{uploaded_file.name}` 解析成功！")
        with st.expander("👁️ 查看解析后的原始文本内容"):
            st.text_area("Content", doc_content, height=200)
    except Exception as e:
        st.error(f"解析失败: {e}")
        st.stop()

    # Generate
    st.subheader("🧠 2. AI 处理层 & 用例生成")
    
    col_action, col_info = st.columns([1, 3])
    with col_action:
        generate_btn = st.button("🚀 开始生成测试用例", type="primary")
    
    if generate_btn:
        with st.spinner("正在进行语义分析、场景扩展与用例生成，请稍候..."):
            llm = LLMClient(api_key=api_key, base_url=base_url, model=model)
            # 模拟：如果文本太长，这里应该做切分，MVP 暂时全量传
            test_cases = llm.generate_test_cases(doc_content)
            st.session_state['test_cases'] = test_cases
            st.success(f"✅ 生成完成！共生成 {len(test_cases)} 条测试用例。")

    # Review & Export
    if 'test_cases' in st.session_state:
        st.subheader("📝 3. 执行反馈层 (人工审核)")
        st.caption("您可以直接在下方表格中修改、添加或删除用例，修改后的结果将用于导出。")
        
        # Editable Dataframe
        # 确保 DataFrame 列序正确
        df = pd.DataFrame(st.session_state['test_cases'])
        column_order = ["module", "test_point", "precondition", "steps", "expected_result", "test_data", "priority", "type"]
        # 补全缺失列
        for col in column_order:
            if col not in df.columns:
                df[col] = ""
        df = df[column_order]
        
        edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True, height=400)
        
        st.subheader("📤 4. 交付物导出")
        exporter = TestCaseExporter(edited_df.to_dict('records'))
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.download_button(
                label="📥 导出 Excel (通用)",
                data=exporter.to_excel(),
                file_name="test_cases.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        with col2:
             st.download_button(
                label="📥 导出 CSV (通用)",
                data=exporter.to_csv(),
                file_name="test_cases.csv",
                mime="text/csv"
            )
        with col3:
             st.download_button(
                label="📥 导出 CSV (禅道格式)",
                data=exporter.to_zentao_csv(),
                file_name="zentao_cases.csv",
                mime="text/csv"
            )
        
        st.download_button(
            label="📥 导出 Excel (飞书格式)",
            data=exporter.to_feishu_excel(),
            file_name="feishu_cases.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
        st.subheader("🔄 5. 模型优化反馈 (可选)")
        with st.expander("🛠️ 提交错误用例反馈，优化模型"):
            feedback_text = st.text_area("请输入优化建议或错误描述", placeholder="例如：登录模块缺少对特殊字符的测试覆盖...")
            if st.button("提交反馈并尝试优化"):
                if feedback_text:
                    st.info("反馈已收到！系统将尝试基于您的反馈微调后续生成逻辑。(当前版本仅演示接口调用)")
                    # 在实际场景中，这里会调用 llm.refine_test_cases() 并更新 session_state
                    # refined_cases = llm.refine_test_cases(edited_df.to_dict('records'), feedback_text)
                    # st.session_state['test_cases'] = refined_cases
                    # st.experimental_rerun()
                else:
                    st.warning("请填写反馈内容")

    # Cleanup is handled by OS eventually, or we can remove explicitely. 
    # For Streamlit, keeping it simple.
