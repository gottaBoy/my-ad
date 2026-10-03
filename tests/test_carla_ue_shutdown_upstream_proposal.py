import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORK = ROOT / "third_party/unreal-engine"
RELATIVE = "Engine/Source/Runtime/RenderCore/Private/GPUMessaging.cpp"
SOURCE = FORK / RELATIVE
PATCH = ROOT / "scripts/carla/patches/ue-gpumessaging-shutdown-guard.patch"
README = ROOT / "scripts/carla/patches/README.md"


class UpstreamShutdownGuardProposalTest(unittest.TestCase):
    """The engine-side fix stays a proposal until someone decides it is not.

    The defect it addresses is a static destruction order problem between two `TGlobalResource`
    globals, with the evidence recorded in docs/carla-dgx-audit.md 9.91. Applying it is a
    decision that would affect every GPU message user, so these tests hold the line between
    "proposed" and "applied" in both directions: the patch must still apply to the current fork,
    and it must still not be in the tree.
    """

    @classmethod
    def setUpClass(cls):
        cls.patch = PATCH.read_text(encoding="utf-8")
        cls.added = "\n".join(
            line[1:]
            for line in cls.patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )

    def test_patch_exists_and_is_documented(self):
        self.assertTrue(PATCH.is_file())
        readme = README.read_text(encoding="utf-8")
        self.assertIn(PATCH.name, readme)
        # The README separates curated applied patches from the frozen working-tree delta; this
        # is a third category and has to stay described as such.
        self.assertIn("提案", readme)
        self.assertIn("没应用", readme)

    def test_patch_applies_cleanly_to_the_current_fork(self):
        result = subprocess.run(
            ["git", "-C", str(FORK), "apply", "--check", str(PATCH)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_patch_is_not_applied(self):
        self.assertNotIn("bIsBeingDestroyed", SOURCE.read_text(encoding="utf-8"))
        status = subprocess.run(
            ["git", "-C", str(FORK), "status", "--short", "--", RELATIVE],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual("", status.stdout.strip())

    def test_patch_touches_a_single_file(self):
        targets = {
            line[4:].strip()
            for line in self.patch.splitlines()
            if line.startswith("--- ") or line.startswith("+++ ")
        }
        self.assertEqual({f"a/{RELATIVE}", f"b/{RELATIVE}"}, targets)

    def test_flag_is_set_in_the_destructor_body(self):
        # A destructor body runs before the class's members are destroyed, which is the whole
        # reason the guard works: it has to cover the window in which MessageHandlers is being
        # torn down, not the moment after.
        self.assertRegex(
            self.added,
            r"~FSystem\(\)\s*\{\s*(?://[^\n]*\n\s*)*bIsBeingDestroyed = true;",
        )
        self.assertIn("Runs before MessageHandlers is destroyed", self.added)

    def test_flag_is_declared_and_defined(self):
        self.assertIn("static bool bIsBeingDestroyed;", self.added)
        self.assertIn("bool FSystem::bIsBeingDestroyed = false;", self.added)

    def test_reset_is_guarded_by_the_flag(self):
        self.assertIn("if (MessageId.IsValid() && !FSystem::bIsBeingDestroyed)", self.added)
        # The socket must still drop its own id: a re-used socket object that kept a stale
        # MessageId would point at a registration that no longer exists. The line sits outside
        # the hunk context, so the property to check is that the patch does not delete it.
        removed = "\n".join(
            line[1:]
            for line in self.patch.splitlines()
            if line.startswith("-") and not line.startswith("---")
        )
        self.assertNotIn("MessageId = FMessageId::Null;", removed)
        self.assertIn("MessageId = FMessageId::Null;", SOURCE.read_text(encoding="utf-8"))

    def test_the_check_itself_is_left_alone(self):
        # Relaxing the assertion would hide genuine double-unregister bugs; the defect is a
        # lifetime problem, so the patch must not touch RemoveHandler.
        self.assertNotIn("MessageHandlers.Contains", self.added)
        self.assertNotIn("check(MessageId.IsValid())", self.added)


if __name__ == "__main__":
    unittest.main()
