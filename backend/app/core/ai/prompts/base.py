"""Prompt 基础模板类"""

from typing import Any


class PromptTemplate:
    """Prompt 模板类
    
    提供简单的字符串模板功能。
    
    示例:
        >>> template = PromptTemplate("你好，{name}！")
        >>> prompt = template.format(name="世界")
        >>> print(prompt)  # 你好，世界！
    """
    
    def __init__(self, template: str):
        """初始化模板
        
        Args:
            template: 模板字符串，使用 {variable} 作为占位符
        """
        self.template = template
    
    def format(self, **kwargs: Any) -> str:
        """格式化模板
        
        Args:
            **kwargs: 模板变量
            
        Returns:
            str: 格式化后的字符串
        """
        return self.template.format(**kwargs)
    
    def __str__(self) -> str:
        return self.template
