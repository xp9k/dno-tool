"""Скрипт подготовки заводского хоста (сервис мастера «prepare»).

Выполняется на хосте от root: ``python3 -I -c PREPARE_SCRIPT``, параметры -
JSON в stdin (пароли в командную строку не попадают). Совместим с Python 3.8
(МОС 12). Сначала проверяет всё, затем меняет. Всегда завершается с кодом 0 и
печатает одну строку JSON ``{"ok", "messages", "warnings", "error"}`` без
паролей: транспорт с секретами скрывает вывод при ненулевом коде.
"""

PREPARE_SCRIPT = r"""
import grp
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile

LOGIN_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
KEY_RE = re.compile(
    r"(ssh-[a-z0-9-]+|ecdsa-sha2-[a-z0-9]+|sk-[a-z0-9@.-]+)\s+AAAA[A-Za-z0-9+/=]+(\s.*)?\Z"
)
SSHD_CONFIG = "/etc/ssh/sshd_config"
DROPIN_DIR = "/etc/ssh/sshd_config.d"
DROPIN = DROPIN_DIR + "/00-dno-tool-root.conf"
DENYUSERS = "/etc/ssh/denyusers"
messages = []
warnings = []


def finish(ok, error=""):
    print(json.dumps(
        {"ok": ok, "messages": messages, "warnings": warnings, "error": error},
        ensure_ascii=False,
    ))
    sys.stdout.flush()
    os._exit(0)


def run(args, data=None, error=None):
    try:
        result = subprocess.run(
            args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True,
        )
    except OSError:
        finish(False, error or ("Не удалось запустить " + args[0]))
    if result.returncode != 0:
        # Вывод chpasswd не показывается: в нём может быть строка ввода.
        detail = "" if error else " " + (result.stderr or result.stdout).strip()[:300]
        finish(False, (error or ("Ошибка " + " ".join(args[:2]) + ":")) + detail)
    return result.stdout


def restorecon(path):
    if shutil.which("restorecon"):
        subprocess.run(
            ["restorecon", "-R", path], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def replace_text(path, text, mode=0o600, uid=0, gid=0):
    # Атомарная замена с правами и владельцем прежнего файла (если он был).
    try:
        status = os.stat(path)
        mode, uid, gid = status.st_mode & 0o7777, status.st_uid, status.st_gid
    except FileNotFoundError:
        pass
    directory = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(prefix=".dno-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, uid, gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    restorecon(path)


def read_text(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as stream:
        return stream.read()


def single_line(value):
    return isinstance(value, str) and value and not any(c in value for c in "\0\n\r")


def uid_min():
    try:
        for line in read_text("/etc/login.defs").splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] == "UID_MIN":
                return int(fields[1])
    except (OSError, ValueError):
        pass
    return 1000


def sshd_binary():
    for candidate in (shutil.which("sshd"), "/usr/sbin/sshd"):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def sshd_effective(sshd):
    try:
        result = subprocess.run(
            [sshd, "-T"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    values = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        values.setdefault(key.lower(), []).append(value.strip())
    return values


def permit_matches(values, wanted):
    current = (values.get("permitrootlogin") or [""])[0]
    if wanted == "prohibit-password":
        return current in ("prohibit-password", "without-password")
    return current == wanted


def edit_sshd_config(text, permit):
    # Глобальная часть - до первого Match: там PermitRootLogin заменяется
    # одной строкой, из DenyUsers убирается root.
    lines = text.splitlines(True)
    end = len(lines)
    for index, line in enumerate(lines):
        if re.match(r"\s*match\s", line, re.I):
            end = index
            break
    result, placed = [], False
    for index, line in enumerate(lines):
        if index < end and re.match(r"\s*permitrootlogin\b", line, re.I):
            if not placed:
                result.append("PermitRootLogin " + permit + "\n")
                placed = True
            continue
        if index < end and re.match(r"\s*denyusers\b", line, re.I):
            fields = line.split()
            users = [user for user in fields[1:] if user != "root"]
            if users:
                result.append(fields[0] + " " + " ".join(users) + "\n")
            continue
        if index == end and not placed:
            result.append("PermitRootLogin " + permit + "\n")
            placed = True
        result.append(line)
    if not placed:
        if result and not result[-1].endswith("\n"):
            result[-1] += "\n"
        result.append("PermitRootLogin " + permit + "\n")
    return "".join(result)


try:
    data = json.loads(sys.stdin.read())
    accounts = [
        (str(item["login"]), item["password"], bool(item["admin"]))
        for item in data["accounts"]
    ]
    root_password = data["root_password"]
    root_keys = [str(key).strip() for key in data["root_keys"]]
    permit = str(data.get("permit_root") or "")
    # Учётная запись входа, у которой отзываются права администратора.
    revoke = str(data.get("revoke_admin") or "")
except (ValueError, KeyError, TypeError):
    finish(False, "Не удалось прочитать параметры подготовки")

if os.geteuid() != 0:
    finish(False, "Скрипт подготовки запущен не от root")

# ---- проверки до изменений ----
for tool in ("useradd", "usermod", "chpasswd"):
    if shutil.which(tool) is None:
        finish(False, "На хосте нет программы " + tool)
admin_group = None
if any(admin for _, _, admin in accounts):
    for name in ("wheel", "sudo"):
        try:
            grp.getgrnam(name)
            admin_group = name
            break
        except KeyError:
            pass
    if admin_group is None:
        finish(False, "На хосте нет группы wheel или sudo для прав администратора")
minimum = uid_min()
existing = set()
for login, password, _ in accounts:
    if not LOGIN_RE.match(login) or login == "root":
        finish(False, "Недопустимое имя учётной записи «" + login + "»")
    if not single_line(password):
        finish(False, "Недопустимый пароль учётной записи «" + login + "»")
    try:
        entry = pwd.getpwnam(login)
    except KeyError:
        continue
    if entry.pw_uid < minimum:
        finish(False, "«" + login + "» - системная учётная запись (UID "
               + str(entry.pw_uid) + "), её пароль не меняется")
    existing.add(login)
if root_password and not single_line(root_password):
    finish(False, "Недопустимый пароль root")
for key in root_keys:
    if not KEY_RE.match(key):
        finish(False, "Строка не похожа на открытый SSH-ключ")
if permit not in ("", "prohibit-password", "yes"):
    finish(False, "Неизвестный режим входа root")
sshd = sshd_binary()
if permit and (sshd is None or not os.path.isfile(SSHD_CONFIG)):
    finish(False, "Не найден sshd или " + SSHD_CONFIG)
if revoke:
    if not LOGIN_RE.match(revoke) or revoke == "root":
        finish(False, "Недопустимая учётная запись для отзыва прав «" + revoke + "»")
    if revoke in [login for login, _, admin in accounts if admin]:
        finish(False, "«" + revoke + "» указана и для выдачи, и для отзыва прав sudo")
    try:
        revoke_entry = pwd.getpwnam(revoke)
    except KeyError:
        finish(False, "Учётной записи «" + revoke + "» для отзыва прав на хосте нет")
    if shutil.which("gpasswd") is None:
        finish(False, "На хосте нет программы gpasswd")

# ---- учётные записи и пароли ----
for login, _, admin in accounts:
    if login in existing:
        messages.append("Учётная запись «" + login + "» уже есть, пароль заменён")
    else:
        run(["useradd", "-m", login])
        messages.append("Создана учётная запись «" + login + "»")
    if admin:
        run(["usermod", "-aG", admin_group, login])
        messages.append("«" + login + "» добавлена в группу " + admin_group)
# Пустой пароль root - не менять.
lines = "".join(login + ":" + password + "\n" for login, password, _ in accounts)
if root_password:
    lines += "root:" + root_password + "\n"
if lines:
    run(["chpasswd"], lines, error="Не удалось задать пароли (chpasswd)")
if accounts:
    messages.append("Пароли учётных записей заданы")
if root_password:
    messages.append("Пароль root изменён")

# ---- ключи root ----
if root_keys:
    home = pwd.getpwnam("root").pw_dir or "/root"
    directory = os.path.join(home, ".ssh")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chown(directory, 0, 0)
    os.chmod(directory, 0o700)
    target = os.path.join(directory, "authorized_keys")
    old = read_text(target) if os.path.exists(target) else ""
    present = set()
    for line in old.splitlines():
        fields = line.split()
        for index in range(len(fields) - 1):
            present.add(tuple(fields[index:index + 2]))
    added = []
    for key in root_keys:
        identity = tuple(key.split()[:2])
        if identity not in present:
            present.add(identity)
            added.append(key)
    if added:
        text = old + ("\n" if old and not old.endswith("\n") else "")
        replace_text(target, text + "".join(key + "\n" for key in added))
    os.chown(target, 0, 0)
    os.chmod(target, 0o600)
    restorecon(directory)
    messages.append(
        "Ключи root: добавлено " + str(len(added)) + ", уже были "
        + str(len(root_keys) - len(added))
    )

def denyusers_has_root():
    if not os.path.isfile(DENYUSERS):
        return False
    return any(line.strip() == "root" for line in read_text(DENYUSERS).splitlines())


# ---- PermitRootLogin и /etc/ssh/denyusers (только с разрешением входа root) ----
if permit:
    original = read_text(SSHD_CONFIG)
    dropin_existed = os.path.exists(DROPIN)
    dropin_original = read_text(DROPIN) if dropin_existed else None

    def restore():
        replace_text(SSHD_CONFIG, original)
        if dropin_existed:
            replace_text(DROPIN, dropin_original, 0o600)
        elif os.path.exists(DROPIN):
            os.unlink(DROPIN)

    updated = edit_sshd_config(original, permit)
    if updated != original:
        replace_text(SSHD_CONFIG, updated)
    values = sshd_effective(sshd)
    if values is not None and not permit_matches(values, permit) and os.path.isdir(DROPIN_DIR):
        # Значение из включаемого каталога перекрывает основной файл
        # (действует первое): файл 00-... читается раньше остальных.
        replace_text(DROPIN, "PermitRootLogin " + permit + "\n", 0o600)
        values = sshd_effective(sshd)
    if values is None:
        restore()
        finish(False, "Проверка sshd -t не пройдена, sshd_config возвращён")
    if not permit_matches(values, permit):
        restore()
        finish(False, "Не удалось изменить PermitRootLogin (перекрыт другой настройкой), sshd_config возвращён")
    reloaded = False
    for unit in ("sshd", "ssh"):
        if subprocess.run(
            ["systemctl", "reload", unit], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0:
            reloaded = True
            break
    if not reloaded:
        finish(False, "sshd_config изменён, но перезагрузить sshd не удалось")
    messages.append("Вход root по SSH: PermitRootLogin " + permit)
    # denyusers - после успешной смены sshd_config: при откате выше файл
    # остаётся нетронутым.
    if os.path.isfile(DENYUSERS):
        old = read_text(DENYUSERS)
        kept = [line for line in old.splitlines(True) if line.strip() != "root"]
        if len(kept) != len(old.splitlines(True)):
            replace_text(DENYUSERS, "".join(kept))
            messages.append("root убран из " + DENYUSERS)
        else:
            messages.append("В " + DENYUSERS + " root не указан")
    else:
        messages.append("Файла " + DENYUSERS + " нет")
elif denyusers_has_root():
    warnings.append("root указан в " + DENYUSERS + ": вход root по SSH закрыт")

# ---- отзыв прав администратора (последним: подготовка идёт через sudo этой записи) ----
if revoke:
    removed = []
    for name in ("wheel", "sudo"):
        try:
            group = grp.getgrnam(name)
        except KeyError:
            continue
        if group.gr_gid == revoke_entry.pw_gid:
            warnings.append(
                "Группа " + name + " - основная у «" + revoke + "», исключить из неё нельзя"
            )
        elif revoke in group.gr_mem:
            run(["gpasswd", "-d", revoke, name])
            removed.append(name)
    if removed:
        messages.append(
            "Права администратора у «" + revoke + "» отозваны: исключена из групп "
            + ", ".join(removed)
        )
    else:
        messages.append("«" + revoke + "» не состоит в группах wheel и sudo")
    # Права могут быть выданы и записью в /etc/sudoers или /etc/sudoers.d.
    if shutil.which("sudo"):
        check = subprocess.run(
            ["sudo", "-n", "-l", "-U", revoke], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, universal_newlines=True,
            env=dict(os.environ, LC_ALL="C", LANG="C"),
        )
        if check.returncode == 0 and "may run the following" in check.stdout:
            warnings.append(
                "У «" + revoke + "» остались права sudo из /etc/sudoers или "
                "/etc/sudoers.d: проверьте их вручную"
            )

# ---- предупреждения о настройках sshd, которые не менялись ----
values = sshd_effective(sshd) if sshd else None
if values is not None:
    if (values.get("permitrootlogin") or [""])[0] == "no":
        warnings.append("Вход root по SSH запрещён (PermitRootLogin no)")
    denied = " ".join(values.get("denyusers") or []).replace(",", " ").split()
    if "root" in denied:
        warnings.append("root указан в DenyUsers")
    allowed = " ".join(values.get("allowusers") or []).replace(",", " ").split()
    if allowed and "root" not in allowed:
        warnings.append("Задан AllowUsers без root: вход root по SSH закрыт")
finish(True)
"""
