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


def _toolhelp_processes() -> list[str]:
    """Имена всех процессов через Windows API (быстро и без внешних команд, которые могут зависнуть)."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    names: set[str] = set()
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    try:
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            names.add(entry.szExeFile)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return sorted(names)


def _described_processes() -> dict[str, list[str]]:
    """Имя .exe → описание и название продукта (по ним находятся форки: AyuGram, 64Gram — это тоже «Telegram»)."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Process | Select-Object Name,"
                              "Description,Product | ConvertTo-Json -Compress"],
                             capture_output=True, text=True, encoding="utf-8", timeout=40,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        rows = json.loads(out or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    info: dict[str, list[str]] = {}
    for r in rows if isinstance(rows, list) else [rows]:
        if isinstance(r, dict) and r.get("Name"):
            info.setdefault(f"{r['Name']}.exe", []).extend(x for x in (r.get("Description"), r.get("Product")) if x)
    return info


def running_programs() -> list[str]:
    """Имена запущенных процессов (без системных)."""
    if sys.platform != "win32":
        return []
    try:
        names = _toolhelp_processes()
    except Exception:  # noqa: BLE001 — на всякий случай старый способ
        names = []
    if not names:
        try:
            out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=40,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            names = sorted({row[0] for row in csv.reader(io.StringIO(out)) if row})
        except (OSError, subprocess.SubprocessError):
            names = []
    return [n for n in names if n.lower() not in PROTECTED]


def close_program(name: str) -> str:
    """Закрывает запущенную программу по названию (все её процессы). Системные процессы и бот защищены."""
    procs = running_programs()
    if not procs:
        raise PcError("Не удалось получить список запущенных программ.")
    q = normalize(name).replace(" ", "")
    found = best_match(name, procs, lambda n: (n.removesuffix(".exe"), n))
    if found is None:  # «закрой стим» → steam.exe, «дискорд» → Discord.exe и т. п.
        cand = [p for p in procs if q and (q in normalize(p).replace(" ", "") or normalize(p.removesuffix(".exe")) in q)]
        found = cand[0] if len(cand) == 1 else None
    if found is None:  # ищем по описанию программы: «Telegram» → AyuGram.exe (Telegram Desktop)
        info = _described_processes()
        found = best_match(name, [p for p in procs if p in info], lambda n: (*info.get(n, []), n.removesuffix(".exe")))
    if found is None:
        log.info("Компьютер: «%s» не найден среди %d процессов", name, len(procs))
        raise PcError(f"Не нашёл запущенную программу «{name}». Запущено похожего: "
                      + (", ".join(p for p in procs if q[:3] and q[:3] in p.lower())[:300] or "ничего"))
    if str(found).lower() in PROTECTED:
        raise PcError(f"«{found}» — системный процесс, его не трогаю.")
    r = subprocess.run(["taskkill", "/im", str(found), "/t", "/f"], capture_output=True, text=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        err = (r.stderr or r.stdout).strip()
        if "denied" in err.lower() or "отказано" in err.lower() or r.returncode == 1:
            raise PcError(f"«{found}» запущен от имени администратора — бот без прав администратора закрыть его не может.")
        raise PcError(f"Windows не дал закрыть «{found}»: {err[:200]}")
    log.info("Компьютер: закрыл %s", found)
    return str(found)
