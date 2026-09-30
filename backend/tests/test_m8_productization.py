import sys

from evaluation.m8_productization.public_safety import scan_paths
from evaluation.m8_productization.validate import _public_command


def test_public_safety_rejects_private_docs_and_secret_material(tmp_path):
    private = tmp_path / "docs" / "resume.md"
    private.parent.mkdir()
    private.write_text("private", encoding="utf-8")
    leaked = tmp_path / "config.txt"
    leaked.write_text("token=" + "sk-" + "abcdefghijklmnopqrstuvwxyz123456", encoding="utf-8")

    findings = scan_paths(tmp_path, [private, leaked])

    assert {item.rule for item in findings} == {
        "private_docs_tracked",
        "openai_style_key",
    }


def test_public_safety_rejects_legacy_product_reference(tmp_path):
    source = tmp_path / "README.md"
    source.write_text("Built from " + "Echo" + "Mind", encoding="utf-8")

    findings = scan_paths(tmp_path, [source])

    assert [item.rule for item in findings] == ["legacy_product_reference"]


def test_public_safety_accepts_examples_without_credentials(tmp_path):
    example = tmp_path / ".env.example"
    example.write_text("OPENAI_API_KEY=your_api_key\n", encoding="utf-8")

    assert scan_paths(tmp_path, [example]) == []


def test_validation_evidence_hides_local_python_and_workspace_paths(tmp_path):
    command = _public_command(
        [sys.executable, "--root", str(tmp_path)],
        tmp_path,
    )

    assert command == ["python", "--root", "."]
