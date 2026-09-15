"""Stage/restore only the files owned by the domain cutover; also usable on a fixture."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
UNITS = [f"cs2-inventory-{name}" for name in (
    "web.service", "worker.service", "schedule.service", "schedule.timer",
    "cleanup.service", "cleanup.timer",
)]
NEW_SITES = ("000-default-deny.conf", "cs2-domain.conf", "cs2-domain-acme.conf")


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def stage(root: Path):
    apache = root / "etc/apache2"
    for source, destination in (
        ("apache-default-deny.conf", "000-default-deny.conf"),
        ("apache-cs2-inventory.conf", "cs2-domain.conf"),
    ):
        shutil.copy2(HERE / source, apache / "sites-available" / destination)
        link = apache / "sites-enabled" / destination
        link.unlink(missing_ok=True)
        link.symlink_to(f"../sites-available/{destination}")
    for name in ("cs2-inventory.conf", "cs2-domain-acme.conf"):
        (apache / "sites-enabled" / name).unlink(missing_ok=True)
    shutil.copy2(HERE / "cs2-retired-paths.conf", apache / "cs2-retired-paths.conf")
    for name in ("healthdoc.conf", "healthdoc-le-ssl.conf"):
        path = apache / "sites-available" / name
        content = path.read_text(encoding="utf-8")
        include = "    Include /etc/apache2/cs2-retired-paths.conf\n"
        if include not in content:
            marker = "    ServerName healthdoc.cn\n"
            if content.count(marker) != 1:
                raise ValueError(f"Unexpected HealthDoc virtual host: {name}")
            write(path, content.replace(marker, marker + include, 1))
    env = root / "etc/cs2-inventory.env"
    values = {"CS2_COOKIE_PATH": "/", "CS2_COOKIE_SECURE": "1",
              "CS2_TRUSTED_HOSTS": "cs2inventory.cn,localhost,127.0.0.1"}
    lines = [line for line in env.read_text().splitlines() if line.split("=", 1)[0] not in values]
    write(env, "\n".join(lines + [f"{key}={value}" for key, value in values.items()]) + "\n")
    hook = root / "etc/letsencrypt/renewal-hooks/deploy/cs2-reload.sh"
    hook.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HERE / "certbot-cs2-reload.sh", hook)
    hook.chmod(0o755)


def restore(root: Path, backup: Path):
    apache = root / "etc/apache2"
    for name in NEW_SITES:
        (apache / "sites-enabled" / name).unlink(missing_ok=True)
        (apache / "sites-available" / name).unlink(missing_ok=True)
    (apache / "cs2-retired-paths.conf").unlink(missing_ok=True)
    (root / "etc/letsencrypt/renewal-hooks/deploy/cs2-reload.sh").unlink(missing_ok=True)
    # Restore links themselves rather than following or overwriting their targets.
    for source in (backup / "apache2").rglob("*"):
        destination = apache / source.relative_to(backup / "apache2")
        if source.is_symlink():
            destination.unlink(missing_ok=True)
            destination.symlink_to(source.readlink())
        elif source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            shutil.copy2(source, destination)
    shutil.copy2(backup / "cs2-inventory.env", root / "etc/cs2-inventory.env")
    for name in UNITS:
        shutil.copy2(backup / name, root / "etc/systemd/system" / name)
    current = root / "opt/cs2-inventory/current"
    previous = (backup / "previous-release.txt").read_text().strip()
    if not previous.startswith("/opt/cs2-inventory/releases/"):
        raise ValueError("Unexpected previous release")
    current.unlink(missing_ok=True)
    current.symlink_to(previous)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("stage", "restore"))
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if root != Path("/") and not str(root).startswith("/tmp/cs2-domain-test."):
        raise SystemExit("Fixture root must be /tmp/cs2-domain-test.*")
    if args.action == "stage":
        stage(root)
    else:
        if not args.backup:
            parser.error("restore requires --backup")
        restore(root, args.backup.resolve())
    print(f"DOMAIN_CONFIG_{args.action.upper()}_OK")
