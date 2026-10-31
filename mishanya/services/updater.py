"""Автообновление из GitHub через git: проверка, загрузка (fast-forward) и перезапуск через start.bat.

Обновляется только установка, сделанная через `git clone`. Перед обновлением создаётся резервная копия базы.
Зависимости Python переустанавливает start.bat после выхода процесса: на Windows нельзя заменить
модули, загруженные работающим приложением.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..db import utcnow
from ..db.backup import create_backup

log = logging.getLogger(__name__)

# Код выхода, по которому start.bat ставит зависимости и запускает приложение снова.
RESTART_EXIT_CODE = 75
DEPS_FLAG = ".update-deps"
LAUNCHER_ENV = "MISHANYA_LAUNCHER"
GIT_TIMEOUT = 120
BUILD_DIR = "frontend/dist/"


class UpdateError(Exception):
    """Обновление невозможно; текст показывается пользователю."""


@dataclass
class UpdateState:
    supported: bool = False
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

    def _probe(self) -> None:
        """Заполняет сведения о репозитории или объясняет, почему обновление недоступно."""
        st = self.state
        if not (self.repo_dir / ".git").exists():
            st.supported, st.reason = False, ("Mishanya установлена не через git clone (например, из ZIP-архива). "
                                              "Автообновление работает только для копии, полученной командой "
                                              "git clone.")
            return
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
