"""Setup results, parsers and installer registry."""

from dataclasses import dataclass, field
import re


# Учётная запись Linux: как NAME_REGEX useradd по умолчанию, без «$» на конце.
PREPARE_LOGIN_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
# Открытый ключ OpenSSH одной строкой: тип, base64, необязательный комментарий.
PREPARE_KEY_RE = re.compile(
    r"(ssh-[a-z0-9-]+|ecdsa-sha2-[a-z0-9]+|sk-[a-z0-9@.-]+)\s+AAAA[A-Za-z0-9+/=]+(\s.*)?"
)
PERMIT_ROOT_MODES = ("", "prohibit-password", "yes")


@dataclass(frozen=True)
class PrepareAccount:
    """Учётная запись, которую подготовка создаёт на хосте."""

    login: str
    password: str = field(default="", repr=False)
    admin: bool = False


@dataclass(frozen=True)
class PreparePlan:
    """Параметры подготовки заводских устройств (шаг «Подготовка»).

    connect_bundle_iid - связка для входа при подготовке (заводская учётка с sudo);
    after_bundle_iid - связка, которую получают записи подготовленных хостов
    и под которой выполняются остальные сервисы того же прогона.
    Пустой iid связки - наследовать глобальную (связку по умолчанию); записи
    хостов тогда остаются без своей связки.
    root_password: "" - пароль root не менять.
    root_keys - открытые ключи для root, выбранные на шаге «Подготовка».
    permit_root: "" - не менять sshd_config, иначе значение PermitRootLogin.
    revoke_admin - исключить учётную запись входа для подготовки из групп
    wheel и sudo (последним шагом подготовки).
    """

    connect_bundle_iid: str = ""
    accounts: tuple = ()
    root_password: str = field(default="", repr=False)
    root_keys: tuple = ()
    permit_root: str = ""
    after_bundle_iid: str = ""
    revoke_admin: bool = False

    def __post_init__(self):
        object.__setattr__(
            self,
            "accounts",
            tuple(
                item if isinstance(item, PrepareAccount) else PrepareAccount(*item)
                for item in self.accounts
            ),
        )
        object.__setattr__(self, "root_keys", tuple(self.root_keys))


def _check_secret(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label}: пароль не может быть пустым")
    if any(char in value for char in ("\0", "\n", "\r")) or len(value) > 4096:
        raise ValueError(f"{label}: пароль должен быть одной строкой")


def validate_prepare(plan, bundles=None, default_iid=""):
    """Проверить параметры подготовки; ValueError с понятной причиной.

    bundles - {iid: (login, password)}; без него связки не проверяются
    (worker проверяет их при подключении). default_iid - глобальная связка,
    которой заменяется пустой iid («наследовать глобальную»).
    """
    if not isinstance(plan, PreparePlan):
        raise ValueError("Параметры подготовки не заданы")
    seen = set()
    for account in plan.accounts:
        login = account.login
        if not isinstance(login, str) or not PREPARE_LOGIN_RE.fullmatch(login):
            raise ValueError(
                f"Недопустимое имя учётной записи «{login}»: строчные латинские "
                "буквы, цифры, _ и -, не больше 32 символов"
            )
        if login == "root":
            raise ValueError("Учётная запись root уже есть: её пароль задаётся отдельно")
        if login in seen:
            raise ValueError(f"Учётная запись «{login}» указана дважды")
        seen.add(login)
        _check_secret(account.password, f"Учётная запись «{login}»")
        if type(account.admin) is not bool:
            raise ValueError("Некорректный признак sudo")
    # Пустой пароль root - не менять.
    if plan.root_password:
        _check_secret(plan.root_password, "root")
    for key in plan.root_keys:
        if not isinstance(key, str) or "\n" in key or not PREPARE_KEY_RE.fullmatch(key):
            raise ValueError("Выбранный ключ не похож на открытый SSH-ключ")
    # Пустой набор допустим: подготовка проверяет вход, и записи хостов
    # получают связку для подготовленных.
    if plan.permit_root not in PERMIT_ROOT_MODES:
        raise ValueError("Неизвестный режим входа root")
    if type(plan.revoke_admin) is not bool:
        raise ValueError("Некорректный признак отзыва прав")
    if bundles is None:
        return
    if not (default_iid and default_iid in bundles):
        fields = [
            label
            for label, iid in (
                ("«Вход для подготовки»", plan.connect_bundle_iid),
                ("«Связка для записей хостов»", plan.after_bundle_iid),
            )
            if not iid
        ]
        if fields:
            raise ValueError(
                f"{' и '.join(fields)}: выбрано «наследовать глобальную», но связка "
                "по умолчанию не задана. Выберите связку в списке или задайте связку "
                "по умолчанию на шаге «SSH-ключи»."
            )
    connect = bundles.get(plan.connect_bundle_iid or default_iid)
    if connect is None:
        raise ValueError("Выберите связку для входа при подготовке")
    if not connect[0]:
        raise ValueError("В связке для входа при подготовке не указан логин")
    # Пароль не обязателен: вход может быть по ключу, sudo - без пароля
    # (NOPASSWD). Если sudo его спросит, установка сообщит об этом.
    after = bundles.get(plan.after_bundle_iid or default_iid)
    if after is None:
        raise ValueError("Выберите связку для записей хостов после подготовки")
    after_login = after[0] or "root"
    for account in plan.accounts:
        if account.login == after_login and not account.admin:
            raise ValueError(
                f"Связка после подготовки входит под «{after_login}», но этой учётной "
                "записи не выбраны права sudo: установка сервисов под ней невозможна"
            )
    if plan.revoke_admin:
        login = connect[0]
        if login == "root":
            raise ValueError(
                "Вход для подготовки идёт под root: отзывать права администратора не у кого"
            )
        if after_login == login:
            raise ValueError(
                f"Связка для записей хостов входит под «{login}», у которой отзываются "
                "права администратора: остальные сервисы ставить будет нечем. "
                "Выберите другую связку или снимите галочку отзыва прав"
            )
        if any(a.login == login and a.admin for a in plan.accounts):
            raise ValueError(
                f"«{login}» есть в учётных записях с флажком sudo, а права у неё "
                "отзываются: оставьте что-то одно"
            )
    if after_login == "root" and not plan.root_keys:
        if plan.permit_root == "prohibit-password":
            raise ValueError(
                "Вход root разрешается только по ключу, но ключ root не добавляется"
            )
        if plan.root_password and after[1] != plan.root_password:
            raise ValueError(
                "Вход под root будет невозможен: пароль связки после подготовки "
                "не совпадает с новым паролем root, а ключ root не добавляется"
            )


def validate_port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError("Порт должен быть целым числом от 1 до 65535")


def validate_service_parameters(service, parameters):
    if service == "prepare":
        validate_prepare(parameters.prepare)
    elif service == "packages":
        from src.workers.setup.packages import validate_package_names

        validate_package_names(parameters.extra_packages)
    elif service == "ssh":
        validate_port(parameters.ssh_port)
    elif service == "veyon":
        validate_port(parameters.veyon_server_port)
        if not re.fullmatch(
            r"[A-Za-z0-9_][A-Za-z0-9_.\-]{0,127}", parameters.veyon_key_name or ""
        ):
            raise ValueError("Недопустимое имя ключа Veyon")
        if parameters.veyon_access_group is not None and not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_.\-]*\$?", parameters.veyon_access_group
        ):
            raise ValueError("Недопустимая группа доступа Veyon")
    elif service == "vnc":
        validate_port(parameters.vnc_port)
        from src.config.settings import VNC_SERVERS

        if parameters.vnc_server not in VNC_SERVERS:
            raise ValueError("Неизвестный VNC backend")
        if any(c in parameters.vnc_password for c in ("\0", "\n", "\r")):
            raise ValueError("Пароль VNC должен быть одной строкой без нулевых байтов")
        if parameters.vnc_server == "krfb" and not parameters.vnc_password:
            raise ValueError("Для автоматического доступа KRFB требуется пароль")
        if parameters.vnc_server == "kmsvnc" and not parameters.vnc_password:
            raise ValueError("Для kmsvnc требуется пароль")


RESULT_SUCCESS: int = 0


RESULT_ERROR: int = 1


RESULT_IGNORE: int = 2


RESULT_ABORT: int = 3


RESULT_WAITING: int = 4


def parse_dnf_available(output: str, wanted) -> set:
    """Выделить из `dnf list available` имена найденных пакетов.

    Строки вида `mos-auth-core.x86_64  1.0  repo` - имя до последней точки
    первого токена. Строки ошибок пропускаются. Чистая функция (тестируема).
    """
    found = set()
    wanted = set(wanted)
    for line in (output or "").splitlines():
        line = line.strip()
        if not line:
            continue
        first = line.split()[0]
        if first.startswith("Error") or first.startswith("Failed"):
            continue
        name = first.rsplit(".", 1)[0] if "." in first else first
        if name in wanted:
            found.add(name)
    return found


def parse_apt_candidate(output: str) -> bool:
    """True, если `apt-cache policy <pkg>` показывает кандидата на установку."""
    for line in (output or "").splitlines():
        s = line.strip()
        if s.startswith("Candidate:"):
            return s.split(":", 1)[1].strip() not in ("(none)", "")
    return False


@dataclass(frozen=True)
class InstallerDescriptor:
    key: str
    label: str
    implementation: str
    capabilities: tuple[str, ...] = ()
    reversible: bool = False
    parameters: tuple = ()
    # Галочка сервиса на первом шаге мастера при открытии.
    enabled_by_default: bool = True
    # Подпись галочки; пусто - «<label> — установка и настройка».
    intro_text: str = ""

    def __post_init__(self):
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(
            self, "parameters", tuple(tuple(item) for item in self.parameters)
        )

    def validate(self, parameters):
        validate_service_parameters(self.key, parameters)

    def build_parameters(self, values):
        """Validate declared extension options and freeze their plan representation."""
        if set(values) - {name for name, _, _ in self.parameters}:
            raise ValueError("Неизвестные параметры сервиса")
        options = []
        for name, label, default in self.parameters:
            value = values.get(name, default)
            if not isinstance(value, str) or "\0" in value or len(value) > 4096:
                raise ValueError(f"Недопустимое значение: {label}")
            options.append((name, value))
        return tuple(options)


INSTALLERS = [
    # Первым: остальные сервисы хоста выполняются после подготовки и под
    # связкой, выбранной для подготовленных хостов.
    InstallerDescriptor(
        "prepare",
        "Подготовка",
        "src.workers.setup.installers.prepare:PrepareInstaller",
        ("accounts", "root_access"),
        intro_text=(
            "Подготовка — подготовка заводских устройств "
            "(учётные записи, пароль и ключи root)"
        ),
    ),
    InstallerDescriptor(
        "ssh",
        "SSH",
        "src.workers.setup.installers.ssh:SshInstaller",
        ("key_deployment", "key_login"),
    ),
    InstallerDescriptor(
        "packages",
        "Пакеты",
        "src.workers.setup.installers.packages:PackagesInstaller",
        ("packages", "post_install"),
    ),
    InstallerDescriptor(
        "veyon",
        "Veyon",
        "src.workers.setup.installers.veyon:VeyonInstaller",
        ("key_auth", "listener", "operator_auth"),
    ),
    InstallerDescriptor(
        "vnc",
        "VNC",
        "src.workers.setup.installers.vnc:VncInstaller",
        ("x11vnc", "krfb", "kmsvnc", "rfb_probe"),
        True,
    ),
]


def register_installer(descriptor):
    if any(item.key == descriptor.key for item in INSTALLERS):
        raise ValueError("Сервис уже зарегистрирован")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", descriptor.key):
        raise ValueError("Недопустимый ключ сервиса")
    INSTALLERS.append(descriptor)


SERVICE_ORDER = tuple(item.key for item in INSTALLERS)
