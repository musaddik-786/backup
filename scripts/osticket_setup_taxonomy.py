"""Create the Guidewire module topics and Issue Category list in osTicket.

Names come from backend/taxonomy.py (GuidewireModule, IssueCategory) so the AI app and
osTicket never drift. Safe to re-run: existing topics/list items/field are left alone.

    python scripts/osticket_setup_taxonomy.py --dry-run
    python scripts/osticket_setup_taxonomy.py --osticket-dir ~/osticket

Needs `php` on PATH and filesystem access to the osTicket install (it uses osTicket's own
PHP classes and DB credentials from include/ost-config.php).
"""
import argparse, importlib.util, json, os, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHP_SCRIPT = ROOT / "scripts" / "osticket" / "setup_taxonomy.php"


def load_taxonomy() -> dict:
    spec = importlib.util.spec_from_file_location("taxonomy", ROOT / "backend" / "taxonomy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {"modules": [m.value for m in mod.GuidewireModule],
            "categories": [c.value for c in mod.IssueCategory],
            "category_label": "Issue Category"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--osticket-dir", default=os.environ.get("OSTICKET_DIR", str(Path.home() / "osticket")))
    ap.add_argument("--dry-run", action="store_true", help="show what would be created, change nothing")
    args = ap.parse_args()
    php = shutil.which("php")
    if not php:
        sys.exit("php not found on PATH")
    cmd = [php, str(PHP_SCRIPT), os.path.expanduser(args.osticket_dir)] + (["--dry-run"] if args.dry_run else [])
    return subprocess.run(cmd, input=json.dumps(load_taxonomy()), text=True).returncode


if __name__ == "__main__":
    sys.exit(main())
