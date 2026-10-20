import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.scoring


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build_scoring_sample_corpus.py"
spec = importlib.util.spec_from_file_location("build_scoring_sample_corpus", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_parse_index_spec_supports_ranges_and_singletons() -> None:
    assert builder.parse_index_spec("1-3,27,28") == [1, 2, 3, 27, 28]


def test_parse_index_spec_rejects_empty_result() -> None:
    try:
        builder.parse_index_spec(" ")
    except ValueError as exc:
        assert "indexes" in str(exc)
    else:
        raise AssertionError("empty index spec should fail")


def test_build_targets_can_address_holdout_directory_without_merged() -> None:
    samples_dir = Path("backend/storage/samples/scoring_corpus/holdout")

    targets = builder.build_targets(
        samples_dir=samples_dir,
        indexes=[23, 24],
        name_template="structure-{index:02d}.pdf",
        merged_name="评分表结构-合并.pdf",
        include_merged=False,
        extras=[],
    )

    assert targets == [
        ("23", samples_dir / "structure-23.pdf"),
        ("24", samples_dir / "structure-24.pdf"),
    ]
