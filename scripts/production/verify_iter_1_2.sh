#!/bin/bash
set -euo pipefail

# BidMind AI Lite - 迭代 1-2 生产环境综合验证脚本
# 用法: bash verify_iter_1_2.sh [HOST] [CONTAINER]
# 示例: bash verify_iter_1_2.sh http://server.example.invalid:8000 bidmind-backend

HOST=${1:-http://localhost:8000}
CONTAINER=${2:-bidmind-backend}
PASS=0
FAIL=0

check() {
    local label="$1"
    shift
    echo ""
    echo "=== $label ==="
    if "$@"; then
        echo "[PASS] $label"
        PASS=$((PASS + 1))
    else
        echo "[FAIL] $label"
        FAIL=$((FAIL + 1))
    fi
}

# ---- 1/7 健康检查 /health ----
check "[1/7] /health (DB + 服务)" bash -c "
    resp=\$(curl -sf $HOST/health)
    echo \"\$resp\" | python3 -m json.tool 2>/dev/null || echo \"\$resp\"
    echo \"\$resp\" | grep -q '\"healthy\"'
"

# ---- 2/7 存活探针 /health/live ----
check "[2/7] /health/live (存活探针)" bash -c "
    resp=\$(curl -sf $HOST/health/live)
    echo \"\$resp\" | python3 -m json.tool 2>/dev/null || echo \"\$resp\"
    echo \"\$resp\" | grep -q '\"alive\"'
"

# ---- 3/7 就绪探针 /health/ready ----
check "[3/7] /health/ready (就绪探针)" bash -c "
    resp=\$(curl -sf $HOST/health/ready)
    echo \"\$resp\" | python3 -m json.tool 2>/dev/null || echo \"\$resp\"
    echo \"\$resp\" | grep -q '\"ready\"'
"

# ---- 4/7 Prometheus /metrics 可达且有足够指标 ----
check "[4/7] /metrics (Prometheus 指标端点)" bash -c "
    lines=\$(curl -sf $HOST/metrics | wc -l)
    echo \"  /metrics 返回 \$lines 行\"
    hits=\$(curl -sf $HOST/metrics | grep -cE '^(guard_invocation_total|gate_verdict_total|failure_state_distribution_total|cove_outcome_total|judge_outcome_total|writer_gate_reject_total|llm_calls_total|llm_tokens_total)' || true)
    echo \"  核心指标命中行数: \$hits\"
    [ \"\$lines\" -gt 50 ] && [ \"\$hits\" -ge 5 ]
"

# ---- 5/7 Alembic migration 到头 ----
check "[5/7] alembic current (Migration 到头)" bash -c "
    result=\$(docker exec $CONTAINER alembic current 2>&1)
    echo \"\$result\"
    echo \"\$result\" | grep -qi 'head'
"

# ---- 6/7 B5 Counter 累计（有流量时应非零） ----
check "[6/7] B5 Counter (CoVe/Judge/WriterGate)" bash -c "
    echo '  B5 新增指标当前值:'
    curl -sf $HOST/metrics | grep -E '^(cove_outcome_total|judge_outcome_total|writer_gate_reject_total|material_source_trace_outcome_total|judge_inconclusive_total)' | head -15
    echo '  (若全为 0 且有真实流量，请排查框架接线)'
    true
"

# ---- 7/7 关键环境变量无占位符 ----
check "[7/7] 环境变量安全检查 (无占位符)" bash -c "
    docker exec $CONTAINER python -c \"
from app.core.config import settings
errors = []
if settings.ADMIN_PASSWORD == '<SET_ADMIN_PASSWORD>':
    errors.append('ADMIN_PASSWORD is placeholder')
if 'change-in-production' in (settings.JWT_SECRET_KEY or ''):
    errors.append('JWT_SECRET_KEY is placeholder')
if not settings.METRICS_ENABLED:
    errors.append('METRICS_ENABLED is False')
if settings.LOG_LEVEL.upper() == 'DEBUG':
    errors.append('LOG_LEVEL is DEBUG (should be INFO in production)')
if errors:
    for e in errors:
        print(f'  [ERROR] {e}')
    raise SystemExit(1)
print('  All environment checks passed')
\"
"

# ---- 汇总 ----
echo ""
echo "=========================================="
echo "  验证完成: $PASS 通过 / $FAIL 失败 (共 $((PASS + FAIL)))"
echo "=========================================="

if [ "$FAIL" -gt 0 ]; then
    echo "  存在失败项，请按 docs/production_iter_verification.md 对应章节排查"
    exit 1
else
    echo "  全部通过，可进入迭代 3-4 线上实测"
    exit 0
fi
