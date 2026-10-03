import fnmatch
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERIGNORE = ROOT / ".dockerignore"
DOCKERFILES = sorted((ROOT / "images").glob("*/Dockerfile"))

# The trees that must never enter a build context: they are storage, not inputs.
HEAVY_PATHS = (
    "artifacts/carla/cooked-client-full/CarlaUnreal/Content/Carla/Maps/x.umap",
    "data/models/some-model.bin",
    "data/engines/UE_5/Engine/Binaries/x",
    "third_party/unreal-engine/Engine/Source/Runtime/Core/Private/Core.cpp",
    "third_party/carla/Unreal/CarlaUnreal/Content/x.uasset",
    ".git/config",
)


def ignore_patterns(path=DOCKERIGNORE):
    patterns = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def context_inputs(dockerfile):
    """COPY/ADD sources that come from the build context, not from a stage."""
    inputs = []
    for raw in Path(dockerfile).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts[0].upper() not in ("COPY", "ADD"):
            continue
        if any(part.startswith("--from=") for part in parts[1:]):
            continue
        args = [part for part in parts[1:] if not part.startswith("--")]
        if len(args) < 2:
            continue
        inputs.extend(args[:-1])  # the last token is the destination
    return inputs


def is_included(path, patterns):
    """Approximate docker's ignore matching: patterns apply in order, last wins."""
    included = True
    for pattern in patterns:
        negated = pattern.startswith("!")
        candidate = pattern[1:] if negated else pattern
        if (fnmatch.fnmatch(path, candidate) or path == candidate
                or path.startswith(candidate.rstrip("/") + "/")):
            included = negated
    return included


class BuildContextTest(unittest.TestCase):
    """A .dockerignore that hides an input makes the image silently stale.

    The toolchain image in use was built 2026-09-14 while its Dockerfile had
    already changed on 2026-09-25, because the build context had grown to the
    whole ~966 GB working tree and no build could start. The context is now
    filtered, which is only safe while every Dockerfile input stays included.
    """

    def setUp(self):
        self.patterns = ignore_patterns()
        self.assertTrue(DOCKERFILES, "no Dockerfiles found under images/")

    def test_denies_everything_before_allowing(self):
        self.assertEqual("*", self.patterns[0],
                         ".dockerignore must deny the whole tree first, then "
                         "re-allow the few Dockerfile inputs")

    def test_every_dockerfile_context_input_is_included(self):
        for dockerfile in DOCKERFILES:
            for source in context_inputs(dockerfile):
                self.assertTrue(
                    is_included(source, self.patterns),
                    f"{dockerfile.relative_to(ROOT)} copies {source} from the "
                    "context but .dockerignore excludes it",
                )

    def test_no_dockerfile_reads_something_unexpected(self):
        # If a new input appears, this pins the expectation next to the check
        # above instead of letting the ignore file drift.
        seen = {source for dockerfile in DOCKERFILES
                for source in context_inputs(dockerfile)}
        self.assertEqual({"scripts/carla/g0-probe.sh",
                          "scripts/dataset-converter/convert.py"}, seen)

    def test_heavy_trees_are_excluded(self):
        for path in HEAVY_PATHS:
            self.assertFalse(is_included(path, self.patterns), path)

    def test_only_the_two_input_directories_are_included(self):
        included = [entry.name for entry in ROOT.iterdir()
                    if is_included(entry.name, self.patterns)]
        self.assertEqual(["images", "scripts"], sorted(included),
                         "the context must stay a few megabytes, not a terabyte")


if __name__ == "__main__":
    unittest.main()
