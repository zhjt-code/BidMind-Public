"""健康检查路由

提供服务健康状态检查端点。
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from app.core.database import get_db


router = APIRouter()


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str
    database: str
    version: str
    message: Optional[str] = None


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    """健康检查端点
    
    检查服务和数据库连接状态。
    
    Returns:
        HealthResponse: 健康状态信息
    """
    db_status = "disconnected"
    message = None
    
    try:
        # 测试数据库连接
        await db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as e:
        db_status = "error"
        message = str(e)
    
    return HealthResponse(
        status="healthy" if db_status == "connected" else "unhealthy",
        database=db_status,
        version="0.1.0",
        message=message
    )


@router.get("/health/live")
async def liveness():
    """存活探针
    
    用于 Kubernetes 等容器编排系统的存活检查。
    
    Returns:
        dict: 存活状态
    """
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness(db: AsyncSession = Depends(get_db)):
    """就绪探针
    
    检查服务是否准备好接收请求。
    数据库不可用时返回 503。
    """
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
