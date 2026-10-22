# AI 评测体系

## 目录结构

```text
eval/
  golden_datasets/               -- 黄金测试集（人工标注数据）
    sample_01.json               -- 招标提取示例
    collusion_extract_sample.json
    collusion_pair_sample.json
    collusion_project_sample.json
  run_eval.py                    -- 评测脚本入口
  README.md                      -- 本文件
```

## 黄金测试集格式

每个 JSON 文件代表一个测试用例。当前支持 4 类 purpose：

- `tender_extract`
- `collusion_extract`
- `collusion_pair`
- `collusion_project`

### 1. tender_extract

```json
{
  "id": "sample_01",
  "description": "测试用例描述",
  "purpose": "tender_extract",
  "input": {
    "chunks": ["招标文件文本片段1...", "招标文件文本片段2..."]
  },
  "expected_output": {
    "requirements": [
      {
        "category": "QUAL",
        "description": "投标人须具有...",
        "is_mandatory": true
      }
    ]
  },
  "metadata": {
    "source": "某市政项目招标文件",
    "annotator": "human",
    "date": "2026-03-18"
  }
}
```

### 2. collusion_extract

用于评测串标 S1 结构化提取质量。建议以“关键字段 token”方式标注，先覆盖公司、人员、证号、报价、业绩等高价值字段。

```json
{
  "id": "collusion_extract_sample",
  "purpose": "collusion_extract",
  "expected_output": {
    "collusion_extract": {
      "documents": [
        {
          "document_id": "bid_a",
          "company_name": "<ORG_23>",
          "legal_representative": "<PERSON_1>",
          "contact_phone": "<PHONE_0001>",
          "registered_address": "南山区科技园1号楼801",
          "unified_social_credit_code": "<CREDIT_CODE_4567>",
          "total_price": "1020000元",
          "person_names": ["<PERSON_1>", "<PERSON_2>"],
          "certificate_numbers": ["粤建安B(2020)0001"],
          "past_projects": ["某市政道路改造工程"]
        }
      ]
    }
  },
  "actual_output": {
    "collusion_extract": {
      "documents": []
    }
  }
}
```

### 3. collusion_pair

用于评测单对文件的风险判定、决策状态和命中维度。

```json
{
  "id": "collusion_pair_sample",
  "purpose": "collusion_pair",
  "expected_output": {
    "collusion_pair": {
      "pairs": [
        {
          "pair_id": "101:102",
          "overall_risk": "high",
          "risk_level": "high",
          "decision_state": "decidable",
          "review_state": "completed",
          "hit_dimensions": ["D1", "D4"]
        }
      ]
    }
  },
  "actual_output": {
    "collusion_pair": {
      "pairs": []
    }
  }
}
```

### 4. collusion_project

用于评测项目级业务结论是否与黄金标注一致。

```json
{
  "id": "collusion_project_sample",
  "purpose": "collusion_project",
  "expected_output": {
    "collusion_project": {
      "project_decision": "suspicious_found",
      "project_risk_level": "high",
      "overall_risk": "high",
      "review_required": true,
      "suspected_groups": [
        {
          "document_ids": [101, 102],
          "risk_level": "high"
        }
      ]
    }
  },
  "actual_output": {
    "collusion_project": {}
  }
}
```

## 运行评测

```bash
cd backend
python -m eval.run_eval --dataset eval/golden_datasets/ --purpose tender_extract
python -m eval.run_eval --dataset eval/golden_datasets/ --purpose collusion_extract
python -m eval.run_eval --dataset eval/golden_datasets/ --purpose collusion_pair
python -m eval.run_eval --dataset eval/golden_datasets/ --purpose collusion_project
```

## 输出指标

- Precision（准确率）: 正确提取的条目 / AI 提取的总条目
- Recall（召回率）: 正确提取的条目 / 标注的总条目
- F1: 2 * P * R / (P + R)

## 设计约束

- 第一阶段评测全部采用离线 `expected_output` vs `actual_output` 对比，不依赖在线任务调度或数据库状态。
- `collusion_extract` / `collusion_pair` / `collusion_project` 先以“弱归一化 token 对齐”做基线，目标是形成稳定回归门禁，而不是一开始就追求复杂评测器。
- 后续如串标输出 schema 演进，应优先扩展 token flatten 规则，而不是让黄金样本强耦合运行时内部结构。
