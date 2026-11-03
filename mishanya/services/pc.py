"""Управление программами на компьютере, где работает бот: открыть установленную программу или закрыть запущенную.

Открывает только то, что установлено (ярлыки меню «Пуск») или добавлено владельцем в панели, — произвольные команды
не выполняются никогда. Закрывает запущенные процессы, кроме системных и самого бота.
"""
from __future__ import annotations

import csv
import json
import io
import logging
import os
import subprocess
import sys
from pathlib import Path

from .mind import best_match, normalize

log = logging.getLogger(__name__)

# Эти процессы бот не закроет ни при каких просьбах: без них Windows или сам бот перестанут работать.
PROTECTED = {
    "system", "idle", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "lsaiso.exe", "svchost.exe", "fontdrvhost.exe", "dwm.exe", "explorer.exe", "sihost.exe",
    "taskhostw.exe", "ctfmon.exe", "conhost.exe", "runtimebroker.exe", "searchhost.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "securityhealthservice.exe", "msmpeng.exe", "audiodg.exe", "spoolsv.exe",
    "python.exe", "pythonw.exe", "py.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe",
    "ffmpeg.exe", "taskmgr.exe", "logonui.exe", "wudfhost.exe", "dllhost.exe", "smartscreen.exe",
}


class PcError(Exception):
    pass


def start_menu_programs() -> dict[str, str]:
    """Установленные программы: имя ярлыка → путь к ярлыку (меню «Пуск» всех пользователей и текущего)."""
    roots = [Path(os.environ.get("ProgramData", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
             Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs"]
    out: dict[str, str] = {}
    skip = ("uninstall", "удал", "readme", "help", "справк", "documentation", "website", "сайт")
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if p.suffix.lower() not in (".lnk", ".url") or any(s in p.stem.lower() for s in skip):
                continue
            out.setdefault(p.stem, str(p))
    return out


APPS_PREFIX = "shell:AppsFolder\\"


def start_apps() -> dict[str, str]:
    """Все приложения из меню «Пуск», включая установленные из Microsoft Store (Spotify, Telegram, WhatsApp…),
    у которых нет обычного ярлыка. Имя → shell:AppsFolder + AppID."""
    if sys.platform != "win32":
        return {}
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, encoding="utf-8", timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        rows = json.loads(out or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    if isinstance(rows, dict):
        rows = [rows]
    skip = ("uninstall", "удал", "readme", "help", "справк", "documentation", "website", "сайт")
    return {r["Name"]: APPS_PREFIX + r["AppID"] for r in rows
            if isinstance(r, dict) and r.get("Name") and r.get("AppID") and not any(x in r["Name"].lower() for x in skip)}


def catalog(custom: list[tuple[str, str]], use_start_menu: bool) -> dict[str, str]:
    """Что бот может открыть: свои программы из панели (приоритет) + всё из меню «Пуск» (и из Microsoft Store)."""
    items: dict[str, str] = {}
    if use_start_menu and sys.platform == "win32":
        items.update(start_menu_programs())
        items.update(start_apps())
    items.update({name: target for name, target in custom})
    return items


def open_program(name: str, items: dict[str, str]) -> str:
    """Открывает программу по названию. Возвращает настоящее название того, что открыл."""
    found = best_match(name, list(items), lambda n: (n,))
    if found is None:
        raise PcError(f"Не нашёл программу «{name}» среди установленных.")
    target = items[found]  # type: ignore[index]
    if target.startswith(APPS_PREFIX):
        if sys.platform != "win32":
            raise PcError("Открывать программы можно только на Windows.")
        if any(c in target for c in '"<>|&;'):
            raise PcError("Странный идентификатор приложения — не открываю.")
        subprocess.Popen(["explorer.exe", target], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # noqa: S603, S607
        log.info("Компьютер: открыл «%s» (%s)", found, target)
        return str(found)
    if target.startswith("steam://"):
        if not target.removeprefix("steam://rungameid/").isdigit():
            raise PcError("Неверная ссылка Steam.")
    elif not Path(target).exists():
        raise PcError(f"Файл программы «{found}» не найден.")
    if sys.platform != "win32":
        raise PcError("Открывать программы можно только на Windows.")
    os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606 — только ярлыки, файлы и steam:// из каталога
    log.info("Компьютер: открыл «%s» (%s)", found, target)
    return str(found)


def running_programs() -> list[str]:
    """Имена запущенных процессов (без системных)."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    names = {row[0] for row in csv.reader(io.StringIO(out)) if row}
    return sorted(n for n in names if n.lower() not in PROTECTED)


def close_program(name: str) -> str:
    """Закрывает запущенную программу по названию (все её процессы). Системные процессы и бот защищены."""
    procs = running_programs()
    q = normalize(name).replace(" ", "")
    found = best_match(name, procs, lambda n: (n.removesuffix(".exe"), n))
    if found is None:  # «закрой стим» → steam.exe, «дискорд» → Discord.exe и т. п.
        cand = [p for p in procs if q and (q in normalize(p).replace(" ", "") or normalize(p.removesuffix(".exe")) in q)]
        found = cand[0] if len(cand) == 1 else None
    if found is None:
        raise PcError(f"Не нашёл запущенную программу «{name}».")
    if str(found).lower() in PROTECTED:
        raise PcError(f"«{found}» — системный процесс, его не трогаю.")
    r = subprocess.run(["taskkill", "/im", str(found), "/t", "/f"], capture_output=True, text=True, timeout=20,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise PcError(f"Windows не дал закрыть «{found}»: {(r.stderr or r.stdout).strip()[:200]}")
    log.info("Компьютер: закрыл %s", found)
    return str(found)
