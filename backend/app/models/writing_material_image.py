"""标书编写 -- 素材图片模型

记录从素材文件(PDF/DOCX/独立图片)中提取的每张图片及其分类、
细粒度业务子类、结构化字段和标注信息,供内容生成与文档渲染阶段引用。
"""

import enum

from sqlalchemy import Column, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import backref, relationship

from app.models.base import BaseModel


class ImageType(str, enum.Enum):
    """图片类型枚举

    可结构化的4种:
      license        -- 营业执照、税务登记证等企业证件
      personnel_cert -- 人员证书、身份证、职称证
      qualification  -- 行业准入证、ISO认证、安全生产许可证
      nameplate      -- 设备铭牌、出厂标牌

    不可结构化的3种:
      photo   -- 机械设备照片、施工现场、办公场所
      diagram -- 组织架构图、技术方案流程图
      other   -- 无法归类的图片
    """
    license = "license"
    personnel_cert = "personnel_cert"
    qualification = "qualification"
    nameplate = "nameplate"
    photo = "photo"
    diagram = "diagram"
    other = "other"


STRUCTURABLE_IMAGE_TYPES = frozenset({
    ImageType.license,
    ImageType.personnel_cert,
    ImageType.qualification,
    ImageType.nameplate,
})


class WritingMaterialImage(BaseModel):
    """素材图片模型

    每条记录对应从某个 WritingMaterial 中提取出的一张图片。
    图片在 VL 识别后经过分类与条件化结构字段提取,存储为
    独立记录,供 SectionComposer 和 DocRenderer 引用。

    business_subtype 用于在 image_type 粗类下进一步区分真实业务语义:
      license:        business_license / tax_registration / org_code / bank_permit
      personnel_cert: id_card / degree_cert / title_cert / qualification_personnel
      qualification:  qualification_cert / iso_cert / safety_permit
      other:          invoice_receipt / tax_social_proof / honor_cert / contract_notice / authorization_letter ...

    structured_fields 以 JSON 字符串形式存储,格式因 image_type 而异:
      license:        {"证件类型": "", "统一社会信用代码": "", ...}
      personnel_cert: {"姓名": "", "证件号码": "", "专业": "", ...}
      qualification:  {"资质名称": "", "等级": "", "证书编号": "", ...}
      nameplate:      {"设备名称": "", "型号规格": "", "制造商": "", ...}
      photo/diagram/other: null
    """

    __tablename__ = "writing_material_images"

    material_id = Column(
        Integer,
        ForeignKey("writing_materials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属素材ID",
    )
    image_path = Column(
        String(500), nullable=False, comment="图片磁盘存储路径",
    )
    image_index = Column(
        Integer, nullable=False, default=0,
        comment="在素材中的序号(从0开始)",
    )
    vl_content = Column(
        Text, nullable=True, comment="VL模型原始识别文本",
    )
    image_type = Column(
        Enum(ImageType), nullable=True, comment="图片类型(7种子类型)",
    )
    caption = Column(
        String(500), nullable=True,
        comment="自动生成的标注文本(所有类型均有)",
    )
    business_subtype = Column(
        String(50), nullable=True,
        comment="细粒度业务子类(如 tax_registration/id_card/invoice_receipt)",
    )
    structured_fields = Column(
        Text, nullable=True,
        comment="结构化字段(JSON),仅可结构化类型有值",
    )
    width = Column(Integer, nullable=False, default=0, comment="图片宽度(px)")
    height = Column(Integer, nullable=False, default=0, comment="图片高度(px)")
    file_hash = Column(
        String(64), nullable=True, index=True,
        comment="文件内容SHA-256哈希(前64KB),用于跨素材去重",
    )

    material = relationship(
        "WritingMaterial",
        backref=backref("images", passive_deletes=True),
    )

    def __repr__(self) -> str:
        return (
            f"<WritingMaterialImage(id={self.id}, "
            f"type='{self.image_type}', material_id={self.material_id})>"
        )
