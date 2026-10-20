"""公开仓库数据边界检查。

检查常见密钥模式，以及评分/规则语料中不应出现的联系方式和本机绝对路径。
它是发布前护栏，不替代人工的数据来源与授权审查。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".css", ".html", ".ini", ".js", ".json", ".jsonl", ".md", ".mjs",
    ".py", ".sql", ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml",
}
PUBLIC_CORPUS_PREFIXES = (
    "backend/tests/fixtures/scoring/",
    "backend/tests/fixtures/relevance/",
    "backend/tests/baselines/scoring/",
)

SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b"),
    "provider API key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
}
SENSITIVE_PATTERNS = {
    "phone number": re.compile(
        r"(?<!\d)(?:1[3-9]\d{9}|0\d{2,3}[- ]\d{7,8}|(?:400|800)\d{7})(?!\d)"
    ),
    "PRC identity number": re.compile(
        r"(?<![0-9A-Z])\d{6}(?:18|19|20)\d{2}"
        r"(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?![0-9A-Z])"
    ),
    "unified social credit code": re.compile(
        r"(?<![0-9A-Z])9[0-9A-Z]{17}(?![0-9A-Z])"
    ),
    "bank account number": re.compile(
        r"(?:银行账?号|账户)\s*[：:]?\s*(?!<BANK_ACCOUNT_)"
        r"(?:\d[ -]?){12,30}\b"
    ),
    "specific person name": re.compile(
        r"(?:联系人|姓名|法定代表人|项目负责人|授权代表)\s*[：:]\s*"
        r"(?!<PERSON_|匿名|待补充|无)[\u4e00-\u9fff]{2,4}(?=[ \t]*(?:$|[，,；;\"']))",
        re.MULTILINE,
    ),
    "specific organization name": re.compile(
        r"(?:公司名称|单位名称|机构名称|招标人|投标人|采购人)\s*[：:]\s*"
        r"(?!<ORG_|匿名|某单位)[\u4e00-\u9fff]{2,40}?"
        r"(?:有限责任公司|股份有限公司|有限公司|集团|研究院|大学|银行|委员会|政府|管理局)"
    ),
    "specific person fixture value": re.compile(
        r"(?:legal_rep|legal_representative|contact_name|person_name|representative)"
        r"\s*(?:=|:)\s*[\"'](?!<PERSON_)[\u4e00-\u9fff]{2,4}[\"']"
    ),
    "specific organization fixture value": re.compile(
        r"(?:company_name|bidder_name|supplier_name)"
        r"\s*(?:=|:)\s*[\"'](?!<ORG_)[^\"'\n]{2,80}[\"']"
    ),
    "specific author name": re.compile(
        r'"author"\s*:\s*"(?!<PERSON_|匿名|其他人)[\u4e00-\u9fff]{2,4}"'
    ),
    "email address": re.compile(
        r"(?i)(?<![A-Z0-9._%+*\-])"
        r"(?![A-Z0-9._%+-]+@example\.(?:com|invalid)\b)"
        r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
    ),
    "detailed address": re.compile(
        r"(?:联系|办公|注册地址|住所)?地\s*址\s*[：:]\s*"
        r"(?!<ADDRESS_|<redacted>|无\b|电子邮箱)[\u4e00-\u9fff][^\n\r]{3,}"
    ),
    # In JSON, a real backslash is encoded as ``\\``. Requiring that form
    # avoids flagging OCR fragments such as ``C:\\n`` as local paths.
    "Windows absolute path": re.compile(r"(?i)(?<![A-Za-z])[A-Z]:(?:\\\\|/)"),
    "server IP address": re.compile(
        r"(?i)(?:https?://|(?:server|host|ip|服务器|主机)\s*[=:：]\s*)"
        r"(?!127\.0\.0\.1\b)(?!0\.0\.0\.0\b)"
        r"(?:25[0-5]|2[0-4]\d|1?\d?\d)"
        r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])"
    ),
}

SCORING_METADATA_PATTERNS = {
    "original source filename": re.compile(
        r'["\']source["\']\s*:\s*["\'][^"\']+\.(?:pdf|docx?|xlsx?|pptx?)["\']',
        re.IGNORECASE,
    ),
    "original file hash": re.compile(
        r'["\']sha256["\']\s*:\s*["\'][0-9a-f]{64}["\']',
        re.IGNORECASE,
    ),
}
COLLUSION_FIXTURE_PATTERNS = {
    "specific person name": re.compile(
        r'"(?:legal_representative|author|contact_name|name)"\s*:\s*"'
        r'(?!<PERSON_)[\u4e00-\u9fff]{2,4}"'
    ),
    "specific organization name": re.compile(
        r'"(?:company_name|bidder_name|supplier_name)"\s*:\s*"'
        r'(?!<ORG_)[^"\n]{2,80}"'
    ),
}


@dataclass(frozen=True)
class Finding:
    relative: str
    label: str


def scan_file(path: Path, relative: str, *, text: str | None = None) -> list[Finding]:
    if text is None:
        text = path.read_text(encoding="utf-8", errors="replace")

    findings: list[Finding] = []
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            findings.append(Finding(relative, label))
    for label, pattern in SENSITIVE_PATTERNS.items():
        if label == "Windows absolute path" and not relative.startswith(PUBLIC_CORPUS_PREFIXES):
            continue
        if pattern.search(text):
            findings.append(Finding(relative, label))
    if relative.startswith("backend/tests/fixtures/scoring/"):
        for label, pattern in SCORING_METADATA_PATTERNS.items():
            if pattern.search(text):
                findings.append(Finding(relative, label))
    if relative.startswith("backend/tests/fixtures/collusion_rules/"):
        for label, pattern in COLLUSION_FIXTURE_PATTERNS.items():
            if pattern.search(text):
                findings.append(Finding(relative, label))
    return findings


def candidate_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def main() -> int:
    findings: list[str] = []
    for relative in candidate_files():
        path = ROOT / relative
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file():
            continue
        for finding in scan_file(path, relative):
            findings.append(f"{finding.relative}: contains {finding.label}")

    if findings:
        print("Public-data check failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("Public-data check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

