import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")
ROOTS = {
    "project": ROOT,
    "carla": ROOT / "third_party/carla",
    "ue": ROOT / "third_party/unreal-engine",
}


def untracked_entries(root):
    """Every untracked path git does not ignore, as the manifest sees them."""
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    return [entry for entry in result.stdout.decode("utf-8", "replace").split("\0") if entry]


@unittest.skipUnless(GIT, "git is required")
class ScratchHygieneTest(unittest.TestCase):
    """A tree that carries non-regular untracked entries cannot be manifested.

    capture_build_manifest.py replays a build from three things: the recorded
    HEAD, the tracked binary patch, and a snapshot of every untracked entry git
    does not ignore. Regular files are copied and hashed and directories are
    traversed, but anything else aborts the capture with `not a regular file`.
    The scratch directory `.codex-tmp/` broke `make carla-manifest` with exactly
    that error - it held nine symlinks pointing into shared engine and content
    trees - so the precondition is asserted here rather than rediscovered at
    capture time. It is a real precondition, not a style rule: archiving the
    scratch evidence and removing the directory is what unblocked the manifest.
    """

    def test_no_untracked_entry_is_a_non_regular_file(self):
        offenders = []
        for name, root in sorted(ROOTS.items()):
            if not (root / ".git").exists():
                self.skipTest(f"{name} is not a git checkout")
            for entry in untracked_entries(root):
                path = root / entry
                if path.is_symlink() or (path.exists() and not path.is_file()
                                         and not path.is_dir()):
                    offenders.append(f"{name}:{entry}")
                elif not path.exists():
                    # A dangling symlink is reported above; anything else that
                    # vanished between listing and stat is a race, not a defect.
                    continue
        self.assertEqual(
            [], offenders,
            "untracked entries that the build manifest cannot replay; move the "
            "evidence into artifacts/ and delete the scratch copy, or ignore "
            "the path in .gitignore: " + ", ".join(offenders),
        )

    def test_listing_matches_what_the_manifest_walks(self):
        # Guard the guard: an empty listing would make the assertion above
        # vacuous, and the manifest really does see untracked files here.
        entries = untracked_entries(ROOT)
        self.assertTrue(any(entry.startswith("scripts/carla/") for entry in entries))


if __name__ == "__main__":
    unittest.main()
