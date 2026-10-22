"""文本分块器

使用LangChain RecursiveCharacterTextSplitter进行语义分块。
"""

from dataclasses import dataclass, field
from typing import Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter
from loguru import logger


@dataclass
class TextChunk:
    """文本分块"""
    chunk_index: int  # 分块索引（从0开始）
    content: str  # 分块内容
    char_count: int  # 字符数
    start_char: int  # 起始字符位置
    end_char: int  # 结束字符位置
    metadata: dict = field(default_factory=dict)  # 元数据
    
    @property
    def preview(self) -> str:
        """获取分块预览（前100字符）
        
        Returns:
            str: 分块内容预览
        """
        if len(self.content) <= 100:
            return self.content
        return self.content[:100] + "..."


class TextChunker:
    """文本分块器
    
    使用LangChain的RecursiveCharacterTextSplitter进行语义分块，
    支持中文文档的智能切分。
    
    示例:
        >>> chunker = TextChunker()
        >>> chunks = chunker.split("这是一段很长的文本...")
        >>> for chunk in chunks:
        ...     print(chunk.preview)
    """
    
    # 默认分隔符（优先级从高到低）
    DEFAULT_SEPARATORS = [
        "\n\n",  # 段落分隔
        "\n",    # 行分隔
        "。",    # 中文句号
        "！",    # 中文感叹号
        "？",    # 中文问号
        "；",    # 中文分号
        "，",    # 中文逗号
        ". ",    # 英文句号
        "! ",    # 英文感叹号
        "? ",    # 英文问号
        "; ",    # 英文分号
        ", ",    # 英文逗号
        " ",     # 空格
        "",      # 单字符（最后手段）
    ]
    
    def __init__(
        self,
        chunk_size: int = 1500,
        chunk_overlap: int = 200,
        separators: Optional[list[str]] = None,
        length_function: Optional[callable] = None
    ):
        """初始化分块器
        
        Args:
            chunk_size: 分块大小（字符数），默认1500
            chunk_overlap: 分块重叠（字符数），默认200
            separators: 分隔符列表，默认使用中英文混合分隔符
            length_function: 长度计算函数，默认len
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or self.DEFAULT_SEPARATORS
        self.length_function = length_function or len
        
        # 创建LangChain分块器
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=self.separators,
            length_function=self.length_function,
            is_separator_regex=False,
            keep_separator=True,  # 保留分隔符
        )
        
        logger.debug(
            f"TextChunker初始化: chunk_size={chunk_size}, "
            f"chunk_overlap={chunk_overlap}"
        )
    
    def split(
        self,
        text: str,
        metadata: Optional[dict] = None
    ) -> list[TextChunk]:
        """将文本分割成多个分块
        
        Args:
            text: 要分割的文本
            metadata: 附加到每个分块的元数据
            
        Returns:
            list[TextChunk]: 分块列表
        """
        if not text or not text.strip():
            logger.warning("输入文本为空")
            return []
        
        # 使用LangChain分割
        raw_chunks = self._splitter.split_text(text)
        
        # 转换为TextChunk对象
        chunks: list[TextChunk] = []
        current_pos = 0
        
        for idx, content in enumerate(raw_chunks):
            # 计算位置（考虑重叠，找到实际位置）
            start_char = text.find(content, current_pos)
            if start_char == -1:
                # 如果找不到，使用近似位置
                start_char = current_pos
            
            end_char = start_char + len(content)
            
            chunk = TextChunk(
                chunk_index=idx,
                content=content,
                char_count=len(content),
                start_char=start_char,
                end_char=end_char,
                metadata=metadata.copy() if metadata else {}
            )
            chunks.append(chunk)
            
            # 更新位置（考虑重叠）
            current_pos = max(start_char + 1, end_char - self.chunk_overlap)
        
        logger.info(
            f"文本分块完成: 输入 {len(text)} 字符, "
            f"输出 {len(chunks)} 个分块"
        )
        
        return chunks
    
    def split_with_page_tracking(
        self,
        pages: list[tuple[int, str]],
        metadata: Optional[dict] = None
    ) -> list[TextChunk]:
        """分割多页文本，保留页码信息
        
        Args:
            pages: 页面列表，每项为 (page_num, text) 元组
            metadata: 附加到每个分块的基础元数据
            
        Returns:
            list[TextChunk]: 分块列表，每个分块包含来源页码
        """
        if not pages:
            return []
        
        all_chunks: list[TextChunk] = []
        global_index = 0
        
        for page_num, page_text in pages:
            if not page_text or not page_text.strip():
                continue
            
            # 分割单页
            page_chunks = self.split(page_text, metadata=None)
            
            # 添加页码信息并更新全局索引
            for chunk in page_chunks:
                chunk.chunk_index = global_index
                chunk.metadata = {
                    **(metadata or {}),
                    "page_num": page_num,
                    "source": f"page_{page_num}"
                }
                all_chunks.append(chunk)
                global_index += 1
        
        logger.info(
            f"多页分块完成: {len(pages)} 页, "
            f"共 {len(all_chunks)} 个分块"
        )
        
        return all_chunks
    
    def estimate_chunk_count(self, text: str) -> int:
        """估算分块数量（不实际分割）
        
        Args:
            text: 输入文本
            
        Returns:
            int: 预估的分块数量
        """
        if not text:
            return 0
        
        text_length = len(text)
        effective_chunk_size = self.chunk_size - self.chunk_overlap
        
        if effective_chunk_size <= 0:
            return 1
        
        return max(1, (text_length + effective_chunk_size - 1) // effective_chunk_size)
    
    @classmethod
    def create_for_embedding(cls, model_max_tokens: int = 512) -> "TextChunker":
        """创建适合向量化的分块器
        
        针对常见的embedding模型（如bge-m3），创建优化的分块配置。
        
        Args:
            model_max_tokens: 模型最大token数，默认512
            
        Returns:
            TextChunker: 配置好的分块器实例
        """
        # 假设平均每个token约2个中文字符或4个英文字符
        # 取保守值，使用1.5倍系数
        chunk_size = int(model_max_tokens * 1.5)
        chunk_overlap = int(chunk_size * 0.15)  # 15%重叠
        
        return cls(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )
