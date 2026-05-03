"""Phase 4: Internal backend/src/ restructure to clean-architecture template.

Folder moves (git mv — history preserved):
  backend/src/filters/        ->  backend/src/services/       (merge)
  backend/src/notifications/  ->  backend/src/services/notifications/
  backend/src/profile/        ->  backend/src/services/profile/
  backend/src/storage/        ->  backend/src/repositories/
  backend/src/config/         ->  backend/src/core/

Import rewrites applied to every .py file under backend/:
  from src.notifications.X -> from src.services.notifications.X
  from src.profile.X       -> from src.services.profile.X
  from src.filters.X       -> from src.services.X
  from src.storage.X       -> from src.repositories.X
  from src.config.X        -> from src.core.X
  import src.{same prefixes} rewritten with the same substitutions

Run once, then delete.
"""
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
SRC = BACKEND / "src"


def run(args: list[str]) -> None:
    """Run a command in the repo, raise on failure."""
    result = subprocess.run(
        args, cwd=str(REPO), capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.strip()}")


def git_mv(src: Path, dst: Path) -> None:
    run(["git", "mv", str(src.relative_to(REPO)), str(dst.relative_to(REPO))])


def phase_a_folder_moves() -> None:
    print("Phase A: folder moves")

    # Ensure services/ exists with its own __init__.py BEFORE moving filters/ files in.
    services = SRC / "services"
    services.mkdir(exist_ok=True)
    init = services / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")
        run(["git", "add", str(init.relative_to(REPO))])
        print(f"  created {init.relative_to(REPO)}")

    # 1. filters/ -> services/ — move each file individually, handle __init__.py conflict
    filters_dir = SRC / "filters"
    if filters_dir.exists():
        for f in sorted(filters_dir.iterdir()):
            if not f.is_file():
                continue
            dst = services / f.name
            if f.name == "__init__.py" and dst.exists():
                # Drop the filters one — services/ already has an __init__.py.
                run(["git", "rm", "-f", str(f.relative_to(REPO))])
                print(f"  rm  {f.relative_to(REPO)} (duplicate __init__.py)")
            else:
                git_mv(f, dst)
                print(f"  mv  {f.relative_to(REPO)} -> {dst.relative_to(REPO)}")
        # Clean leftover __pycache__ and then the empty dir. Windows/OneDrive
        # sometimes holds a lock on __pycache__ — tolerate it silently; git won't
        # track the dir either way (empty dirs are invisible to git).
        cache = filters_dir / "__pycache__"
        if cache.exists():
            try:
                shutil.rmtree(cache)
            except (OSError, PermissionError):
                pass
        try:
            filters_dir.rmdir()
        except OSError:
            pass

    # 2. notifications/ -> services/notifications/
    src_dir = SRC / "notifications"
    if src_dir.exists():
        git_mv(src_dir, services / "notifications")
        print(f"  mv  src/notifications/ -> src/services/notifications/")

    # 3. profile/ -> services/profile/
    src_dir = SRC / "profile"
    if src_dir.exists():
        git_mv(src_dir, services / "profile")
        print(f"  mv  src/profile/ -> src/services/profile/")

    # 4. storage/ -> repositories/
    src_dir = SRC / "storage"
    if src_dir.exists():
        git_mv(src_dir, SRC / "repositories")
        print(f"  mv  src/storage/ -> src/repositories/")

    # 5. config/ -> core/
    src_dir = SRC / "config"
    if src_dir.exists():
        git_mv(src_dir, SRC / "core")
        print(f"  mv  src/config/ -> src/core/")


# Order: specific prefixes BEFORE general. `src.notifications.` and `src.profile.`
# must go before `src.filters.` because the new form `src.services.X` doesn't
# collide — but that's still the safe order.
IMPORT_REWRITES = [
    ("src.notifications.", "src.services.notifications."),
    ("src.profile.",       "src.services.profile."),
    ("src.filters.",       "src.services."),
    ("src.storage.",       "src.repositories."),
    ("src.config.",        "src.core."),
]


def phase_b_import_rewrites() -> None:
    print("Phase B: import rewrites")
    skip_dirs = {"venv", ".venv", "__pycache__", ".pytest_cache", "node_modules", ".next"}
    total_files = 0
    total_rewrites = 0
    for py in BACKEND.rglob("*.py"):
        if any(part in skip_dirs for part in py.parts):
            continue
        content = py.read_text(encoding="utf-8")
        new_content = content
        file_count = 0
        for old, new in IMPORT_REWRITES:
            if old in new_content:
                file_count += new_content.count(old)
                new_content = new_content.replace(old, new)
        if new_content != content:
            py.write_text(new_content, encoding="utf-8")
            total_files += 1
            total_rewrites += file_count
            print(f"  {py.relative_to(REPO)}: {file_count}")
    print(f"  Total: {total_rewrites} rewrites in {total_files} files")


def main() -> None:
    phase_a_folder_moves()
    print()
    phase_b_import_rewrites()
    print("\nDone.")


if __name__ == "__main__":
    main()
