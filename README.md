# dnotool

Утилита для удалённого администрирования компьютеров под управлением МОС/ALT Linux и Windows по протоколу SSH.

Позволяет выполнять команды сразу на множестве устройств, управлять пакетами, службами, пользователями, сетевыми настройками и ключами доступа — всё из одного окна с графическим интерфейсом.

## Возможности

- **Массовое выполнение команд** — отправка SSH-команд на десятки хостов одновременно
- **Библиотека команд** — более 150 готовых команд: пакеты, службы, пользователи, сеть, безопасность, диагностика
- **Управление ключами** — генерация и автоматическая установка SSH-ключей на хосты
- **SFTP-менеджер** — приём/передача файлов между вашим компьютером и удалёнными хостами
- **Мониторинг** — ping и сканирование портов с визуальным отображением статуса
- **Запись экрана** — захват удалённого рабочего стола через FFmpeg/VLC

## Установка

### МОС / ALT Linux

```bash
bash <(curl -sL -H "Authorization: token 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" -H "Accept: application/vnd.github.v3.raw" https://api.github.com/repos/xp9k/dno-tool/contents/scripts/install.sh)
```

### Windows (PowerShell от имени администратора)

```powershell
$t="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"; Invoke-WebRequest -Uri "https://api.github.com/repos/xp9k/dno-tool/contents/scripts/install.ps1" -Headers @{Authorization="token $t";Accept="application/vnd.github.v3.raw"} -OutFile install.ps1; .\install.ps1; Remove-Item install.ps1
```

## Лицензия

См. файл [LICENSE](LICENSE).