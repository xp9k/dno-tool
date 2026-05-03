import uuid
from ...config import Config, ICONS

class DeviceModel(object):
    def __init__(self, data: dict = None):
        self._iid = uuid.uuid4().hex
        self.name = data.get("name", "")
        self.host = data.get("host", "")
        self.port = data.get("port", None)
        self._icon_offline = data.get("icon", ICONS['offline'])
        self._icon_online = data.get("icon", ICONS['online'])
        self.login = data.get("login", None)
        self.password = data.get("password", None)
        self.mac_address = data.get("mac_address", None)
        
        # Инициализируем как offline по умолчанию, но с иконкой default
        self._is_online = False
        self.icon = ICONS['default']

    @property
    def is_online(self) -> bool:
        """Проверка статуса онлайн"""
        return self._is_online

    def set_online(self, online: bool = False):
        """Установить статус онлайн/офлайн"""
        self._is_online = online
        if online:
            self.icon = self._icon_online
        else:
            self.icon = self._icon_offline

    def update(self, data: dict = None):
        if data is None:
            return

        # If object provides to_dict(), prefer that
        if hasattr(data, "to_dict") and not isinstance(data, dict):
            try:
                data = data.to_dict()
            except Exception:
                pass

        # If we now have a dict, use .get as before
        if isinstance(data, dict):
            self.name = data.get("name", self.name)
            self.host = data.get("host", self.host)
            self.port = data.get("port", self.port)
            self._icon_offline = data.get("icon_offline", self._icon_offline)
            self._icon_online = data.get("icon_online", self._icon_online)
            self.login = data.get("login", self.login)
            self.password = data.get("password", self.password)
            self.mac_address = data.get("mac_address", self.mac_address)
            return

        # Fallback: copy attributes from the provided object
        self.name = getattr(data, "name", self.name)
        self.host = getattr(data, "host", self.host)
        self.port = getattr(data, "port", self.port)
        self._icon_offline = getattr(data, "_icon_offline", getattr(data, "icon_offline", self._icon_offline))
        self._icon_online = getattr(data, "_icon_online", getattr(data, "icon_online", self._icon_online))
        self.login = getattr(data, "login", self.login)
        self.password = getattr(data, "password", self.password)
        self.mac_address = getattr(data, "mac_address", self.mac_address)

    @property
    def iid(self) -> str:
        return self._iid
    
    
    def __str__(self) -> str:
        return f"{self.name} ({self.host}:{self.port})"
    

    def __repr__(self) -> str:
        return f"{self.name} ({self.host}:{self.port})"
    

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "icon": self.icon,
            "login": self.login,
            "password": self.password,
            "mac_address": self.mac_address,
        }
    
    def export(self) -> dict:
        device =  {
            "name": self.name,
            "host": self.host,
        }
        if self.port and self.port != Config().app.ssh.port:
            device['port'] = self.port
        if self.login:
            device['login'] = self.login
        if self.password:
            device['password'] = self.password
        if self.mac_address:
            device['mac_address'] = self.mac_address

        return device