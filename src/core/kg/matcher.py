import os
from typing import List, Dict, Any, Optional, Tuple
from rapidfuzz import fuzz
from loguru import logger
import numpy as np

SentenceTransformer = None
EMBEDDING_AVAILABLE = None


class KeywordMatcher:
    """
    Two-stage keyword matching:
      Stage 1 (Fast)   — rapidfuzz literal match, O(n) per query
      Stage 2 (Semantic) — embedding cosine similarity, uses prebuilt index
    """

    FUZZY_THRESHOLD = 80      # rapidfuzz score threshold (0-100)
    SEMANTIC_THRESHOLD = 0.75  # cosine similarity threshold (0.0-1.0)

    # 推荐用支持中文的多语言模型，或专门的中文模型
    # paraphrase-multilingual 体积小速度快，中文效果够用
    # 如果精度要求高，换 BAAI/bge-base-zh-v1.5
    DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

    def __init__(self, model_name: str = DEFAULT_MODEL, use_semantic: bool = None):
        self._model: Optional[Any] = None
        self._model_name = model_name
        
        # Determine if semantic should be used
        if use_semantic is None:
            # Check env var for manual override
            env_val = os.getenv("USE_SEMANTIC_MATCHER", "false").lower()
            use_semantic = (env_val == "true")
            
        self._use_semantic = bool(use_semantic)

        # 预计算索引: { text -> embedding vector }
        # 在 build_index() 调用后填充
        self._index_texts: List[str] = []
        self._index_matrix: Optional[np.ndarray] = None  # shape: (n, dim)

        # Do not load during init if it blocks startup; build_index or is_match will trigger it
        # if self._use_semantic:
        #     self._load_model()

    def _load_model(self):
        """懒加载模型，首次调用时才真正加载"""
        global SentenceTransformer, EMBEDDING_AVAILABLE
        if EMBEDDING_AVAILABLE is None:
            try:
                from sentence_transformers import SentenceTransformer as _SentenceTransformer
                SentenceTransformer = _SentenceTransformer
                EMBEDDING_AVAILABLE = True
            except ImportError:
                EMBEDDING_AVAILABLE = False
                logger.warning("sentence-transformers not installed. Semantic matching disabled.")

        if not EMBEDDING_AVAILABLE:
            self._use_semantic = False
            return

        if self._model is None:
            logger.info(f"Loading embedding model: {self._model_name}")
            self._model = SentenceTransformer(self._model_name)
            logger.info("Embedding model loaded.")

    # ------------------------------------------------------------------
    # Index Management（KG 节点预计算）
    # ------------------------------------------------------------------

    def build_index(self, texts: List[str]):
        """
        预计算所有 KG 节点的向量，构建检索索引。
        在 KG 初始化或节点变更时调用一次即可。

        Args:
            texts: KG 中所有节点的文本列表（模块名、约束描述等）
        """
        if not self._use_semantic or not texts:
            return

        self._load_model() # Ensure model is loaded before encoding

        self._index_texts = [t.strip() for t in texts]
        logger.info(f"Building embedding index for {len(texts)} nodes...")
        embeddings = self._model.encode(self._index_texts, normalize_embeddings=True)
        self._index_matrix = np.array(embeddings)  # (n, dim), already L2-normalized
        logger.info("Embedding index built.")

    def update_index(self, new_texts: List[str]):
        """增量更新索引（新增节点时使用）"""
        if not self._use_semantic or not new_texts:
            return

        self._load_model() # Ensure model is loaded before encoding

        new_embeddings = self._model.encode(
            [t.strip() for t in new_texts],
            normalize_embeddings=True
        )
        self._index_texts.extend(new_texts)
        if self._index_matrix is not None:
            self._index_matrix = np.vstack([self._index_matrix, new_embeddings])
        else:
            self._index_matrix = np.array(new_embeddings)

    # ------------------------------------------------------------------
    # Core Matching API
    # ------------------------------------------------------------------

    def is_match(
        self,
        target: str,
        keyword: str,
        aliases: Optional[List[str]] = None
    ) -> bool:
        """
        两阶段匹配：先字面，再语义。
        任一阶段命中即返回 True。
        """
        # Stage 1: 字面匹配（快速路径）
        if self._literal_match(target, keyword, aliases):
            return True

        # Stage 2: 语义匹配（仅在字面匹配失败时触发）
        if self._use_semantic:
            score = self._semantic_score(target, keyword)
            if score >= self.SEMANTIC_THRESHOLD:
                logger.debug(f"Semantic match: '{keyword}' ~ '{target}' (score={score:.3f})")
                return True

        return False

    def get_match_score(self, target: str, keyword: str) -> float:
        """
        返回综合相似度分数 (0.0 - 1.0)。
        取字面分和语义分的最大值，保证两个方法行为一致。
        """
        literal_score = fuzz.token_sort_ratio(
            keyword.lower().strip(),
            target.lower().strip()
        ) / 100.0

        if not self._use_semantic:
            return literal_score

        semantic_score = self._semantic_score(target, keyword)
        return max(literal_score, semantic_score)

    def find_best_matches(
        self,
        query: str,
        candidates: List[str],
        top_k: int = 5
    ) -> List[Tuple[str, float]]:
        """
        在候选列表中找最相似的 top_k 个，返回 (text, score) 列表。
        利用预构建索引时走矩阵运算，否则逐一计算。

        用于 KG 中"找最相关节点"场景。
        """
        if not candidates:
            return []

        # 如果候选集恰好是已建索引的集合，走快速矩阵路径
        if (
            self._use_semantic
            and self._index_matrix is not None
            and set(candidates) == set(self._index_texts)
        ):
            return self._search_index(query, top_k)

        # 否则 fallback 到逐一计算
        scores = [(c, self.get_match_score(query, c)) for c in candidates]
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    def _literal_match(
        self,
        target: str,
        keyword: str,
        aliases: Optional[List[str]] = None
    ) -> bool:
        """字面匹配：子串 + fuzzy ratio"""
        keyword = keyword.lower().strip()
        target = target.lower().strip()

        # 子串（双向，修复原来的单向 bug）
        if keyword in target or target in keyword:
            return True

        # Fuzzy
        if fuzz.partial_ratio(keyword, target) >= self.FUZZY_THRESHOLD:
            return True

        # Alias（同样双向）
        if aliases:
            for alias in aliases:
                alias = alias.lower().strip()
                if keyword in alias or alias in keyword:
                    return True
                if fuzz.partial_ratio(keyword, alias) >= self.FUZZY_THRESHOLD:
                    return True

        return False

    def _semantic_score(self, target: str, keyword: str) -> float:
        """计算两个字符串之间的语义相似度（余弦，已归一化所以用点积）"""
        if not self._use_semantic:
            return 0.0
        vecs = self._model.encode(
            [keyword.strip(), target.strip()],
            normalize_embeddings=True
        )
        return float(np.dot(vecs[0], vecs[1]))

    def _search_index(self, query: str, top_k: int) -> List[Tuple[str, float]]:
        """对预建索引做批量余弦检索，O(n) 矩阵乘法"""
        query_vec = self._model.encode([query.strip()], normalize_embeddings=True)
        scores = self._index_matrix @ query_vec[0]  # (n,)
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [(self._index_texts[i], float(scores[i])) for i in top_indices]
