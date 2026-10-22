import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import io
import sys

from tokensaver.memory import RollingMemory, build_request
from tokensaver.cli import main


class MemoryTests(unittest.TestCase):
    def test_add_deduplicates_and_render_contains_fact(self):
        memory = RollingMemory(project="Example")
        self.assertTrue(memory.add("GOALS", "Build a game"))
        self.assertFalse(memory.add("GOALS", "Build a game"))
        self.assertIn("Build a game", memory.render())

    def test_recent_turns_are_bounded(self):
        memory = RollingMemory(max_recent_turns=2)
        memory.add_turn("user", "one")
        memory.add_turn("assistant", "two")
        memory.add_turn("user", "three")
        rendered = memory.render()
        self.assertNotIn("one", rendered)
        self.assertIn("three", rendered)

    def test_multiline_items_and_turns_are_preserved(self):
        memory = RollingMemory()
        memory.add("OPEN_TASKS", 'Traceback (most recent call last):\n  File "app.py", line 4\nValueError: bad')
        memory.add_turn("user", "~~~python\ndef f():\n    return 1\n~~~")
        self.assertIn("line 4\nValueError", memory.render())
        self.assertIn("def f():\n    return 1", memory.render())

    def test_request_combines_memory_and_new_multiline_message(self):
        memory = RollingMemory(project="Example")
        memory.add("GOALS", "Build a game")
        prompt = build_request(memory, "Fix this:\n  return 1")
        self.assertIn("[COMPRESSED MEMORY]", prompt)
        self.assertIn("Build a game", prompt)
        self.assertIn("[NEW MESSAGE]", prompt)
        self.assertIn("Fix this:\n  return 1", prompt)

    def test_category_and_total_memory_limits(self):
        memory = RollingMemory(max_items_per_category=2, max_total_chars=100)
        memory.add("GOALS", "first fact")
        memory.add("GOALS", "second fact")
        memory.add("GOALS", "third fact")
        self.assertEqual(memory.sections["GOALS"], ["second fact", "third fact"])
        memory.add_turn("user", "x" * 200)
        self.assertLessEqual(memory._total_chars(), 100)

    def test_save_and_load_roundtrip_and_backup(self):
        memory = RollingMemory(project="Test")
        memory.add("DECISIONS", "Use standard library")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            memory.save(path)
            memory.add("RULES", "Preserve tests")
            memory.save(path)
            loaded = RollingMemory.load(path)
            self.assertEqual(loaded.project, "Test")
            self.assertIn("Use standard library", loaded.sections["DECISIONS"])
            self.assertTrue(Path(str(path) + ".bak").exists())

    def test_corrupt_json_is_backed_up_and_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "A copy was saved"):
                RollingMemory.load(path)
            self.assertEqual(Path(str(path) + ".bak").read_text(encoding="utf-8"), "{broken")

    def test_missing_memory_file_is_not_silently_created(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                RollingMemory.load(Path(directory) / "missing.json")

    def test_remove_and_clear(self):
        memory = RollingMemory(project="Kept project")
        memory.add("RULES", "Keep tests")
        memory.add_turn("user", "hello")
        self.assertTrue(memory.remove("RULES", "Keep tests"))
        self.assertFalse(memory.remove("RULES", "missing"))
        memory.add("GOALS", "Build app")
        memory.clear()
        self.assertEqual(memory.project, "Kept project")
        self.assertFalse(any(memory.sections.values()))
        self.assertEqual(memory.recent_turns, [])

    def test_memory_init_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            RollingMemory(project="original").save(path)
            output = io.StringIO()
            with patch.object(sys, "argv", ["tokensaver", "memory-init", str(path)]), patch("sys.stderr", output):
                self.assertEqual(main(), 2)
            self.assertEqual(RollingMemory.load(path).project, "original")
            with patch.object(sys, "argv", ["tokensaver", "memory-init", str(path), "--force"]), patch("sys.stdout", io.StringIO()):
                self.assertEqual(main(), 0)
            self.assertTrue(Path(str(path) + ".bak").exists())

    def test_memory_add_on_missing_file_gives_friendly_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            err = io.StringIO()
            with patch.object(sys, "argv", ["tokensaver", "memory-add", str(path), "RULES", "Keep tests"]), patch("sys.stderr", err):
                self.assertEqual(main(), 2)
            self.assertIn("Memory file not found", err.getvalue())
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
