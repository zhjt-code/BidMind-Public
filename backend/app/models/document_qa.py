"""文档质检持久化模型

document_qa_runs: 一次 QA 运行(scope=document 单文档 / scope=envelope 信封收敛)
document_qa_findings: 运行产出的发现项(人工处置在 finding 行上推进)

与 review_results 物理隔离: QA 发现不绑定 Requirement, 不参与符合度汇总。
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class DocumentQaRun(BaseModel):
    """文档质检运行记录"""

    __tablename__ = "document_qa_runs"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "scope", "input_fingerprint",
            name="uq_document_qa_run_fingerprint",
        ),
    )

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目ID",
    )
    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="scope=document 时的文档ID; envelope 运行为 NULL",
    )
    scope = Column(String(16), nullable=False, comment="document / envelope")
    input_fingerprint = Column(
        String(64), nullable=False, comment="输入指纹(文件+内容+规则版本+配置)"
    )
    ruleset_version = Column(String(32), nullable=False, comment="规则集版本")
    status = Column(
        String(16), nullable=False, default="running",
        comment="running/completed/degraded/failed",
    )
    is_active = Column(
        Boolean, nullable=False, default=False,
        comment="是否为当前生效运行(每信封至多一条 envelope active)",
    )
    envelope_signature = Column(
        JSONB, nullable=True,
        comment="信封快照 [{document_id,bid_part,file_hash,content_hash}]",
    )
    features = Column(
        JSONB, nullable=True,
        comment="跨卷收敛消费的紧凑特征(槽位台账/段落哈希/标题树)",
    )
    summary = Column(JSONB, nullable=True, comment="汇总统计(按类别/严重度计数等)")
    checker_status = Column(
        JSONB, nullable=True, comment="各检查器状态 {code: ok/failed/deferred/skipped}"
    )
    error_message = Column(Text, nullable=True, comment="失败/降级原因")

    findings = relationship(
        "DocumentQaFinding",
        back_populates="run",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<DocumentQaRun(id={self.id}, project={self.project_id}, "
            f"scope='{self.scope}', status='{self.status}', active={self.is_active})>"
        )


class DocumentQaFinding(BaseModel):
    """文档质检发现项"""

    __tablename__ = "document_qa_findings"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "finding_fingerprint",
            name="uq_document_qa_finding_fingerprint",
        ),
    )

    run_id = Column(
        Integer,
        ForeignKey("document_qa_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属运行ID",
    )
    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目ID(冗余便于按项目清理/查询)",
    )
    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        comment="主证据文档ID; 跨卷发现为 NULL(证据内逐项标注)",
    )
    rule_code = Column(String(64), nullable=False, comment="规则编码 如 DOC-NUM-001")
    category = Column(String(32), nullable=False, index=True, comment="检查类别")
    severity = Column(String(8), nullable=False, index=True, comment="严重度 P0-P3")
    certainty = Column(
        String(16), nullable=False, comment="certain/probable/possible/unknown"
    )
    title = Column(String(200), nullable=False, comment="问题标题")
    message = Column(Text, nullable=False, comment="问题描述(禁止废标类措辞)")
    suggestion = Column(Text, nullable=True, comment="修改建议")
    evidence = Column(
        JSONB, nullable=False,
        comment="证据 {kind,items[{document_id,bid_part,page,excerpt,bbox,role}],expected,actual}",
    )
    finding_fingerprint = Column(
        String(64), nullable=False, index=True,
        comment="发现项指纹(规则+卷+结构锚点), 供重跑幂等与处置结转",
    )
    disposition = Column(
        String(20), nullable=False, default="pending",
        comment="pending/confirmed/false_positive/ignored",
    )
    disposition_note = Column(Text, nullable=True, comment="处置备注")
    disposition_version = Column(
        Integer, nullable=False, default=0, comment="处置乐观锁版本号"
    )
    disposed_by = Column(Integer, nullable=True, comment="处置人 user_id")
    disposed_at = Column(DateTime(timezone=True), nullable=True, comment="处置时间")

    run = relationship("DocumentQaRun", back_populates="findings")

    def __repr__(self) -> str:
        return (
            f"<DocumentQaFinding(id={self.id}, rule='{self.rule_code}', "
            f"severity='{self.severity}', disposition='{self.disposition}')>"
        )
