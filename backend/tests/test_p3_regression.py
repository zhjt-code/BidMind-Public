"""P3 回归测试: 渲染器收口"""
import sys
sys.path.insert(0, ".")


def test_renderer_no_global_grab():
    """验证 _replace_all_image_placeholders 不再做全局补位"""
    print("=== P3 renderer regression ===")
    import inspect
    from app.core.writer.doc_renderer import DocRenderer

    source = inspect.getsource(DocRenderer._replace_all_image_placeholders)

    ok1 = "find_best_image_for_placeholder" not in source
    print(f"  [{'PASS' if ok1 else 'FAIL'}] no find_best_image_for_placeholder call (no global grab)")

    ok2 = "remaining.pop" not in source
    print(f"  [{'PASS' if ok2 else 'FAIL'}] no remaining.pop (no image consumption)")

    ok3 = "add_picture" not in source
    print(f"  [{'PASS' if ok3 else 'FAIL'}] no add_picture (no image insertion)")

    ok4 = "图片待人工确认" in source
    print(f"  [{'PASS' if ok4 else 'FAIL'}] marks as 图片待人工确认")

    lines_without_docstring = [
        ln for ln in source.splitlines()
        if not ln.strip().startswith(('"""', '#', "'''"))
        and '"""' not in ln
    ]
    code_only = "\n".join(lines_without_docstring)
    ok5 = "_insert_figure_images(" not in code_only
    print(f"  [{'PASS' if ok5 else 'FAIL'}] does not call _insert_figure_images in code body")

    all_ok = ok1 and ok2 and ok3 and ok4 and ok5
    print(f"  Result: {'ALL PASSED' if all_ok else 'HAS FAILURES'}")

    # verify _insert_figure_images still exists for precise ID-based insertion
    ok6 = hasattr(DocRenderer, '_insert_figure_images')
    print(f"  [{'PASS' if ok6 else 'FAIL'}] _insert_figure_images still exists for precise insertion")

    # verify _verify_render_output checks for pending markers
    verify_source = inspect.getsource(DocRenderer._verify_render_output)
    ok7 = "图片待人工确认" in verify_source
    print(f"  [{'PASS' if ok7 else 'FAIL'}] quality check detects 图片待人工确认 markers")

    final_ok = all_ok and ok6 and ok7
    print(f"\nP3 REGRESSION: {'ALL PASSED' if final_ok else 'HAS FAILURES'}")
    assert final_ok


if __name__ == "__main__":
    try:
        test_renderer_no_global_grab()
        sys.exit(0)
    except AssertionError:
        sys.exit(1)
