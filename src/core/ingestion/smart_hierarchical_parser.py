import pandas as pd
import json
import io
from typing import Dict, List, Any, Optional, Union
import numpy as np

class SmartHierarchicalParser:
    """
    Parses Excel files with hierarchical structure (merged cells) into
    AI-friendly formats (Markdown tree, Nested JSON, etc.).
    """

    def parse_excel(self, file_path: str, sheet_name: Union[str, int] = 0, skip_rows: Optional[int] = None) -> Dict[str, Any]:
        """
        Parse an Excel file and return structured data.
        
        Args:
            file_path: Path to the Excel file
            sheet_name: Sheet name or index
            skip_rows: Number of rows to skip. If None, auto-detects header.
        
        Returns:
            Dict containing headers, records, and metadata.
        """
        # Read with header=None to see everything
        df = pd.read_excel(file_path, sheet_name=sheet_name, header=None)
        
        # Detect header row if not provided
        if skip_rows is None:
            header_idx = self._detect_header_row(df)
        else:
            header_idx = skip_rows
            
        # Set headers
        if header_idx is not None and header_idx < len(df):
            # Set the header
            headers = df.iloc[header_idx].astype(str).str.strip().tolist()
            
            # Dedup and clean headers
            seen = {}
            deduped_headers = []
            for i, h in enumerate(headers):
                # Replace nan/empty with a generic name
                if h.lower() == 'nan' or not h:
                    # Try to infer based on previous column if it looks like a merged header
                    # But safer to just call it Level_N or Column_N
                    h = f"Level_{i+1}"
                
                if h in seen:
                    seen[h] += 1
                    deduped_headers.append(f"{h}.{seen[h]}")
                else:
                    seen[h] = 0
                    deduped_headers.append(h)
            
            # Slice data to start after header
            df = df.iloc[header_idx + 1:].copy()
            df.columns = deduped_headers
        else:
            # Use default 0,1,2 headers if no good header found
            df.columns = [str(i) for i in df.columns]
        
        # Clean data
        df = self._clean_dataframe(df)
        
        # Identify hierarchy columns FIRST using a temporary ffill
        # This is needed to know WHICH columns are hierarchy for the smart fill logic
        df_temp = df.copy().ffill()
        hierarchy_cols = self._identify_hierarchy_columns(df_temp)
        
        # Apply Smart Forward Fill (Structured Fill)
        # This prevents lower levels from leaking into new higher-level sections
        df_filled = self._smart_forward_fill(df, hierarchy_cols)
        
        # Final cleanup: Replace NaN with empty string
        df_filled = df_filled.fillna("")
        
        # Convert to records
        records = df_filled.to_dict(orient='records')
        
        result = {
            "headers": list(df.columns),
            "records": records,
            "metadata": {
                "source": file_path,
                "sheet": sheet_name,
                "total_records": len(records),
                "hierarchy_cols": hierarchy_cols
            }
        }
        
        # Generate AI friendly markdown
        result["ai_markdown"] = self.to_ai_friendly_markdown(result)
        
        return result

    def to_ai_friendly_markdown(self, data: Dict[str, Any]) -> str:
        """
        Generate a comprehensive Markdown document with both tree view and table view.
        """
        md_lines = []
        meta = data['metadata']
        
        md_lines.append(f"# Sheet Data: {meta.get('sheet', 'Unknown')}")
        md_lines.append("")
        md_lines.append(f"> **Source**: {meta['source']}")
        md_lines.append(f"> **Records**: {meta['total_records']}")
        md_lines.append("")
        
        md_lines.append("## 📋 Data Structure")
        md_lines.append("This document contains hierarchical requirements data.")
        md_lines.append("")
        
        # Tree View
        md_lines.append("## 🌳 Tree View (Hierarchy)")
        tree_content = self.to_markdown_tree(data)
        md_lines.append(tree_content)
        md_lines.append("")
        
        # Table View
        md_lines.append("## 📊 Table View (Full Details)")
        table_content = self._generate_markdown_table(data['headers'], data['records'])
        md_lines.append(table_content)
        
        return "\n".join(md_lines)

    def to_markdown_tree(self, data: Dict[str, Any], 
                         level1_col: str = None, 
                         level2_col: str = None, 
                         level3_col: str = None,
                         detail_col: str = None) -> str:
        """
        Generate a Markdown tree structure from the data.
        """
        records = data['records']
        if not records:
            return "(No data)"
            
        # Determine columns if not provided
        hierarchy_cols = data['metadata']['hierarchy_cols']
        
        # If manual columns provided, use them
        levels = []
        if level1_col: levels.append(level1_col)
        if level2_col: levels.append(level2_col)
        if level3_col: levels.append(level3_col)
        
        # If not, use auto-detected ones (up to 3 levels)
        if not levels:
            levels = hierarchy_cols[:3]
            
        # Identify the detail column (usually the last non-hierarchy column with long text)
        if not detail_col:
            remaining_cols = [c for c in data['headers'] if c not in levels]
            # Heuristic: longest average string length
            best_col = None
            max_len = 0
            for col in remaining_cols:
                # Sample first 20 rows
                sample_vals = [str(r.get(col, "")) for r in records[:20]]
                avg_len = sum(len(v) for v in sample_vals) / len(sample_vals) if sample_vals else 0
                if avg_len > max_len:
                    max_len = avg_len
                    best_col = col
            detail_col = best_col

        return self._build_tree_string(records, levels, detail_col)

    def to_nested_json(self, data: Dict[str, Any]) -> Dict:
        records = data['records']
        hierarchy_cols = data['metadata']['hierarchy_cols']

        tree = {}

        for row in records:
            current = tree

            for col in hierarchy_cols[:-1]:
                key = row.get(col, "Unknown")
                current = current.setdefault(key, {})

            # 最后一层
            last_key = row.get(hierarchy_cols[-1], "Unknown")
            current[last_key] = row

        return tree

    def _detect_header_row(self, df: pd.DataFrame) -> int:
        """
        Detect the most likely header row.
        Prioritizes rows with keywords like 'ID', 'Name', 'Module', '项目'.
        Otherwise picks the row with most non-null strings.
        """
        max_non_null = 0
        best_row = 0
        
        # Check first 10 rows
        limit = min(10, len(df))
        
        keywords = ['id', 'no', '序号', 'case', 'title', 'module', 'feature', 'system', 'description', 'step', 'expected', 'result', '项目', '名称', '模块', '功能', '要求']
        
        for i in range(limit):
            row = df.iloc[i]
            # Count non-null strings
            strings = [str(val).lower() for val in row if isinstance(val, str) and len(val.strip()) > 0]
            count = len(strings)
            
            # Bonus for keywords
            keyword_matches = sum(1 for s in strings if any(k in s for k in keywords))
            score = count + (keyword_matches * 2)
            
            if score > max_non_null:
                max_non_null = score
                best_row = i
                
        # If best_row is 0 and it has very few strings (like 1), but next row has many (like 5),
        # and row 0 looks like a title (merged), maybe we should check if row 1 is better?
        # But for "检验项目" (Project), it is often the header for column 1.
        # Let's stick to the score. "检验项目" matches "项目" (+2). Total score 3 (1 string + 2 bonus).
        # Row 1 "1" "System"... "System" matches? No. "Feature"? No.
        # If Row 1 is data "1", "SawSystem", "Home", "Map"...
        # No keywords. Score = 5 (5 strings).
        # 5 > 3. So it still picks Row 1.
        
        # Adjust heuristic: Header usually doesn't start with a number.
        first_val = str(df.iloc[best_row, 0]).strip()
        if first_val.isdigit() and best_row > 0:
            # If the "best" row starts with a number, it's likely data. 
            # Fallback to previous row if it has decent score?
            # Or just prefer row 0?
            # Let's prefer the first row that has a keyword.
            for i in range(limit):
                row = df.iloc[i]
                strings = [str(val).lower() for val in row if isinstance(val, str)]
                if any(any(k in s for k in keywords) for s in strings):
                    return i
                    
        return best_row

    def _smart_forward_fill(self, df: pd.DataFrame, hierarchy_cols: List[str]) -> pd.DataFrame:
        """
        Intelligent forward fill that respects hierarchy boundaries.
        When a higher-level column has a value, lower-level context is reset.
        """
        if not hierarchy_cols:
            return df.ffill()
            
        df = df.copy()
        
        # We need to iterate rows to maintain state. 
        # Vectorized approach is hard for this specific logic, so we iterate.
        # But we can optimize by only iterating hierarchy columns.
        
        # Initialize context with NaNs
        context = {col: np.nan for col in hierarchy_cols}
        
        # Map column index for speed
        col_indices = {col: df.columns.get_loc(col) for col in hierarchy_cols}
        sorted_hierarchy = sorted(hierarchy_cols, key=lambda c: col_indices[c])
        
        filled_rows = []
        
        for idx, row in df.iterrows():
            new_row = row.copy()
            
            # Check for changes in hierarchy
            # Find the highest level (left-most) that has a value
            change_level_idx = -1
            
            for i, col in enumerate(sorted_hierarchy):
                val = row[col]
                if not pd.isna(val) and str(val).strip() != "":
                    change_level_idx = i
                    break
            
            # If a level changed, update context for that level and RESET deeper levels
            if change_level_idx != -1:
                # Update context for the changed level and deeper ones that are present
                # Actually, if Level 2 changes, Level 3 context should be wiped UNLESS Level 3 also has a value in this row.
                
                # Update context from current row values
                for i, col in enumerate(sorted_hierarchy):
                    val = row[col]
                    if not pd.isna(val) and str(val).strip() != "":
                        context[col] = val
                    else:
                        # If we are deeper than the change level, we must RESET context (set to NaN)
                        # because we started a new section at change_level_idx
                        if i > change_level_idx:
                            context[col] = np.nan
                        # Else (i < change_level_idx): keep existing context (should be filled already)
            
            # Now apply context to the row
            for col in sorted_hierarchy:
                if pd.isna(new_row[col]) or str(new_row[col]).strip() == "":
                    new_row[col] = context[col]
            
            filled_rows.append(new_row)
            
        return pd.DataFrame(filled_rows, columns=df.columns)

    def _clean_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Clean DataFrame: drop empty rows/cols, strip whitespace.
        """
        # Drop completely empty rows
        df = df.dropna(how='all')
        # Drop completely empty columns
        df = df.dropna(axis=1, how='all')
        
        # Strip string columns
        # Use simple string conversion for object columns to be safe, or just check dtype
        for col in df.columns:
            if df[col].dtype == 'object':
                 # Convert to string and strip, but keep NaN as NaN
                 # Or better: just strip strings
                 df[col] = df[col].apply(lambda x: str(x).strip() if isinstance(x, str) else x)

        # Replace empty strings with NaN to allow ffill to work
        df = df.replace(r'^\s*$', np.nan, regex=True)
        
        return df

    def _identify_hierarchy_columns(self, df: pd.DataFrame) -> List[str]:
        """
        Identify which columns likely represent hierarchy.
        Heuristic: Columns with low cardinality (few unique values) relative to row count
        and appearing on the left side.
        """
        hierarchy_cols = []
        total_rows = len(df)
        if total_rows < 2:
            return list(df.columns)

        for i, col in enumerate(df.columns):
            # Skip if column name implies it's an ID or Description
            col_lower = str(col).lower()
            if any(x in col_lower for x in ['id', 'no', '序号', 'description', 'detail', 'content', '要求', '说明']):
                continue
                
            unique_count = df[col].nunique()
            ratio = unique_count / total_rows
            
            # Check if values look like IDs (digits)
            is_numeric = False
            try:
                # Check sample of values
                sample = df[col].dropna().head(5)
                if not sample.empty and all(str(x).strip().isdigit() for x in sample):
                    is_numeric = True
            except:
                pass

            # If unique ratio is low (e.g., < 80%), it's likely a category
            # Bumped from 0.5 to 0.8 to capture more granular levels (like Sub-features)
            if ratio < 0.8:
                hierarchy_cols.append(col)
            else:
                # High cardinality.
                # If it's the first column OR looks numeric, assume it's an ID and skip it (continue).
                if i == 0 or is_numeric:
                    continue
                # Once we hit a high-cardinality column that isn't an ID, stop (assuming hierarchy is on the left)
                break
                
        return hierarchy_cols

    def _build_tree_string(self, records: List[Dict], levels: List[str], detail_col: str) -> str:
        """
        Recursive function to build Markdown tree.
        """
        if not levels:
            return ""

        lines = []
        
        # Group by the first level
        # We need to preserve order, so we can't just use a set
        seen_keys = []
        grouped = {}
        
        current_level = levels[0]
        remaining_levels = levels[1:]
        
        for record in records:
            key = record.get(current_level)
            if pd.isna(key) or key == "":
                key = "Unknown"
            
            if key not in grouped:
                grouped[key] = []
                seen_keys.append(key)
            grouped[key].append(record)
            
        # Build output
        for key in seen_keys:
            # Add header for this level
            # Level 1 -> ##, Level 2 -> ###, etc.
            # Base indentation is implicit in Markdown headers, but we want a nested list look
            # or headers. The guide uses headers.
            
            # Actually, standard markdown headers don't nest infinitely well visually.
            # Let's use bullet points for deeper levels or just headers.
            # Guide Example:
            # ## Module
            # ### Feature
            # - **Detail**: text...
            
            # Determine header depth. Let's assume top level is H2.
            # But we are recursing. This function is complex to do purely recursively with headers.
            # Let's try a simpler approach: strict H2/H3 for top levels, then lists.
            
            pass # We'll handle this in the loop below
            
        # Better implementation:
        output = []
        
        for key in seen_keys:
            group_records = grouped[key]
            
            # Level 1 (e.g., System/Module)
            output.append(f"## {key}")
            output.append("")
            
            if remaining_levels:
                # Process Level 2
                level2_col = remaining_levels[0]
                
                seen_l2 = []
                grouped_l2 = {}
                for r in group_records:
                    k2 = r.get(level2_col)
                    if pd.isna(k2) or k2 == "": k2 = "General"
                    if k2 not in grouped_l2:
                        grouped_l2[k2] = []
                        seen_l2.append(k2)
                        
                    grouped_l2[k2].append(r)
                    
                for k2 in seen_l2:
                    output.append(f"### {k2}")
                    output.append("")
                    
                    # Process Items (Level 3 or Details)
                    items = grouped_l2[k2]
                    for item in items:
                        detail = item.get(detail_col, "")
                        # Try to find a "Name" or "Title" for the item if available
                        # Maybe there is a level 3?
                        if len(remaining_levels) > 1:
                            l3_col = remaining_levels[1]
                            l3_val = item.get(l3_col)
                            if l3_val:
                                output.append(f"- **{l3_val}**: {detail}")
                            else:
                                output.append(f"- {detail}")
                        else:
                            # Just detail
                            # Try to find a short summary column
                            summary = str(detail)[:50] + "..." if len(str(detail)) > 50 else detail
                            output.append(f"- {detail}")
                    
                    output.append("")
            else:
                # No more levels, just list items
                for item in group_records:
                    detail = item.get(detail_col, "")
                    output.append(f"- {detail}")
                output.append("")
                
        return "\n".join(output)

    def _generate_markdown_table(self, headers: List[str], records: List[Dict]) -> str:
        """
        Generate a standard Markdown table.
        """
        if not headers:
            return ""
            
        # Ensure headers are strings
        str_headers = [str(h) for h in headers]
            
        # Header row
        md = "| " + " | ".join(str_headers) + " |\n"
        # Separator row
        md += "| " + " | ".join(["---"] * len(headers)) + " |\n"
        
        # Data rows
        for record in records:
            row_vals = []
            for h in headers:
                val = str(record.get(h, ""))
                # Escape pipes in content
                val = val.replace("|", "\\|").replace("\n", "<br>")
                row_vals.append(val)
            md += "| " + " | ".join(row_vals) + " |\n"
            
        return md
