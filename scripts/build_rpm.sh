#!/usr/bin/env bash
# build_rpm.sh — сборка RPM-пакета dnotool для ROSA / МОС Linux
# Запуск из корня проекта: bash scripts/build_rpm.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PACKAGING_DIR="${PROJECT_ROOT}/packaging"

INIT_FILE="${PROJECT_ROOT}/src/__init__.py"
if [ ! -f "${INIT_FILE}" ]; then
    echo "Ошибка: файл не найден: ${INIT_FILE}"
    echo "PROJECT_ROOT=${PROJECT_ROOT}"
    echo "SCRIPT_DIR=${SCRIPT_DIR}"
    ls -la "${PROJECT_ROOT}/" 2>/dev/null || echo "(каталог не найден)"
    exit 1
fi

VERSION=$(grep -m1 '__version__.*=.*"' "${INIT_FILE}" | cut -d'"' -f2)
if [ -z "${VERSION}" ]; then
    VERSION=$(grep -m1 "__version__.*=.*'" "${INIT_FILE}" | cut -d"'" -f2)
fi

if [ -z "${VERSION}" ]; then
    echo "Ошибка: не удалось определить версию из src/__init__.py"
    exit 1
fi

echo "=== Сборка RPM-пакета dnotool-${VERSION} ==="

# ── 0. Проверка и установка зависимостей ──────────────────────────────────
RPM_DEPS=(rpm-build python3)
DNF_DEPS=(rpm-build python3 imagemagick gtk3-devel libX11-devel libXrandr-devel \
           libXi-devel libXinerama-devel mesa-libGL-devel libxcb-devel libglvnd-devel)

NEED_INSTALL=()
for i in "${!RPM_DEPS[@]}"; do
    if ! rpm -q "${RPM_DEPS[$i]}" &>/dev/null; then
        NEED_INSTALL+=("${DNF_DEPS[$i]}")
    fi
done

if [ ${#NEED_INSTALL[@]} -gt 0 ]; then
    echo "Отсутствуют пакеты: ${NEED_INSTALL[*]}"
    echo -n "Установить? [Y/n] "
    read -r answer
    case "${answer}" in
        [nN]*) echo "Установка отменена. Выход."; exit 1 ;;
    esac
    sudo dnf install -y "${NEED_INSTALL[@]}"
fi

# ── 0.5. Проверка наличия необходимых файлов проекта ─────────────────────
POLICYKIT_DIR="${PACKAGING_DIR}/policykit"
if [ ! -d "${POLICYKIT_DIR}" ]; then
    echo "Ошибка: каталог policykit не найден: ${POLICYKIT_DIR}"
    exit 1
fi

for f in com.dnotool.policy com.dnotool.desktop dnotool-admin; do
    if [ ! -f "${POLICYKIT_DIR}/${f}" ]; then
        echo "Ошибка: файл не найден: ${POLICYKIT_DIR}/${f}"
        exit 1
    fi
done

# ── 1. Собираем бинарный файл через PyInstaller ──────────────────────────
echo "--- Шаг 1: Сборка бинарного файла ---"
cd "${PROJECT_ROOT}"

if [ ! -d ".venv" ]; then
    echo "Создание виртуального окружения..."
    python3 -m venv .venv
fi

source ./.venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
pip install pyinstaller -q
pyinstaller dnotool.spec
deactivate

BINARY="${PROJECT_ROOT}/dist/dnotool"
if [ ! -f "${BINARY}" ]; then
    BINARY="${PROJECT_ROOT}/dist/dno-tool"
fi
if [ ! -f "${BINARY}" ]; then
    echo "Ошибка: бинарный файл не найден в dist/"
    exit 1
fi
echo "Бинарный файл: ${BINARY}"

# ── 2. Подготавливаем иконки ─────────────────────────────────────────────
echo "--- Шаг 2: Подготовка иконок ---"
ICON_DIR="${PROJECT_ROOT}/rpmbuild/_icons"
mkdir -p "${ICON_DIR}"

HAS_PNG=false
if command -v convert &>/dev/null; then
    convert "${PROJECT_ROOT}/assets/favicon.ico[256x256]" "${ICON_DIR}/dnotool.png"
    HAS_PNG=true
    echo "Иконка PNG создана из favicon.ico"
else
    echo "Предупреждение: ImageMagick не установлен, иконка PNG не создана"
fi

SVG_SRC="${PROJECT_ROOT}/assets/default.svg"
HAS_SVG=true
if [ ! -f "${SVG_SRC}" ]; then
    echo "Предупреждение: SVG иконка не найдена (${SVG_SRC})"
    HAS_SVG=false
fi

# ── 3. Формируем исходный архив для rpmbuild ────────────────────────────
echo "--- Шаг 3: Подготовка исходного архива ---"
RPMBUILD="${PROJECT_ROOT}/rpmbuild"
SOURCES="${RPMBUILD}/SOURCES/dnotool-${VERSION}"

rm -rf "${SOURCES}"
mkdir -p "${SOURCES}/policykit" "${SOURCES}/icon"

cp "${BINARY}" "${SOURCES}/dnotool"
chmod +x "${SOURCES}/dnotool"

cp "${POLICYKIT_DIR}/com.dnotool.policy" "${SOURCES}/policykit/"
cp "${POLICYKIT_DIR}/com.dnotool.desktop" "${SOURCES}/policykit/"
cp "${POLICYKIT_DIR}/dnotool-admin" "${SOURCES}/policykit/"

if ${HAS_PNG}; then
    cp "${ICON_DIR}/dnotool.png" "${SOURCES}/icon/"
fi
if ${HAS_SVG}; then
    cp "${SVG_SRC}" "${SOURCES}/icon/dnotool.svg"
fi

# ── 4. Создаём tar.gz ────────────────────────────────────────────────────
echo "--- Шаг 4: Создание tar.gz архива ---"
cd "${RPMBUILD}/SOURCES"
rm -f "dnotool-${VERSION}.tar.gz"
tar -czf "dnotool-${VERSION}.tar.gz" "dnotool-${VERSION}"

# ── 5. Копируем и обновляем spec-файл ────────────────────────────────────
echo "--- Шаг 5: Подготовка spec-файла ---"
mkdir -p "${RPMBUILD}/SPECS"
cp "${PACKAGING_DIR}/rpm/dnotool.spec" "${RPMBUILD}/SPECS/dnotool.spec"
sed -i "s/^Version:.*/Version:        ${VERSION}/" "${RPMBUILD}/SPECS/dnotool.spec"

# Убираем строки с иконками из spec, если файлов нет
if ! ${HAS_PNG}; then
    sed -i '/icon\/dnotool.png/d' "${RPMBUILD}/SPECS/dnotool.spec"
fi
if ! ${HAS_SVG}; then
    sed -i '/icon\/dnotool.svg/d' "${RPMBUILD}/SPECS/dnotool.spec"
fi

# ── 6. Собираем RPM ──────────────────────────────────────────────────────
echo "--- Шаг 6: Сборка RPM-пакета ---"
mkdir -p "${RPMBUILD}"/{BUILD,RPMS,SRPMS}
rm -f "${RPMBUILD}"/RPMS/x86_64/dnotool-*.rpm

# Копируем rpmlintrc чтобы rpmlint не валил на предсобранный бинарник
if [ -f "${PACKAGING_DIR}/rpm/dnotool.rpmlintrc" ]; then
    cp "${PACKAGING_DIR}/rpm/dnotool.rpmlintrc" "${RPMBUILD}/SOURCES/dnotool.rpmlintrc"
fi

rpmbuild -bb "${RPMBUILD}/SPECS/dnotool.spec" \
    --define "_topdir ${RPMBUILD}" \
    --define "_unpackaged_files_terminate_build 0"

RPM_FILE=$(find "${RPMBUILD}/RPMS" -name "dnotool-${VERSION}-*.rpm" -type f 2>/dev/null | head -1)
if [ -z "${RPM_FILE}" ]; then
    echo "Ошибка: RPM-пакет не найден после сборки!"
    ls -laR "${RPMBUILD}/RPMS/"
    exit 1
fi

mkdir -p "${PROJECT_ROOT}/dist"
cp "${RPM_FILE}" "${PROJECT_ROOT}/dist/"

echo ""
echo "============================================"
echo "  RPM-пакет собран успешно!"
echo "============================================"
echo "  Пакет:    ${RPM_FILE}"
echo "  Скопирован: ${PROJECT_ROOT}/dist/$(basename "${RPM_FILE}")"
echo ""
echo "  Установка:"
echo "    sudo dnf install ${PROJECT_ROOT}/dist/$(basename "${RPM_FILE}")"
echo ""
echo "  Проверка содержимого:"
echo "    rpm -qlp ${PROJECT_ROOT}/dist/$(basename "${RPM_FILE}")"
echo ""