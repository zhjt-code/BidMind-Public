"""防御测试: 串标进度 step3-5 在全 algorithm_only/skipped 场景下永久 0%

锚定根因:
- collusion_status_service.compute_step_progresses 公式必须保证:
 1. 全 skipped / 全 algorithm_only / 混合无 LLM 路径时, step3/4/5 在项目终态下 = 100
 2. 项目运行中且全 LLM 路径时, step3 按 llm_compared / needs_llm 真实反映
 3. project.collusion_summary 写入即视为 S4 完成, 不再依赖 step3_pct 严格 == 100

回归保护:
- 投标审查或写标修改进度公式时, 防止误改本函数
- 群体研判心跳 current 取值漂移, 不能在终态后再被前端误显示
"""

from __future__ import annotations

from app.models.project import ProjectStatus
from app.services.collusion_status_service import compute_step_progresses


def _call(**overrides) -> dict:
    """compute_step_progresses 测试快捷调用, 默认参数为典型 3 文件场景"""
    base = dict(
        total_docs=4,         # 1 tender + 3 bid
        extracted_count=4,
        total_pairs=3,        # C(3, 2) = 3
        compared_count=0,
        llm_compared_count=0,
        algorithm_only_count=0,
        skipped_count=0,
        group_count=0,
        project_status=ProjectStatus.REVIEWING,
        synthesis_saved=False,
    )
    base.update(overrides)
    return compute_step_progresses(**base)


class TestBug390StepProgressFormula:
    """ 核心修复: 全 algorithm_only / 全 skipped 不再把 step3-5 钉在 0%"""

    def test_all_algorithm_only_project_completed_steps_all_100(self):
        """全 algorithm_only + 项目已完成: step3/4/5 必须都到 100%"""
        result = _call(
            compared_count=3,
            llm_compared_count=0,
            algorithm_only_count=3,
            skipped_count=0,
            group_count=0,
            project_status=ProjectStatus.COMPLETED,
            synthesis_saved=True,
        )
        assert result["step1_pct"] == 100
        assert result["step2_pct"] == 100
        assert result["step3_pct"] == 100, "全 algorithm_only 不应让 step3 永久 0%"
        assert result["step4_pct"] == 100, "synthesis_done 后 step4 必须完成"
        assert result["step5_pct"] == 100, "synthesis_done 后 step5 必须完成"
        assert result["step6_pct"] == 100
        assert result["total_llm_pairs"] == 0

    def test_all_skipped_project_completed_steps_all_100(self):
        """全 skipped + 项目已完成: 同样要求 step3/4/5 = 100%"""
        result = _call(
            compared_count=3,
            llm_compared_count=0,
            algorithm_only_count=0,
            skipped_count=3,
            group_count=0,
            project_status=ProjectStatus.COMPLETED,
            synthesis_saved=True,
        )
        assert result["step3_pct"] == 100
        assert result["step4_pct"] == 100
        assert result["step5_pct"] == 100
        assert result["total_llm_pairs"] == 0

    def test_mixed_algorithm_and_skipped_completed_steps_all_100(self):
        """混合 algorithm_only + skipped (无 LLM 工作) + 完成: step3-5 都 100%"""
        result = _call(
            compared_count=3,
            llm_compared_count=0,
            algorithm_only_count=2,
            skipped_count=1,
            group_count=0,
            project_status=ProjectStatus.COMPLETED,
            synthesis_saved=True,
        )
        assert result["step3_pct"] == 100
        assert result["step4_pct"] == 100
        assert result["step5_pct"] == 100
        assert result["total_llm_pairs"] == 0


class TestStep3FormulaMixedLLM:
    """混合场景 step3 仍按真实 LLM 完成度计算"""

    def test_partial_llm_during_running(self):
        """运行中: 3 对里 2 对需要 LLM, 已完成 1 对 -> step3 = 50%"""
        result = _call(
            compared_count=2,           # 1 LLM 完成 + 1 algorithm_only
            llm_compared_count=1,
            algorithm_only_count=1,
            skipped_count=0,
            project_status=ProjectStatus.REVIEWING,
            synthesis_saved=False,
        )
        assert result["total_llm_pairs"] == 2  # 3 - 0 - 1 = 2
        assert result["step3_pct"] == 50
        assert result["step4_pct"] == 0
        assert result["step5_pct"] == 0

    def test_all_llm_done_running_but_synthesis_pending(self):
        """运行中: LLM 全部跑完但综合研判未完成 -> step3=100, step4 待 group/synthesis"""
        result = _call(
            compared_count=3,
            llm_compared_count=3,
            algorithm_only_count=0,
            skipped_count=0,
            group_count=0,
            project_status=ProjectStatus.REVIEWING,
            synthesis_saved=False,
        )
        assert result["step3_pct"] == 100
        # step3=100 + compared=total + 无群组 + 综合未完成 -> step4 也能到 100
        # (业务上 S3 完成已经进入 S3.5)
        assert result["step4_pct"] == 100
        # 但 step5 仍待综合研判完成
        assert result["step5_pct"] == 50

    def test_synthesis_saved_forces_step3_complete(self):
        """边界: 综合研判已写入但 LLM 不全 (异常路径), step3 仍按 synthesis_done 收尾"""
        result = _call(
            compared_count=3,
            llm_compared_count=1,
            algorithm_only_count=0,
            skipped_count=0,
            project_status=ProjectStatus.REVIEWING,
            synthesis_saved=True,
        )
        assert result["step3_pct"] == 100, "synthesis_done 后 step3 不应继续显示 33%"
        assert result["step4_pct"] == 100
        assert result["step5_pct"] == 100


class TestStep4StepCompletionSignals:
    """step4 完成信号多源校验"""

    def test_group_identified_forces_step4(self):
        """识别到嫌疑群组: step4 立即完成"""
        result = _call(
            compared_count=3,
            llm_compared_count=3,
            group_count=2,
            project_status=ProjectStatus.REVIEWING,
        )
        assert result["step4_pct"] == 100

    def test_no_group_but_synthesis_done(self):
        """无群组但综合研判完成: step4 仍视为完成"""
        result = _call(
            compared_count=3,
            llm_compared_count=0,
            algorithm_only_count=3,
            group_count=0,
            project_status=ProjectStatus.COMPLETED,
            synthesis_saved=True,
        )
        assert result["step4_pct"] == 100


class TestProjectFailedTerminalState:
    """项目失败终态: 前序步骤同样应视为已走过, 而非永久等待"""

    def test_failed_terminal_steps_3_4_5_complete(self):
        """项目 FAILED 终态: step3-5 不应卡在 waiting"""
        result = _call(
            compared_count=3,
            llm_compared_count=0,
            algorithm_only_count=3,
            project_status=ProjectStatus.FAILED,
            synthesis_saved=False,  # 失败可能未写 summary
        )
        # FAILED 也算 terminal, synthesis_done=True
        assert result["step3_pct"] == 100
        assert result["step4_pct"] == 100
        assert result["step5_pct"] == 100
        # 但 step6 不能因为 FAILED 而到 100, 报告未生成
        assert result["step6_pct"] == 0


class TestPureRunningState:
    """纯运行态下边界保护: 不能因 synthesis_saved=False 反向把进度倒退"""

    def test_running_no_data_all_zero(self):
        """运行初期, 仅文档已提取: 后续步骤都是 0"""
        result = _call(
            extracted_count=2,
            total_docs=4,
            project_status=ProjectStatus.REVIEWING,
        )
        assert result["step1_pct"] == 50
        assert result["step2_pct"] == 0
        assert result["step3_pct"] == 0
        assert result["step4_pct"] == 0
        assert result["step5_pct"] == 0
        assert result["step6_pct"] == 0

    def test_running_step1_done_only(self):
        """step1 完成后 step2 应立即变 100% (纯算法)"""
        result = _call(
            extracted_count=4,
            total_docs=4,
            project_status=ProjectStatus.REVIEWING,
        )
        assert result["step1_pct"] == 100
        assert result["step2_pct"] == 100


class TestZeroPairsEdge:
    """单文档/无对边界: 不应除零异常, step3 应直接 100%"""

    def test_no_pairs_total_llm_pairs_zero(self):
        """total_pairs=0 (单文档场景, 业务不允许但守卫不应崩) step3=100"""
        result = _call(
            total_pairs=0,
            compared_count=0,
            project_status=ProjectStatus.REVIEWING,
        )
        assert result["step3_pct"] == 100
        assert result["total_llm_pairs"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 集成层面回归: 验证 overall_pct = mean(step1..step6) 在新公式下的合理范围
# ═══════════════════════════════════════════════════════════════════════════════


class TestOverallProgressIntegrity:
    """overall_pct = round(sum/6) 在 修复后应符合预期"""

    def test_overall_pct_completed_no_llm_work_is_100(self):
        """全 algorithm_only + 完成: overall_pct 应为 100, 不再是 50"""
        result = _call(
            compared_count=3,
            llm_compared_count=0,
            algorithm_only_count=3,
            project_status=ProjectStatus.COMPLETED,
            synthesis_saved=True,
        )
        steps = [result[f"step{i}_pct"] for i in range(1, 7)]
        overall = round(sum(steps) / len(steps))
        assert overall == 100, f"修复前 = 50, 修复后必须为 100, 实际 = {overall}"

    def test_overall_pct_completed_with_llm_is_100(self):
        """全 LLM + 完成: overall_pct = 100"""
        result = _call(
            compared_count=3,
            llm_compared_count=3,
            group_count=1,
            project_status=ProjectStatus.COMPLETED,
            synthesis_saved=True,
        )
        steps = [result[f"step{i}_pct"] for i in range(1, 7)]
        overall = round(sum(steps) / len(steps))
        assert overall == 100
