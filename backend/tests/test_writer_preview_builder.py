from types import SimpleNamespace

from app.core.writer.doc_renderer import HtmlPreviewBuilder
from app.core.writer.section_structure import parse_section_blocks


def test_render_section_blocks_keeps_preview_numbering_in_sync() -> None:
    builder = HtmlPreviewBuilder(db=None)
    content = """
    <h4>服务目标</h4>
    <ol>
      <li>一级事项A
        <ol><li>子项A-1</li></ol>
      </li>
      <li>一级事项B
        <ol><li>子项B-1</li></ol>
      </li>
    </ol>
    """

    html = builder._render_section_blocks(
        parse_section_blocks(content),
        base_depth=1,
    )

    assert "（一）服务目标" in html
    assert "1、一级事项A" in html
    assert "（1）子项A-1" in html
    assert "2、一级事项B" in html
    assert html.count("（1）子项B-1") == 1


def test_render_body_uses_section_number_and_normalized_blocks() -> None:
    builder = HtmlPreviewBuilder(db=None)
    node = SimpleNamespace(id=1, depth=1, title="项目方案")
    draft = SimpleNamespace(content="<h4>实施安排</h4><p>正文内容</p>")

    html = builder._render_body([node], {1: draft}, {1: "一、"})

    assert "<h2>一、项目方案</h2>" in html
    assert "（一）实施安排" in html
    assert "<p>正文内容</p>" in html


def test_generate_css_aligns_h5_with_word_spec() -> None:
    css = HtmlPreviewBuilder._generate_css({})

    assert "font-family: '宋体', 'SimSun', serif;" in css
    assert "font-size: 11.5pt" in css


def test_render_section_blocks_strips_residual_double_number_text() -> None:
    builder = HtmlPreviewBuilder(db=None)
    content = """
    <ol>
      <li>1 投标函</li>
      <li>2 法定代表人身份证明及授权委托书</li>
    </ol>
    <p>3</p>
    """

    html = builder._render_section_blocks(parse_section_blocks(content), base_depth=1)

    assert "（一）1 投标函" not in html
    assert "（二）2 法定代表人身份证明及授权委托书" not in html
    assert "3、 2" not in html
    assert "（一）投标函" in html
    assert "（二）法定代表人身份证明及授权委托书" in html


def test_render_section_blocks_merges_pure_number_heading_with_following_paragraph() -> None:
    builder = HtmlPreviewBuilder(db=None)
    content = """
    <h4>（一）</h4>
    <p>我方具备完整的项目执行能力。</p>
    <h4>（二）</h4>
    <p>我方具备成熟的质量保障体系。</p>
    """

    from app.core.writer.doc_renderer import DocRenderer
    blocks = DocRenderer._merge_bare_numbering_with_following_paragraph(
        parse_section_blocks(content),
    )
    html = builder._render_section_blocks(blocks, base_depth=1)

    assert "（一）（一）" not in html
    assert "（二）（二）" not in html
    assert ">（一）<" not in html, "bare (一) should be merged"
    assert ">（二）<" not in html, "bare (二) should be merged"
    assert "我方具备完整的项目执行能力" in html
    assert "我方具备成熟的质量保障体系" in html


def test_render_section_blocks_drops_template_symbol_and_punctuation_residue() -> None:
    builder = HtmlPreviewBuilder(db=None)
    content = """
    <p>¥</p>
    <p>） 我方将按招标文件要求提供资料。</p>
    <p>（一）.</p>
    <p>（三）. 60</p>
    """

    html = builder._render_section_blocks(parse_section_blocks(content), base_depth=1)

    assert "¥" not in html
    assert "） 我方将按招标文件要求提供资料。" not in html
    assert "我方将按招标文件要求提供资料。" in html
    assert "（一）." not in html
    assert "（三）. 60" not in html


def test_render_section_blocks_prefers_context_depth_when_visible_numbering_overshoots() -> None:
    builder = HtmlPreviewBuilder(db=None)
    content = "<h4>（1）核心资质与专业认证</h4>"

    html = builder._render_section_blocks(parse_section_blocks(content), base_depth=2)

    assert "1、核心资质与专业认证" in html
    assert "（1）核心资质与专业认证" not in html


def test_render_section_blocks_collapse_first_h5_heading_that_skips_a_level() -> None:
    builder = HtmlPreviewBuilder(db=None)
    content = "<h5>（1）核心资质与专业认证</h5>"

    html = builder._render_section_blocks(parse_section_blocks(content), base_depth=2)

    assert "1、核心资质与专业认证" in html
    assert "（1）核心资质与专业认证" not in html


def test_render_section_blocks_keep_arabic_children_under_parenthesized_parent() -> None:
    builder = HtmlPreviewBuilder(db=None)
    content = """
    <h4>（九）承诺及声明</h4>
    <p>1、我方将按招标文件要求履行投标责任。</p>
    <p>2、本投标自开标日起有效期为60个日历日。</p>
    """

    html = builder._render_section_blocks(parse_section_blocks(content), base_depth=1)

    assert "（一）承诺及声明" in html
    assert "1、我方将按招标文件要求履行投标责任。" in html
    assert "2、本投标自开标日起有效期为60个日历日。" in html
    assert "（十）我方将按招标文件要求履行投标责任。" not in html


def test_render_body_removes_duplicate_plain_text_fixed_template_title() -> None:
    builder = HtmlPreviewBuilder(db=None)
    node = SimpleNamespace(id=1, depth=1, title="投标函")
    draft = SimpleNamespace(content="（一）投标函\n（一）法定代表人身份证明及授权委托书")

    html = builder._render_body([node], {1: draft}, {1: "一、"})

    assert "（一）投标函" not in html
    assert "（一）法定代表人身份证明及授权委托书" in html


def test_render_section_blocks_merges_bare_numbering_with_following_paragraph() -> None:
    builder = HtmlPreviewBuilder(db=None)
    content = """
    <h4>（三）</h4>
    <p>我公司自成立以来，始终专注于音视频系统集成领域。</p>
    <h4>（四）项目团队</h4>
    <p>为确保本项目高标准高质量完成。</p>
    """

    from app.core.writer.doc_renderer import DocRenderer
    blocks = DocRenderer._merge_bare_numbering_with_following_paragraph(
        parse_section_blocks(content),
    )
    html = builder._render_section_blocks(blocks, base_depth=1)

    assert ">（三）<" not in html
    assert "我公司自成立以来" in html
    assert "项目团队" in html


def test_render_body_inlines_node_images(tmp_path) -> None:
    """章节绑定图片应在预览中渲染为 base64 内联 <img>(端到端)"""
    from app.models.writing_material_image import ImageType

    img_file = tmp_path / "lic.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n-license-bytes")
    rec = SimpleNamespace(
        id=11,
        image_path=str(img_file),
        caption="图1: 营业执照",
        image_type=ImageType.license,
        business_subtype="business_license",
        width=0,
        height=0,
        file_hash="lic-11",
    )

    builder = HtmlPreviewBuilder(db=None)
    node = SimpleNamespace(id=1, depth=1, title="营业执照")
    draft = SimpleNamespace(
        content=(
            "<p>我方持有有效营业执照。</p>"
            '<figure data-image-id="11"><figcaption>图1: 营业执照</figcaption></figure>'
        )
    )
    node_image_map = {1: {11: rec}}

    html = builder._render_body([node], {1: draft}, {1: "一、"}, node_image_map)

    assert "data:image/png;base64," in html
    assert "[图片占位]" not in html
    assert "图1: 营业执照" in html


def test_render_body_without_image_map_degrades_to_placeholder_text() -> None:
    """无图片映射时降级为占位文本, 不报错(健壮性)"""
    builder = HtmlPreviewBuilder(db=None)
    node = SimpleNamespace(id=1, depth=1, title="营业执照")
    draft = SimpleNamespace(
        content='<figure data-image-id="11"><figcaption>图1</figcaption></figure>'
    )

    html = builder._render_body([node], {1: draft}, {1: "一、"})

    assert "[图片占位]" in html
    assert "data:image" not in html
