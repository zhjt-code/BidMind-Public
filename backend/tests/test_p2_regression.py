"""P2 回归测试: 结构化字段白名单"""
import sys

sys.path.insert(0, ".")

from app.core.writer.section_composer import SectionComposer


def test_autofill_whitelist():
    print("=== P2 autofill whitelist regression ===")
    sc = SectionComposer

    tests = [
        ("license OK for 营业执照", sc._is_autofill_compatible("license", "营业执照副本")),
        ("personnel_cert OK for 法定代表人", sc._is_autofill_compatible("personnel_cert", "法定代表人身份证明")),
        ("license BLOCKED for 法定代表人身份证明", not sc._is_autofill_compatible("license", "法定代表人身份证明")),
        ("personnel_cert BLOCKED for 营业执照", not sc._is_autofill_compatible("personnel_cert", "营业执照")),
        ("qualification OK for 资质证明", sc._is_autofill_compatible("qualification", "资质证明")),
        ("other type always compatible", sc._is_autofill_compatible("other", "任意章节")),
        ("nameplate OK for 设备清单", sc._is_autofill_compatible("nameplate", "设备清单")),
        ("nameplate BLOCKED for 公司简介", not sc._is_autofill_compatible("nameplate", "公司简介")),
    ]

    passed = 0
    for name, ok in tests:
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        print(f"  [{status}] {name}")

    print(f"  Result: {passed}/{len(tests)} passed")
    if passed == len(tests):
        print("P2 REGRESSION: ALL PASSED")
    else:
        print("P2 REGRESSION: HAS FAILURES")
        sys.exit(1)


if __name__ == "__main__":
    test_autofill_whitelist()
