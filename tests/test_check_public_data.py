import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts import check_public_data


class CheckPublicDataTests(unittest.TestCase):
    def labels(self, name: str, content: str) -> set[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return {
                finding.label
                for finding in check_public_data.scan_file(path, name)
            }

    def test_rejects_original_source_metadata(self) -> None:
        labels = self.labels(
            "backend/tests/fixtures/scoring/sample.json",
            '{"source": "' + "customer" + ".pdf\", \"sha256\": \"" + "a" * 64 + '"}',
        )

        self.assertEqual(labels, {"original source filename", "original file hash"})

    def test_rejects_identity_and_contact_values(self) -> None:
        content = "\n".join(
            (
                "联系人：" + "张" + "三",
                "公司名称：" + "".join(("某某建设", "有限公司")),
                "联系电话：" + "138" + "0013" + "8000",
                "身份证号：" + "110105" + "19491231" + "002X",
                "统一社会信用代码：" + "91110000" + "XXXXXXXXX1",
                "银行账号：" + "62284800" + "00000000",
            )
        )

        labels = self.labels("fixture.txt", content)

        self.assertEqual(
            labels,
            {
                "specific person name",
                "specific organization name",
                "phone number",
                "PRC identity number",
                "unified social credit code",
                "bank account number",
            },
        )

    def test_allows_explicit_anonymous_placeholders(self) -> None:
        content = "\n".join(
            (
                "联系人：<PERSON_A>",
                "公司名称：<ORG_A>",
                "联系电话：<PHONE_A>",
                "身份证号：<IDENTITY_A>",
                "统一社会信用代码：<CREDIT_CODE_A>",
                "银行账号：<BANK_ACCOUNT_A>",
                '"source": "fixture-scoring-a"',
            )
        )

        self.assertEqual(
            check_public_data.scan_file(
                Path("unused"), "fixture.txt", text=content
            ),
            [],
        )

    def test_rejects_unlabeled_fixture_identity_values(self) -> None:
        content = "\n".join(
            (
                'legal_rep="' + "张" + '三"',
                'company_name="' + "某某建设" + '有限公司"',
                '"author": "' + "李" + '四"',
            )
        )

        labels = self.labels("backend/tests/sample.py", content)

        self.assertEqual(
            labels,
            {
                "specific person fixture value",
                "specific organization fixture value",
                "specific author name",
            },
        )

    def test_main_scans_env_examples_and_rejects_private_file_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env.example").write_text("API_KEY=sk-" + "x" * 25, encoding="utf-8")
            (root / ".env.production").write_text("", encoding="utf-8")
            (root / "certificate.key").write_text("", encoding="utf-8")
            with patch.object(check_public_data, "ROOT", root), patch.object(
                check_public_data,
                "candidate_files",
                return_value=[".env.example", ".env.production", "certificate.key"],
            ), redirect_stdout(StringIO()) as output:
                result = check_public_data.main()

        self.assertEqual(result, 1)
        self.assertIn(".env.example: contains provider API key", output.getvalue())
        self.assertIn(".env.production: forbidden sensitive file", output.getvalue())
        self.assertIn("certificate.key: forbidden sensitive file", output.getvalue())


if __name__ == "__main__":
    unittest.main()
