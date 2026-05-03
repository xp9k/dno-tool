from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QGridLayout, QCheckBox, QHBoxLayout, QPushButton
import stat

class RemoteFileInfoDialog(QDialog):
    def __init__(self, path, mode, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Права: {path}")
        self.mode = mode
        layout = QVBoxLayout(self)
        grid = QGridLayout()
        self.checks = {}
        labels = ["Чтение", "Запись", "Исполнение"]
        roles = ["Пользователь", "Группа", "Остальные"]
        bits = [stat.S_IRUSR, stat.S_IWUSR, stat.S_IXUSR,
                stat.S_IRGRP, stat.S_IWGRP, stat.S_IXGRP,
                stat.S_IROTH, stat.S_IWOTH, stat.S_IXOTH]
        for i, role in enumerate(roles):
            grid.addWidget(QLabel(role), i+1, 0)
        for j, label in enumerate(labels):
            grid.addWidget(QLabel(label), 0, j+1)
        for i in range(3):
            for j in range(3):
                bit = bits[i*3+j]
                cb = QCheckBox()
                cb.setChecked(bool(mode & bit))
                self.checks[(i, j)] = cb
                grid.addWidget(cb, i+1, j+1)
        layout.addLayout(grid)
        self.exec_cb = QCheckBox("Запуск разрешён (любым)")
        self.exec_cb.setChecked(bool(mode & 0o111))
        layout.addWidget(self.exec_cb)
        btns = QHBoxLayout()
        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("Отмена")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)
        
    def get_mode(self):
        mode = 0
        bits = [stat.S_IRUSR, stat.S_IWUSR, stat.S_IXUSR,
                stat.S_IRGRP, stat.S_IWGRP, stat.S_IXGRP,
                stat.S_IROTH, stat.S_IWOTH, stat.S_IXOTH]
        for i in range(3):
            for j in range(3):
                if self.checks[(i, j)].isChecked():
                    mode |= bits[i*3+j]
        # Если чекбокс "запуск" снят, убираем все execute
        if not self.exec_cb.isChecked():
            mode &= ~0o111
        else:
            # Если выставлен, выставляем execute всем, у кого есть read
            for i in range(3):
                if self.checks[(i, 0)].isChecked():
                    mode |= [stat.S_IXUSR, stat.S_IXGRP, stat.S_IXOTH][i]
        return mode
