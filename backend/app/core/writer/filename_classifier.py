"""文件名语义预分类器

从素材文件名中规则化提取业务类型标签。
文件名是用户上传时的第一手信号, 虽非最终真相,
但作为交叉校验的基准输入。

匹配策略:
- 规则表按长关键词优先排列, 列表靠前优先匹配
- 大小写不敏感, 英文关键词(如 ISO)同样支持
- 返回 (业务大类, 细分标签) 二元组
"""
from pathlib import Path
from typing import Optional

from app.models.writing_material import DocumentBusinessType

FILENAME_RULES: list[tuple[str, DocumentBusinessType, str]] = [
    ("法定代表人证明", DocumentBusinessType.authorization, "法定代表人证明"),
    ("法定代表人身份证", DocumentBusinessType.identity_document, "法定代表人身份证"),
    ("法人身份证", DocumentBusinessType.identity_document, "法人身份证"),
    ("法人代表证明", DocumentBusinessType.authorization, "法人代表证明"),
    ("法人证明", DocumentBusinessType.authorization, "法人证明"),
    ("身份证", DocumentBusinessType.identity_document, "身份证"),
    ("营业执照", DocumentBusinessType.business_license, "营业执照"),
    ("开户许可证", DocumentBusinessType.bank_permit, "开户许可证"),
    ("开户许可", DocumentBusinessType.bank_permit, "开户许可证"),
    ("银行开户", DocumentBusinessType.bank_permit, "银行开户信息"),
    ("基本存款账户", DocumentBusinessType.bank_permit, "基本存款账户信息"),
    ("授权委托书", DocumentBusinessType.authorization, "授权委托书"),
    ("授权委托", DocumentBusinessType.authorization, "授权委托书"),
    ("委托书", DocumentBusinessType.authorization, "委托书"),
    ("项目业绩综述", DocumentBusinessType.performance_proof, "项目业绩综述"),
    ("项目业绩", DocumentBusinessType.performance_proof, "项目业绩综述"),
    ("业绩综述", DocumentBusinessType.performance_proof, "项目业绩综述"),
    ("项目经验", DocumentBusinessType.performance_proof, "项目经验综述"),
    ("类似项目", DocumentBusinessType.performance_proof, "类似项目综述"),
    ("公司简介", DocumentBusinessType.company_profile, "公司简介"),
    ("企业简介", DocumentBusinessType.company_profile, "企业简介"),
    ("企业概况", DocumentBusinessType.company_profile, "企业概况"),
    ("公司及团队简介", DocumentBusinessType.company_profile, "公司简介"),
    ("团队简介", DocumentBusinessType.company_profile, "公司简介"),
    ("单位简介", DocumentBusinessType.company_profile, "单位简介"),
    ("质量管理制度", DocumentBusinessType.other, "质量管理制度"),
    ("安全管理制度", DocumentBusinessType.other, "安全管理制度"),
    ("服务承诺", DocumentBusinessType.other, "服务承诺"),
    ("售后服务", DocumentBusinessType.other, "售后服务承诺"),
    ("响应承诺", DocumentBusinessType.other, "响应承诺"),
    ("保障措施", DocumentBusinessType.other, "保障措施"),
    ("管理制度", DocumentBusinessType.other, "管理制度"),
    ("制度汇编", DocumentBusinessType.other, "制度汇编"),
    ("管理办法", DocumentBusinessType.other, "管理办法"),
    ("安全生产许可", DocumentBusinessType.qualification_cert, "安全生产许可证"),
    ("三体系", DocumentBusinessType.qualification_cert, "三体系认证"),
    ("ISO", DocumentBusinessType.qualification_cert, "ISO认证"),
    ("OHSAS", DocumentBusinessType.qualification_cert, "OHSAS认证"),
    ("质量管理体系", DocumentBusinessType.qualification_cert, "质量管理体系认证"),
    ("环境管理体系", DocumentBusinessType.qualification_cert, "环境管理体系认证"),
    ("职业健康", DocumentBusinessType.qualification_cert, "职业健康安全认证"),
    ("体系认证", DocumentBusinessType.qualification_cert, "体系认证"),
    ("认证证书", DocumentBusinessType.qualification_cert, "认证证书"),
    ("确认证书", DocumentBusinessType.qualification_cert, "确认证书"),
    ("资质证书", DocumentBusinessType.qualification_cert, "资质证书"),
    ("资质等级", DocumentBusinessType.qualification_cert, "资质等级证书"),
    ("社保缴纳", DocumentBusinessType.tax_social_proof, "社保缴纳证明"),
    ("社保证明", DocumentBusinessType.tax_social_proof, "社保证明"),
    ("社保", DocumentBusinessType.tax_social_proof, "社保证明"),
    ("纳税证明", DocumentBusinessType.tax_social_proof, "纳税证明"),
    ("完税证明", DocumentBusinessType.tax_social_proof, "完税证明"),
    ("完税", DocumentBusinessType.tax_social_proof, "完税证明"),
    ("纳税", DocumentBusinessType.tax_social_proof, "纳税证明"),
    ("审计报告", DocumentBusinessType.financial_document, "审计报告"),
    ("财务报表", DocumentBusinessType.financial_document, "财务报表"),
    ("财务审计", DocumentBusinessType.financial_document, "财务审计报告"),
    ("资产负债", DocumentBusinessType.financial_document, "资产负债表"),
    ("利润表", DocumentBusinessType.financial_document, "利润表"),
    ("发票", DocumentBusinessType.financial_document, "发票"),
    ("银行流水", DocumentBusinessType.financial_document, "银行流水"),
    ("中标通知书", DocumentBusinessType.performance_proof, "中标通知书"),
    ("中标通知", DocumentBusinessType.performance_proof, "中标通知书"),
    ("验收报告", DocumentBusinessType.performance_proof, "验收报告"),
    ("验收证明", DocumentBusinessType.performance_proof, "验收证明"),
    ("业绩合同", DocumentBusinessType.performance_proof, "业绩合同"),
    ("项目合同", DocumentBusinessType.performance_proof, "项目合同"),
    ("业绩证明", DocumentBusinessType.performance_proof, "业绩证明"),
    ("业绩", DocumentBusinessType.performance_proof, "业绩材料"),
    ("合同", DocumentBusinessType.performance_proof, "项目合同"),
    ("简历", DocumentBusinessType.personnel_info, "人员简历"),
    ("拟委任的主要人员汇总表", DocumentBusinessType.personnel_info, "人员配置方案"),
    ("人员汇总表", DocumentBusinessType.personnel_info, "人员配置方案"),
    ("人员配置表", DocumentBusinessType.personnel_info, "人员配置方案"),
    ("项目经理", DocumentBusinessType.personnel_info, "项目经理资料"),
    ("职称证", DocumentBusinessType.personnel_info, "职称证书"),
    ("学位证", DocumentBusinessType.personnel_info, "学位证书"),
    ("毕业证", DocumentBusinessType.personnel_info, "毕业证书"),
    ("学历证", DocumentBusinessType.personnel_info, "学历证书"),
    ("资格证", DocumentBusinessType.personnel_info, "资格证书"),
    ("执业资格", DocumentBusinessType.personnel_info, "执业资格证"),
    ("人员配置", DocumentBusinessType.personnel_info, "人员配置方案"),
    ("设备清单", DocumentBusinessType.equipment_info, "设备清单"),
    ("设备照片", DocumentBusinessType.equipment_info, "设备照片"),
    ("铭牌", DocumentBusinessType.equipment_info, "设备铭牌"),
    ("设备", DocumentBusinessType.equipment_info, "设备资料"),
    ("诉讼及仲裁情况", DocumentBusinessType.other, "诉讼仲裁情况"),
]


def classify_by_text_signal(
    text: str,
) -> tuple[Optional[DocumentBusinessType], Optional[str]]:
    """从任意文本信号中提取业务类型。

    与文件名规则共用同一组关键词，供 PDF 标题、目录条目等场景复用。
    """
    if not text:
        return None, None

    signal = text.lower()
    for keyword, biz_type, detail_label in FILENAME_RULES:
        if keyword.lower() in signal:
            return biz_type, detail_label
    return None, None


def classify_by_filename(
    filename: str,
) -> tuple[Optional[DocumentBusinessType], Optional[str]]:
    """从文件名提取业务类型

    Args:
        filename: 原始文件名(含扩展名)

    Returns:
        (业务大类, 细分标签) -- 无法识别时均为 None
    """
    if not filename:
        return None, None

    stem = Path(filename).stem
    return classify_by_text_signal(stem)
