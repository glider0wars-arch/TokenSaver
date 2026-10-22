import tempfile
import unittest
from pathlib import Path
from tokensaver.memory import RollingMemory, build_request


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

    def test_request_combines_compact_memory_and_new_message(self):
        memory = RollingMemory(project="Example")
        memory.add("GOALS", "Build a game")
        prompt = build_request(memory, "Add diplomacy")
        self.assertIn("[COMPRESSED MEMORY]", prompt)
        self.assertIn("Build a game", prompt)
        self.assertIn("[NEW MESSAGE]", prompt)
        self.assertIn("Add diplomacy", prompt)
        self.assertNotIn("\\\\n", prompt)

    def test_save_and_load_roundtrip(self):
        memory = RollingMemory(project="Test")
        memory.add("DECISIONS", "Use standard library")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            memory.save(path)
            loaded = RollingMemory.load(path)
        self.assertEqual(loaded.project, "Test")
        self.assertIn("Use standard library", loaded.sections["DECISIONS"])


if __name__ == "__main__":
    unittest.main()
