"""Writer (标书编写) Gate 集合 — B5.1-B5.4 迁移 + S.5.2 新增。"""

from app.core.ai.gates.writer.disqualification_risk import DisqualificationRiskGate
from app.core.ai.gates.writer.fixed_template_placeholder import (
    FixedTemplatePlaceholderGate,
)
from app.core.ai.gates.writer.required_section_completeness import (
    RequiredSectionCompletenessGate,
)
from app.core.ai.gates.writer.cross_reference import CrossReferenceGate
from app.core.ai.gates.writer.terminology_consistency import (
    TerminologyConsistencyGate,
)
from app.core.ai.gates.writer.material_source_trace import MaterialSourceTraceGate
from app.core.ai.gates.writer.placeholder_density import PlaceholderDensityGate
from app.core.ai.gates.writer.de_ai_filter import DeAIFilterGate
from app.core.ai.gates.writer.table_field_fill_rate import TableFieldFillRateGate

__all__ = [
    "DisqualificationRiskGate",
    "FixedTemplatePlaceholderGate",
    "RequiredSectionCompletenessGate",
    "CrossReferenceGate",
    "TerminologyConsistencyGate",
    "MaterialSourceTraceGate",
    "PlaceholderDensityGate",
    "DeAIFilterGate",
    "TableFieldFillRateGate",
]
