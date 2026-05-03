import urllib.request
import urllib.error
import urllib.request
import urllib.error
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
from PySide6.QtGui import QFont, QCursor
from PySide6.QtCore import Qt, QUrl, QThread, Signal
from PySide6.QtGui import QDesktopServices

from src import __version__

VERSION_URL = "https://syno.xp9k.ru/d/s/17AbafYwPMRUXfKJca9f6VZDYdH7Nygg/webapi/entry.cgi/version.txt?api=SYNO.SynologyDrive.Files&method=download&version=2&files=%5B%22id%3A945772702759696148%22%5D&force_download=false&download_type=%22download%22&sharing_token=%222TkxZFywWx6_IUzv7vd.HZFlIg3F1bLB0i1VAevVI93S74X0ekfOhvaO6Ujtezh4ZUIMINVq2zYOC3SuV7boEKBn3zkMW5Bxc2d0m7QwGAlTeMv79xYm017vdpSs.7YmDBp_KwCy.7BW_HrmnILAC86Qbs1kMk2X2y0OHaEF7ffWLjptCfxOSfgEzvdpGUeUwhHKpIiPR1hb257Uz4.SHt_UH6H8gFP1lPCiQ01D97Nn.RsCNRFKEXDoDQ6wQtg39ax4Ci.2eIfEtG3bxclSHjrpvDJfaNO1esRW2y4Kw2.oJx2DqYystBFk.qYnSGTvllIGyOo-%22&_dc=1776355794976"
UPDATE_URL = "https://syno.xp9k.ru/d/s/17AbafYwPMRUXfKJca9f6VZDYdH7Nygg/P4VP6sAtFMohJRFQ-_1ZjLgQL_qCLa6E-obXgVQySHg0"

def parse_version(version_str):
    """Parse version string into tuple of integers for comparison"""
    try:
        return tuple(map(int, version_str.strip().split('.')))
    except ValueError:
        return (0, 0, 0)

def compare_versions(version1, version2):
    """Compare two version tuples, returning True if version1 > version2"""
    for v1, v2 in zip(version1, version2):
        if v1 > v2:
            return True
        elif v1 < v2:
            return False
    # If all compared parts are equal, check if version1 has more parts (e.g., 1.2.3 vs 1.2)
    return len(version1) > len(version2)

class VersionCheckerThread(QThread):
    version_checked = Signal(str, bool)  # version, is_newer
    
    def __init__(self):
        super().__init__()
        self.url = VERSION_URL   
    def run(self):
        try:
            response = urllib.request.urlopen(self.url, timeout=10)
            online_version_str = response.read().decode('utf-8').strip()
            online_version = parse_version(online_version_str)
            current_version = parse_version(__version__)
            
            is_newer = compare_versions(online_version, current_version)
            self.version_checked.emit(online_version_str, is_newer)
        except urllib.error.URLError as e:
            print(f"Error downloading version info: {e}")
            self.version_checked.emit("", False)
        except Exception as e:
            print(f"Unexpected error: {e}")
            self.version_checked.emit("", False)

class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("О программе")
        self.setFixedSize(400, 350)

        layout = QVBoxLayout()

        title_label = QLabel(f"DNOTool v{__version__}")
        title_label.setFont(QFont("Arial", 16, QFont.Bold))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        description_label = QLabel("Программа для управления парком машин под управлением семейства операционных систем систем Linux.")
        description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description_label.setWordWrap(True)

        # Create version info label
        self.version_label = QLabel("")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.version_label.setWordWrap(True)
        self.version_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.version_label.setOpenExternalLinks(False)  # We'll handle it manually to use QDesktopServices
        self.version_label.linkActivated.connect(self.open_link)
        self.version_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.version_label.setOpenExternalLinks(False)  # We'll handle it manually to use QDesktopServices
        self.version_label.linkActivated.connect(self.open_link)
        
        # Start version check in background thread
        self.version_checker = VersionCheckerThread()
        self.version_checker.version_checked.connect(self.on_version_checked)
        self.version_checker.start()

        author_label = QLabel("Разработчик: <a href='https://t.me/x_p_9_k'>Yar</a>")
        author_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        author_label.setOpenExternalLinks(False)
        author_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        author_label.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        author_label.linkActivated.connect(self.open_link)

        
        link_label = QLabel('<a href="https://wiki.dno-it.ru/">wiki.dno-it.ru</a>')
        link_label.setOpenExternalLinks(False)
        link_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        link_label.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        link_label.linkActivated.connect(self.open_link)

        layout.addWidget(title_label)
        layout.addWidget(description_label)
        layout.addWidget(self.version_label)
        layout.addWidget(author_label)
        layout.addWidget(link_label)

        self.setLayout(layout)

    def on_version_checked(self, online_version, is_newer):
        if online_version:
            if is_newer:
                self.version_label.setText(f"<font color='red'>Доступна новая версия ({online_version})</font><br><a href='{UPDATE_URL}'>Скачать новую версию</a>")
            else:
                self.version_label.setText(f"Актуальная версия: {online_version}")
        else:
            self.version_label.setText("<font color='orange'>Не удалось проверить версию</font>")

    def open_link(self, link):
        QDesktopServices.openUrl(QUrl(link))
        
    def closeEvent(self, event):
        # Stop the version checker thread when dialog is closed
        if hasattr(self, 'version_checker'):
            self.version_checker.wait()
        event.accept()
        
    def closeEvent(self, event):
        # Stop the version checker thread when dialog is closed
        if hasattr(self, 'version_checker'):
            self.version_checker.wait()
        event.accept()

