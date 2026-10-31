"""Автообновление из GitHub и перезапуск через start.bat.

Два режима:
* git — копия получена через `git clone` и git установлен: fetch + merge --ff-only;
* archive — копия скачана ZIP-архивом (или git не установлен): версия хранится в файле `.version`,
  новая версия скачивается архивом с GitHub и распаковывается поверх, не трогая `.env`, `data`,
  `backups` и `.venv`.

Перед обновлением создаётся резервная копия базы. Зависимости Python переустанавливает start.bat после
выхода процесса: на Windows нельзя заменить модули, загруженные работающим приложением.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

from ..db import utcnow
from ..db.backup import create_backup

log = logging.getLogger(__name__)

# Код выхода, по которому start.bat ставит зависимости и запускает приложение снова.
RESTART_EXIT_CODE = 75
DEPS_FLAG = ".update-deps"
LAUNCHER_ENV = "MISHANYA_LAUNCHER"
GIT_TIMEOUT = 120
BUILD_DIR = "frontend/dist/"

GITHUB_REPO = os.environ.get("MISHANYA_UPDATE_REPO", "ScA1322/mishanya")
GITHUB_BRANCH = os.environ.get("MISHANYA_UPDATE_BRANCH", "main")
VERSION_FILE = ".version"
MANIFEST_FILE = ".update-manifest"
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
# Пользовательские данные и окружение — архив их никогда не перезаписывает и не удаляет.
PROTECTED = {".env", ".git", ".venv", "data", "backups", VERSION_FILE, MANIFEST_FILE, DEPS_FLAG}


class UpdateError(Exception):
    """Обновление невозможно; текст показывается пользователю."""


@dataclass
class UpdateState:
    supported: bool = False
    mode: str | None = None  # git | archive
    reason: str | None = None
    branch: str | None = None
    upstream: str | None = None
    current: str | None = None
    remote: str | None = None
    behind: int = 0
    ahead: int = 0
    commits: list[dict] = field(default_factory=list)
    last_check: datetime | None = None
    last_error: str | None = None
    last_update: datetime | None = None
    busy: bool = False
    restart_required: bool = False


class UpdateService:
    def __init__(self, repo_dir: Path, db_path: Path, backup_dir: Path):
        self.repo_dir = repo_dir
        self.db_path = db_path
        self.backup_dir = backup_dir
        self.state = UpdateState()
        self._lock = asyncio.Lock()
        self._remote_sha: str | None = None

    @staticmethod
    def launched_by_script() -> bool:
        return os.environ.get(LAUNCHER_ENV) == "1"

    # ------------------------------------------------------------ git

    def _git(self, *args: str, timeout: int = GIT_TIMEOUT) -> str:
        git = shutil.which("git")
        if git is None:
            raise UpdateError("Git не установлен. Скачайте его с https://git-scm.com/download/win и перезапустите "
                              "Mishanya.")
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "LC_ALL": "C"}
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            res = subprocess.run([git, "-c", "i18n.logOutputEncoding=UTF-8", *args], cwd=self.repo_dir, env=env,
                                 capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, **kwargs)
        except subprocess.TimeoutExpired:
            raise UpdateError(f"git {args[0]}: нет ответа за {timeout} с. Проверьте подключение к интернету.") from None
        except OSError as exc:
            raise UpdateError(f"Не удалось запустить git: {exc}") from exc
        out = res.stdout.decode("utf-8", "replace").strip()
        if res.returncode != 0:
            err = res.stderr.decode("utf-8", "replace").strip().splitlines()
            raise UpdateError(f"git {args[0]}: {err[-1] if err else f'код {res.returncode}'}")
        return out

    def _use_git(self) -> bool:
        return (self.repo_dir / ".git").exists() and shutil.which("git") is not None

    def _probe(self) -> None:
        """Заполняет сведения о репозитории или объясняет, почему обновление недоступно."""
        st = self.state
        if not self._use_git():
            st.mode, st.supported, st.reason = "archive", True, None
            st.branch, st.upstream = GITHUB_BRANCH, f"github.com/{GITHUB_REPO}"
            st.current = (self._read_version() or "")[:7] or None
            return
        st.mode = "git"
        st.branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        try:
            st.upstream = self._git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        except UpdateError:
            st.supported, st.reason = False, (f"У ветки {st.branch} нет связанной ветки на GitHub — "
                                              "непонятно, откуда брать обновления.")
            return
        st.current = self._git("rev-parse", "--short", "HEAD")
        st.supported, st.reason = True, None

    def _check_sync(self, fetch_timeout: int = GIT_TIMEOUT) -> None:
        self._probe()
        st = self.state
        if not st.supported:
            return
        if st.mode == "archive":
            self._archive_check(fetch_timeout)
            return
        remote_name = st.upstream.split("/", 1)[0] if st.upstream else "origin"
        self._git("fetch", "--quiet", remote_name, timeout=fetch_timeout)
        st.remote = self._git("rev-parse", "--short", "@{u}")
        counts = self._git("rev-list", "--left-right", "--count", "HEAD...@{u}").split()
        st.ahead, st.behind = int(counts[0]), int(counts[1])
        st.commits = []
        if st.behind:
            for line in self._git("log", "--format=%h%x09%an%x09%cI%x09%s", "-n", "30", "HEAD..@{u}").splitlines():
                sha, author, date, subject = (line.split("\t", 3) + ["", "", ""])[:4]
                st.commits.append({"sha": sha, "author": author, "date": date, "subject": subject})

    def _local_changes(self) -> list[str]:
        out = self._git("status", "--porcelain", "--untracked-files=no")
        return [line[3:] for line in out.splitlines() if line.strip()]

    def _file_hash(self, name: str) -> str | None:
        path = self.repo_dir / name
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

    def _apply_sync(self) -> str:
        self._check_sync()
        st = self.state
        if not st.supported:
            raise UpdateError(st.reason or "Обновление недоступно")
        if not st.behind:
            raise UpdateError("Обновлений нет: установлена последняя версия.")
        if st.mode == "archive":
            return self._archive_apply()
        if st.ahead:
            raise UpdateError(f"В локальной копии есть {st.ahead} собственных коммитов, которых нет на GitHub. "
                              "Автоматическое обновление отключено, чтобы их не потерять — обновите вручную (git pull).")
        changed = self._local_changes()
        if changed and all(c.startswith(BUILD_DIR) for c in changed):
            # Панель пересобрана локально (setup.bat с Node.js) — это сборочные файлы, их можно заменить.
            self._git("checkout", "--", BUILD_DIR)
            changed = []
        if changed:
            shown = ", ".join(changed[:5]) + (" и др." if len(changed) > 5 else "")
            raise UpdateError(f"Файлы программы изменены вручную ({shown}). Обновление отменено, чтобы не "
                              "потерять изменения. Верните файлы (git checkout -- .) или обновите вручную.")
        if self.db_path.exists():
            create_backup(self.db_path, self.backup_dir, "update")
        req_before = self._file_hash("requirements.txt")
        old = st.current
        self._git("merge", "--ff-only", "--quiet", "@{u}")
        if self._file_hash("requirements.txt") != req_before:
            (self.repo_dir / DEPS_FLAG).write_text("requirements.txt changed\n", encoding="utf-8")
        st.current = self._git("rev-parse", "--short", "HEAD")
        st.behind, st.commits = 0, []
        st.last_update = utcnow()
        st.restart_required = True
        log.info("Mishanya обновлена: %s → %s", old, st.current)
        return st.current

    # ------------------------------------------------------------ архив с GitHub (установка из ZIP)

    def _read_version(self) -> str | None:
        path = self.repo_dir / VERSION_FILE
        try:
            sha = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return sha if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha) else None

    @staticmethod
    def _http(url: str, timeout: int, limit: int = 5 * 1024 * 1024) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "Mishanya-updater",
                                                   "Accept": "application/vnd.github+json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — только https://*.github.com
                data = resp.read(limit + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise UpdateError(f"Репозиторий {GITHUB_REPO} не найден на GitHub или закрыт.") from None
            if exc.code in (403, 429):
                raise UpdateError("GitHub временно ограничил число запросов. Проверка повторится позже.") from None
            raise UpdateError(f"GitHub ответил ошибкой {exc.code}.") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise UpdateError(f"Нет связи с GitHub ({reason}). Проверьте подключение к интернету.") from None
        if len(data) > limit:
            raise UpdateError("Ответ GitHub слишком большой.")
        return data

    def _api(self, path: str, timeout: int) -> dict:
        data = self._http(f"https://api.github.com/repos/{GITHUB_REPO}/{path}", timeout)
        try:
            return json.loads(data)
        except ValueError:
            raise UpdateError("GitHub вернул непонятный ответ.") from None

    @staticmethod
    def _commit_info(c: dict) -> dict:
        commit = c.get("commit") or {}
        author = commit.get("author") or {}
        return {"sha": str(c.get("sha", ""))[:7], "author": author.get("name", ""), "date": author.get("date", ""),
                "subject": (commit.get("message") or "").splitlines()[0][:200] if commit.get("message") else ""}

    def _archive_check(self, timeout: int) -> None:
        st = self.state
        latest = self._api(f"commits/{GITHUB_BRANCH}", timeout)
        remote_sha = str(latest.get("sha", ""))
        if len(remote_sha) != 40:
            raise UpdateError("GitHub не сообщил номер последней версии.")
        self._remote_sha = remote_sha
        st.remote, st.ahead = remote_sha[:7], 0
        current = self._read_version()
        if current == remote_sha:
            st.behind, st.commits = 0, []
            return
        commits: list[dict] = []
        behind = 0
        if current:
            try:
                cmp = self._api(f"compare/{current}...{remote_sha}", timeout)
                behind = int(cmp.get("ahead_by") or 0)
                commits = [self._commit_info(c) for c in (cmp.get("commits") or [])][::-1][:30]
            except UpdateError:
                behind = 0  # своей версии на GitHub нет — просто ставим последнюю
        if not behind:
            behind, commits = 1, [self._commit_info(latest)]
        st.behind, st.commits = behind, commits

    def _archive_apply(self) -> str:
        st = self.state
        sha = self._remote_sha
        if not sha:
            raise UpdateError("Сначала проверьте обновления.")
        data = self._http(f"https://codeload.github.com/{GITHUB_REPO}/zip/{sha}", 180, MAX_ARCHIVE_BYTES)
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise UpdateError("Скачанный архив повреждён. Попробуйте ещё раз позже.") from None
        files: dict[str, zipfile.ZipInfo] = {}
        for info in archive.infolist():
            parts = PurePosixPath(info.filename).parts
            if info.is_dir() or len(parts) < 2:
                continue
            rel = PurePosixPath(*parts[1:])  # внутри архива всё лежит в папке <repo>-<sha>/
            if rel.is_absolute() or ".." in rel.parts or rel.parts[0] in PROTECTED:
                continue
            files[rel.as_posix()] = info
        if "mishanya/__init__.py" not in files or "start.bat" not in files:
            raise UpdateError("В архиве с GitHub нет файлов Mishanya — обновление отменено.")
        if self.db_path.exists():
            create_backup(self.db_path, self.backup_dir, "update")
        req_before = self._file_hash("requirements.txt"), self._file_hash("requirements-voice.txt")
        for rel, info in files.items():
            target = self.repo_dir / rel
            content = archive.read(info)
            if target.exists() and target.read_bytes() == content:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".mishanya-new")
            tmp.write_bytes(content)
            os.replace(tmp, target)
        # Удаляем только файлы, которые сами ставили прошлым обновлением и которых больше нет в программе.
        manifest = self.repo_dir / MANIFEST_FILE
        try:
            old_files = set(manifest.read_text(encoding="utf-8").splitlines())
        except OSError:
            old_files = set()
        for rel in sorted(old_files - files.keys()):
            p = PurePosixPath(rel)
            if rel and ".." not in p.parts and not p.is_absolute() and p.parts[0] not in PROTECTED:
                (self.repo_dir / rel).unlink(missing_ok=True)
        manifest.write_text("\n".join(sorted(files)) + "\n", encoding="utf-8")
        if (self._file_hash("requirements.txt"), self._file_hash("requirements-voice.txt")) != req_before:
            (self.repo_dir / DEPS_FLAG).write_text("requirements changed\n", encoding="utf-8")
        old = st.current
        (self.repo_dir / VERSION_FILE).write_text(sha + "\n", encoding="utf-8")
        st.current = sha[:7]
        st.behind, st.commits = 0, []
        st.last_update = utcnow()
        st.restart_required = True
        log.info("Mishanya обновлена из архива GitHub: %s → %s", old or "неизвестная версия", st.current)
        return st.current

    # ------------------------------------------------------------ публичный интерфейс

    async def check(self, fetch_timeout: int = GIT_TIMEOUT) -> UpdateState:
        async with self._lock:
            self.state.busy = True
            try:
                await asyncio.to_thread(self._check_sync, fetch_timeout)
                self.state.last_error = None
            except UpdateError as exc:
                self.state.last_error = str(exc)
                log.warning("Проверка обновлений не удалась: %s", exc)
            finally:
                self.state.last_check = utcnow()
                self.state.busy = False
            return self.state

    async def apply(self) -> str:
        async with self._lock:
            self.state.busy = True
            try:
                return await asyncio.to_thread(self._apply_sync)
            except UpdateError as exc:
                self.state.last_error = str(exc)
                raise
            except Exception as exc:  # noqa: BLE001
                self.state.last_error = f"Неожиданная ошибка обновления: {exc}"
                log.exception("Ошибка обновления")
                raise UpdateError(self.state.last_error) from exc
            finally:
                self.state.busy = False

    def due(self, interval_hours: int) -> bool:
        last = self.state.last_check
        return last is None or utcnow() - last >= timedelta(hours=interval_hours)
