"""Independent installer using the operation context supplied by the worker."""

from src.workers.setup.templates import (
    _as_user_func,
)
from src.workers.setup.context import InstallerContext


class VncOperations(InstallerContext):
    def _open_firewall_v4(self, run, port, log):
        from src.workers.setup.installers.veyon import VeyonInstaller

        return VeyonInstaller(self.context)._open_firewall_v4(run, port, log)

    def _disable_stock_krfb_unit(self, run_cmd, log) -> None:
        """Погасить дистрибутивный user unit krfb.service (конкурент krfb-dno).

        Штатный юнит запускает ``krfb`` БЕЗ ``--nodialog``. При живом нашем
        krfb-dno второй процесс через KDBusService::Unique пересылает
        activateRequested первому - и тот показывает окно "Совместный
        доступ". Поэтому перед включением krfb-dno: --global disable +
        stop/disable в user-менеджерах всех /home/* (best-effort, ошибку
        цикла в лог, не фатально - pkill ниже добьёт процесс).
        """
        run_cmd(
            "systemctl --global disable krfb.service 2>/dev/null; "
            "rm -f /etc/systemd/user/graphical-session.target.wants/krfb.service; "
            "true",
            timeout=30,
        )
        stop_cmd = (
            _as_user_func() + "; for h in /home/*/; do u=$(basename $h); "
            'dnoasuser "$u" bash -c '
            "'systemctl --user stop krfb.service 2>/dev/null; "
            "systemctl --user disable krfb.service 2>/dev/null; true' "
            "2>/dev/null; done; true"
        )
        code, out, err = run_cmd(stop_cmd, timeout=60)
        if code != 0:
            log(
                "krfb: не удалось остановить штатные user-юниты krfb.service "
                f"({err or out or '?'}), продолжаю"
            )

    def _remove_other_backends(self, run_cmd, keep: str, log) -> None:
        """Удалить VNC-серверы, кроме ``keep``, после успешной проверки нового.

        До проверки прежние серверы только остановлены: при сбое нового
        откат вернёт их. После успеха удаляются службы, пароли и настройки
        мастера и пакеты x11vnc/kmsvnc (``rpm -e``: пакет, нужный другим,
        остаётся). Пакет KRFB входит в состав МОС и не удаляется, снимаются
        служба мастера и пароль автоматического доступа. Ошибки удаления
        установку не проваливают.
        """
        packaged = {
            "x11vnc": (
                "/etc/systemd/system/x11vnc.service /usr/local/bin/x11vnc-dno-tool.sh "
                "/var/log/x11vnc.log",
                "/etc/x11vnc",
            ),
            "kmsvnc": ("/etc/systemd/system/kmsvnc.service", "/etc/kmsvnc"),
        }
        for name, (files, directory) in packaged.items():
            if name == keep:
                continue
            code, out, _ = run_cmd(
                f"present=; for p in {files} {directory}; do [ -e \"$p\" ] && present=1; done; "
                f"rpm -q {name} >/dev/null 2>&1 && present=1; "
                f"dpkg -s {name} >/dev/null 2>&1 && present=1; "
                f'[ -n "$present" ] || {{ echo ABSENT; exit 0; }}; '
                f"systemctl disable --now {name}.service >/dev/null 2>&1; "
                f"rm -f -- {files}; rm -rf -- {directory}; systemctl daemon-reload; "
                f"if rpm -q {name} >/dev/null 2>&1; then "
                f"rpm -e {name} >/dev/null 2>&1 && echo PKG_REMOVED || echo PKG_KEPT; "
                f"elif dpkg -s {name} >/dev/null 2>&1; then "
                f"apt-get -y -q remove {name} >/dev/null 2>&1 && echo PKG_REMOVED || echo PKG_KEPT; "
                f"fi; echo CLEANED",
                timeout=120,
            )
            out = out or ""
            if "ABSENT" in out:
                continue
            if code != 0 or "CLEANED" not in out:
                log(f"WARNING: {name} удалён не полностью")
            elif "PKG_KEPT" in out:
                log(f"{name}: служба и настройки удалены, пакет оставлен (нужен другим пакетам)")
            else:
                log(f"Прежний VNC-сервер {name} удалён")
        if keep != "krfb":
            self._remove_krfb_setup(run_cmd, log)

    def _remove_krfb_setup(self, run_cmd, log) -> None:
        """Убрать службу KRFB мастера и пароль автоматического доступа."""
        code, out, _ = run_cmd(
            "ls /etc/systemd/user/krfb-dno.service /etc/systemd/user/app-org.kde.krfb.service "
            "/home/*/.config/systemd/user/krfb-dno.service >/dev/null 2>&1 && echo PRESENT; "
            "grep -ls unattendedPassword /home/*/.config/krfbrc >/dev/null 2>&1 && echo PRESENT; "
            "true",
            timeout=15,
        )
        if "PRESENT" not in (out or ""):
            return
        script = (
            _as_user_func() + "; "
            "rm -f /etc/systemd/user/krfb-dno.service /etc/systemd/user/app-org.kde.krfb.service "
            "/home/*/.config/systemd/user/krfb-dno.service; "
            "for h in /home/*/; do u=$(basename $h); "
            '[ -f "$h.config/krfbrc" ] || continue; '
            'dnoasuser "$u" bash -c '
            "'K=$(command -v kwriteconfig6 || command -v kwriteconfig5) || exit 0; "
            '"$K" --file "$HOME/.config/krfbrc" --group Security --key unattendedPassword --delete; '
            '"$K" --file "$HOME/.config/krfbrc" --group Security --key allowUnattendedAccess --type bool false; '
            "systemctl --user daemon-reload' 2>/dev/null; done; "
            "systemctl daemon-reload; echo CLEANED"
        )
        code, out, _ = run_cmd(script, timeout=60)
        if code != 0 or "CLEANED" not in (out or ""):
            log("WARNING: служба KRFB удалена не полностью")
        else:
            log(
                "Служба KRFB мастера и пароль автоматического доступа удалены "
                "(сам KRFB входит в МОС и остаётся)"
            )
