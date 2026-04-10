import tempfile
import unittest
from pathlib import Path

from scripts.verify import check_output_files


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DOC = REPO_ROOT / "SKILL.md"
README_DOC = REPO_ROOT / "README.md"
RUN_SCRIPT = REPO_ROOT / "run.py"


class PhaseFContractTest(unittest.TestCase):
    def test_skill_md_uses_new_phase_flow_and_skill_folder_contract(self):
        content = SKILL_DOC.read_text(encoding="utf-8")

        self.assertIn('name: 博主蒸馏器', content)
        self.assertIn("### Phase 0.5", content)
        self.assertIn("请选择分析模式", content)
        self.assertIn("创作指南.skill/", content)
        self.assertIn("SKILL.md", content)
        self.assertNotIn("generate_docs.py", content)
        self.assertNotIn("4 份文档", content)

    def test_run_py_switches_to_mode_driven_distill_flow(self):
        content = RUN_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("--mode", content)
        self.assertIn("Phase 0.5", content)
        self.assertIn("请选择分析模式", content)
        self.assertNotIn("generate_docs.py", content)

    def test_readme_sells_html_and_skill_folder_instead_of_word_docs(self):
        content = README_DOC.read_text(encoding="utf-8")

        self.assertIn("博主蒸馏器", content)
        self.assertIn("HTML 报告", content)
        self.assertIn("创作指南.skill/", content)
        self.assertIn("SKILL.md", content)
        self.assertNotIn("4 份 Word 文档", content)
        self.assertNotIn("Word (.docx)", content)

    def test_verify_supports_skill_folder_entry_file_check(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            skill_entry = output_dir / "灵均Kikky_创作指南.skill" / "SKILL.md"
            skill_entry.parent.mkdir(parents=True, exist_ok=True)
            skill_entry.write_text("# test", encoding="utf-8")

            ok, message = check_output_files(
                str(output_dir),
                ["灵均Kikky_创作指南.skill/SKILL.md"],
            )

            self.assertTrue(ok, message)


if __name__ == "__main__":
    unittest.main()
