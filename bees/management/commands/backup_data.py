"""Daily backup of the database (and locally stored images).

Creates one archive per run: backup-YYYYMMDD-HHMM.tar.gz containing
  - db.sqlite3 (a consistent copy, when using SQLite), or
    data.json (a full data export, when using Postgres/Supabase)
  - media/ and private_media/ (only when images are stored on this server)

Where it goes:
  - Supabase Storage, private bucket, folder backups/  (when Supabase
    Storage is configured - safest, survives a server problem), or
  - the backups/ folder next to manage.py.

Only the newest --keep archives are kept (default 14).

    python manage.py backup_data
    python manage.py restore_backup            (see that command)
"""
import gzip
import io
import os
import shutil
import sqlite3
import tarfile
import tempfile

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

PREFIX = "backup-"
FOLDER = "backups"


def remote_storage():
    if getattr(settings, "USE_SUPABASE_STORAGE", False):
        from django.core.files.storage import storages
        return storages["private"]
    return None


def local_dir():
    path = os.path.join(settings.BASE_DIR, FOLDER)
    os.makedirs(path, exist_ok=True)
    return path


def list_backups():
    """Newest first: list of names."""
    storage = remote_storage()
    if storage:
        _, files = storage.listdir(FOLDER)
    else:
        files = os.listdir(local_dir())
    return sorted((f for f in files if f.startswith(PREFIX) and f.endswith(".tar.gz")), reverse=True)


class Command(BaseCommand):
    help = "Back up the database (and local images) and keep the newest copies."

    def add_arguments(self, parser):
        parser.add_argument("--keep", type=int, default=14)
        parser.add_argument("--no-media", action="store_true", help="Skip images.")

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        stamp = timezone.now().strftime("%Y%m%d-%H%M")
        name = f"{PREFIX}{stamp}.tar.gz"
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, name)
            with tarfile.open(archive, "w:gz") as tar:
                if db["ENGINE"].endswith("sqlite3"):
                    copy = os.path.join(tmp, "db.sqlite3")
                    source = sqlite3.connect(str(db["NAME"]))
                    target = sqlite3.connect(copy)
                    with target:
                        source.backup(target)  # consistent even while the site is running
                    source.close()
                    target.close()
                    tar.add(copy, arcname="db.sqlite3")
                else:
                    buf = io.StringIO()
                    call_command("dumpdata", "--natural-foreign", "--natural-primary",
                                 "--exclude=contenttypes", "--exclude=auth.permission", "--exclude=sessions",
                                 "--exclude=admin.logentry", stdout=buf)
                    data = buf.getvalue().encode()
                    info = tarfile.TarInfo("data.json")
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
                if not options["no_media"] and not getattr(settings, "USE_SUPABASE_STORAGE", False):
                    for folder, arc in ((settings.MEDIA_ROOT, "media"), (getattr(settings, "PRIVATE_MEDIA_ROOT", ""), "private_media")):
                        if folder and os.path.isdir(folder):
                            tar.add(str(folder), arcname=arc)
            size = os.path.getsize(archive)
            storage = remote_storage()
            if storage:
                with open(archive, "rb") as fh:
                    from django.core.files import File
                    storage.save(f"{FOLDER}/{name}", File(fh, name=name))
                where = "Supabase Storage (private bucket)"
            else:
                shutil.copy(archive, os.path.join(local_dir(), name))
                where = os.path.join(settings.BASE_DIR, FOLDER)

        removed = 0
        for old in list_backups()[options["keep"]:]:
            if storage:
                storage.delete(f"{FOLDER}/{old}")
            else:
                os.remove(os.path.join(local_dir(), old))
            removed += 1
        self.stdout.write(self.style.SUCCESS(
            f"Backup {name} ({size / 1024 / 1024:.1f} MB) saved to {where}. Removed {removed} old backup(s)."))


# gzip is imported so tarfile's "w:gz" mode is available on minimal Pythons.
_ = gzip
