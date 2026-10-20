"""数据库模型基类"""

from datetime import datetime
from sqlalchemy import Column, Integer, DateTime
from sqlalchemy.sql import func
from app.core.database import Base


class BaseModel(Base):
    """所有模型的基类,提供通用字段"""
    
    __abstract__ = True
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    def to_dict(self) -> dict:
        """将模型转换为字典
        
        Returns:
            dict: 模型字典表示
        """
        return {
            column.name: getattr(self, column.name)
            for column in self.__table__.columns
        }
