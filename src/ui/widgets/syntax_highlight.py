"""Подсветка синтаксиса для различных языков: Bash, Python, JSON, PHP, HTML, CSS, JavaScript, SQL, INI."""

from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont
import re
import os
from PySide6.QtCore import Qt, QRegularExpression
from PySide6.QtGui import QTextDocument

# --- Константы для цветов подсветки ---
COLOR_KEYWORD = QColor("#82AAFF")    # Светло-синий: ключевые слова
COLOR_COMMAND = QColor("#C792EA")    # Сиреневый: команды и встроенные функции bash
COLOR_COMMENT = QColor("#546E7A")    # Серо-голубой: комментарии (# ...)
COLOR_STRING = QColor("#33CC00")     # Светло-зелёный: строковые литералы
COLOR_VARIABLE = QColor("#F78C6C")   # Оранжевый: переменные ($VAR, ${VAR})
COLOR_SHEBANG = QColor("#616161")    # Серый: shebang (#!...)
COLOR_HTML_TAG = QColor("#FFCB6B")   # Жёлтый: HTML-теги (<div>, <span> и т.д.)
COLOR_HTML_ATTR = COLOR_VARIABLE     # Оранжевый: HTML-атрибуты (class=, id= и т.д.)
COLOR_JSON_KEY = COLOR_HTML_TAG      # Жёлтый: ключи в JSON
COLOR_JSON_STRING = COLOR_STRING     # Светло-зелёный: строки в JSON
COLOR_JSON_NUMBER = COLOR_VARIABLE   # Оранжевый: числа в JSON
COLOR_JSON_BOOL = COLOR_KEYWORD      # Светло-синий: true/false в JSON
COLOR_JSON_NULL = COLOR_COMMENT      # Серо-голубой: null в JSON
# --------------------------------------


class JsonHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.key_format = QTextCharFormat()
        self.key_format.setForeground(COLOR_COMMAND)
        self.string_format = QTextCharFormat()
        self.string_format.setForeground(COLOR_JSON_STRING)
        self.number_format = QTextCharFormat()
        self.number_format.setForeground(COLOR_JSON_NUMBER)
        self.bool_format = QTextCharFormat()
        self.bool_format.setForeground(COLOR_JSON_BOOL)
        self.null_format = QTextCharFormat()
        self.null_format.setForeground(COLOR_JSON_NULL)

    def highlightBlock(self, text):
        # Сначала подсвечиваем ключи и запоминаем их диапазоны
        key_ranges = []
        for match in re.finditer(r'"(\\.|[^"\\])*"(?=\s*:)', text):
            self.setFormat(match.start(), len(match.group()), self.key_format)
            key_ranges.append((match.start(), match.start() + len(match.group())))
        # Строки (не ключи)
        for match in re.finditer(r'"([^"\\]*(\\.[^"\\]*)*)"', text):
            start, end = match.start(), match.start() + len(match.group())
            # Проверяем, не попадает ли строка в диапазон ключей
            if not any(ks <= start < ke for ks, ke in key_ranges):
                self.setFormat(start, end - start, self.string_format)
        # Числа
        for match in re.finditer(r'\b\d+(\.\d+)?\b', text):
            self.setFormat(match.start(), len(match.group()), self.number_format)
        # Булевы
        for match in re.finditer(r'\b(true|false)\b', text, re.IGNORECASE):
            self.setFormat(match.start(), len(match.group()), self.bool_format)
        # null
        for match in re.finditer(r'\bnull\b', text, re.IGNORECASE):
            self.setFormat(match.start(), len(match.group()), self.null_format)

class PythonHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        # Форматы для разных элементов
        self.keyword_format = QTextCharFormat()
        self.keyword_format.setForeground(COLOR_KEYWORD)
        self.keyword_format.setFontWeight(QFont.Bold)
        
        self.comment_format = QTextCharFormat()
        self.comment_format.setForeground(COLOR_COMMENT)
        self.comment_format.setFontItalic(True)
        
        self.string_format = QTextCharFormat()
        self.string_format.setForeground(COLOR_STRING)
        
        self.number_format = QTextCharFormat()
        self.number_format.setForeground(COLOR_JSON_NUMBER)
        
        # Список ключевых слов
        self.keywords = [
            'False', 'class', 'finally', 'is', 'return', 'None', 'continue', 'for', 'lambda', 'try',
            'True', 'def', 'from', 'nonlocal', 'while', 'and', 'del', 'global', 'not', 'with',
            'as', 'elif', 'if', 'or', 'yield', 'assert', 'else', 'import', 'pass', 'break',
            'except', 'in', 'raise', 'async', 'finally', 'nonlocal', 'await'
        ]

    def highlightBlock(self, text):
        # Комментарии
        for match in re.finditer(r'#.*$', text):
            self.setFormat(match.start(), len(match.group()), self.comment_format)

        # Строки (тройные, двойные, одинарные)
        patterns = [
            r'"""(?:(?!"").)*"""',  # Тройные двойные
            r"'''(?:(?!'').)*'''",  # Тройные одинарные
            r'"(?:[^"\\]|\\.)*"',   # Двойные с экранированием
            r"'(?:[^'\\]|\\.)*'"    # Одинарные с экранированием
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.DOTALL):
                if not self.isInsideFormatWithColor(match.start(), len(match.group()), COLOR_COMMENT):
                    self.setFormat(match.start(), len(match.group()), self.string_format)

        # Ключевые слова
        keyword_pattern = r'\b(' + '|'.join(re.escape(k) for k in self.keywords) + r')\b'
        for match in re.finditer(keyword_pattern, text):
            start, end = match.span()
            if not self.isInsideFormatWithColor(start, end - start, COLOR_COMMENT) and \
               not self.isInsideFormatWithColor(start, end - start, COLOR_STRING):
                self.setFormat(start, end - start, self.keyword_format)

        # Числа
        for match in re.finditer(r'\b\d+(?:\.\d+)?\b', text):
            start, end = match.span()
            if not self.isInsideFormatWithColor(start, end - start, COLOR_COMMENT) and \
               not self.isInsideFormatWithColor(start, end - start, COLOR_STRING):
                self.setFormat(start, end - start, self.number_format)

    def isInsideFormatWithColor(self, start, length, color: QColor) -> bool:
        current_block = self.currentBlock()
        block_layout = current_block.layout()
        if not block_layout:
            return False
        formats = block_layout.formats()
        end = start + length - 1
        for fmt_range in formats:
            range_start = fmt_range.start
            range_end = fmt_range.start + fmt_range.length - 1
            if max(start, range_start) <= min(end, range_end):
                if fmt_range.format.foreground().color() == color:
                    if start >= range_start and start <= range_end:
                        return True
        return False

class PhpHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.keyword_format = QTextCharFormat()
        self.keyword_format.setForeground(COLOR_KEYWORD)
        self.keyword_format.setFontWeight(QFont.Bold)
        self.keywords = [
            'abstract', 'and', 'array', 'as', 'break', 'callable', 'case', 'catch', 'class', 'clone', 'const',
            'continue', 'declare', 'default', 'do', 'else', 'elseif', 'enddeclare', 'endfor', 'endforeach',
            'endif', 'endswitch', 'endwhile', 'extends', 'final', 'finally', 'for', 'foreach', 'function',
            'global', 'goto', 'if', 'implements', 'include', 'include_once', 'instanceof', 'insteadof',
            'interface', 'isset', 'list', 'namespace', 'new', 'or', 'print', 'private', 'protected',
            'public', 'require', 'require_once', 'return', 'static', 'switch', 'throw', 'trait', 'try',
            'unset', 'use', 'var', 'while', 'xor', 'yield', 'echo', 'true', 'false', 'null'
        ]
        self.comment_format = QTextCharFormat()
        self.comment_format.setForeground(COLOR_COMMENT)
        self.string_format = QTextCharFormat()
        self.string_format.setForeground(COLOR_STRING)
        self.number_format = QTextCharFormat()
        self.number_format.setForeground(COLOR_JSON_NUMBER)
        # Новый формат для переменных
        self.variable_format = QTextCharFormat()
        self.variable_format.setForeground(COLOR_VARIABLE)

    def highlightBlock(self, text):
        # Ключевые слова
        for match in re.finditer(r'\b(' + '|'.join(re.escape(word) for word in self.keywords) + r')\b', text, re.IGNORECASE):
            self.setFormat(match.start(), len(match.group()), self.keyword_format)
        # Комментарии
        for match in re.finditer(r'//.*|#.*', text):
            self.setFormat(match.start(), len(match.group()), self.comment_format)
        for match in re.finditer(r'/\*.*?\*/', text):
            self.setFormat(match.start(), len(match.group()), self.comment_format)
        # Строки
        for match in re.finditer(r'"[^"]*"', text):
            self.setFormat(match.start(), len(match.group()), self.string_format)
        for match in re.finditer(r"'[^']*'", text):
            self.setFormat(match.start(), len(match.group()), self.string_format)
        # Числа
        for match in re.finditer(r'\b\d+(\.\d+)?\b', text):
            self.setFormat(match.start(), len(match.group()), self.number_format)
        # Переменные ($var, $var123)
        for match in re.finditer(r'\$[a-zA-Z_][a-zA-Z0-9_]*', text):
            self.setFormat(match.start(), len(match.group()), self.variable_format)

class HtmlHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.tag_format = QTextCharFormat()
        self.tag_format.setForeground(COLOR_HTML_TAG)
        self.tag_format.setFontWeight(QFont.Bold)
        self.attr_format = QTextCharFormat()
        self.attr_format.setForeground(COLOR_HTML_ATTR)
        self.string_format = QTextCharFormat()
        self.string_format.setForeground(COLOR_STRING)
        self.comment_format = QTextCharFormat()
        self.comment_format.setForeground(COLOR_COMMENT)

    def highlightBlock(self, text):
        # Теги
        for match in re.finditer(r'<[^>]+>', text):
            self.setFormat(match.start(), len(match.group()), self.tag_format)
        # Атрибуты
        for match in re.finditer(r'\b\w+(?=\=)', text):
            self.setFormat(match.start(), len(match.group()), self.attr_format)
        # Строки
        for match in re.finditer(r'"[^"]*"', text):
            self.setFormat(match.start(), len(match.group()), self.string_format)
        for match in re.finditer(r"'[^']*'", text):
            self.setFormat(match.start(), len(match.group()), self.string_format)
        # Комментарии
        for match in re.finditer(r'<!--.*?-->', text):
            self.setFormat(match.start(), len(match.group()), self.comment_format)

class CssHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.selector_format = QTextCharFormat()
        self.selector_format.setForeground(COLOR_HTML_TAG)
        self.property_format = QTextCharFormat()
        self.property_format.setForeground(COLOR_HTML_ATTR)
        self.value_format = QTextCharFormat()
        self.value_format.setForeground(COLOR_STRING)
        self.comment_format = QTextCharFormat()
        self.comment_format.setForeground(COLOR_COMMENT)

    def highlightBlock(self, text):
        # Селекторы
        for match in re.finditer(r'^[^\{]+(?=\{)', text):
            self.setFormat(match.start(), len(match.group()), self.selector_format)
        # Свойства
        for match in re.finditer(r'\b[a-zA-Z-]+(?=\s*:)', text):
            self.setFormat(match.start(), len(match.group()), self.property_format)
        # Значения
        for match in re.finditer(r':\s*([^;]+);', text):
            self.setFormat(match.start(1), len(match.group(1)), self.value_format)
        # Комментарии
        for match in re.finditer(r'/\*.*?\*/', text):
            self.setFormat(match.start(), len(match.group()), self.comment_format)

class BashSyntaxHighlighter(QSyntaxHighlighter):
    """
    Класс для подсветки синтаксиса Bash в QTextEdit.
    """
    def __init__(self, parent: QTextDocument):
        super().__init__(parent)

        self.highlighting_rules = []

        # Ключевые слова (единый паттерн, двойное экранирование для QRegularExpression)
        keywords = [
            "if", "then", "else", "elif", "fi", "case", "esac", "for", "select", "while",
            "until", "do", "done", "in", "function", "time", "{!}"
        ]
        keyword_format = QTextCharFormat()
        keyword_format.setForeground(COLOR_KEYWORD)
        keyword_format.setFontWeight(QFont.Bold)
        keyword_pattern = QRegularExpression("\\b(" + "|".join(keywords) + ")\\b")
        self.highlighting_rules.append((keyword_pattern, keyword_format))

        # Команды/встроенные функции (без bash keywords, двойное экранирование)
        commands = [
            "echo", "printf", "read", "cd", "pwd", "pushd", "popd", "dirs", "let", "eval", "exec", "exit", "export",
            "readonly", "set", "shift", "source", "trap", "umask", "unset", "alias", "unalias", "bg", "fg", "jobs",
            "kill", "wait", "break", "continue", "return", "test", "true", "false", "type", "which", "grep", "sed", "awk",
            "ls", "cat", "mv", "cp", "rm", "mkdir", "chmod", "chown", "sudo", "apt", "yum", "dnf", "ssh", "scp", "find", "xargs", "tar", "gzip"
        ]
        command_format = QTextCharFormat()
        command_format.setForeground(COLOR_COMMAND)
        command_pattern = QRegularExpression("(?<=^|[\\s;|&()])(" + "|".join(commands) + ")(?=\\s|$|;|&|\\||\\)|\\()")
        self.highlighting_rules.append((command_pattern, command_format))

        # Формат для комментариев (#)
        comment_format = QTextCharFormat()
        comment_format.setForeground(COLOR_COMMENT)
        comment_format.setFontItalic(True)
        self.comment_format = comment_format
        self.highlighting_rules.append((QRegularExpression(r"^\s*#.*"), comment_format))
        self.highlighting_rules.append((QRegularExpression(r"\s#+.*"), comment_format))

        # Формат для строк в одинарных кавычках
        single_quote_format = QTextCharFormat()
        single_quote_format.setForeground(COLOR_STRING)
        self.highlighting_rules.append((QRegularExpression(r"'[^']*'"), single_quote_format))

        # Формат для строк в двойных кавычках (с поддержкой экранирования)
        double_quote_format = QTextCharFormat()
        double_quote_format.setForeground(COLOR_STRING)
        self.string_format = double_quote_format
        self.highlighting_rules.append((QRegularExpression(r'"([^"\\]|\\.)*"'), double_quote_format))

        # Формат для переменных ($VAR, ${VAR})
        variable_format = QTextCharFormat()
        variable_format.setForeground(COLOR_VARIABLE)
        variable_format.setFontItalic(True)
        self.highlighting_rules.append((QRegularExpression(r"\$[a-zA-Z_][a-zA-Z0-9_]*"), variable_format))
        self.highlighting_rules.append((QRegularExpression(r"\$\{[^\}]+\}"), variable_format))
        self.highlighting_rules.append((QRegularExpression(r"\$[@*#?$!-]"), variable_format))
        self.highlighting_rules.append((QRegularExpression(r"\$[0-9]"), variable_format))

        # Формат для shebang (#!)
        shebang_format = QTextCharFormat()
        shebang_format.setForeground(COLOR_SHEBANG)
        shebang_format.setFontWeight(QFont.Bold)
        self.highlighting_rules.append((QRegularExpression(r"^#!.*"), shebang_format))

    def highlightBlock(self, text):
        # Комментарии
        comment_pattern = QRegularExpression("(#.*)")
        match_iterator = comment_pattern.globalMatch(text)
        while match_iterator.hasNext():
            match = match_iterator.next()
            start = match.capturedStart(1)
            length = match.capturedLength(1)
            self.setFormat(start, length, self.comment_format)

        # Строки (двойные кавычки с экранированием)
        dq_pattern = QRegularExpression('("([^"\\\\]|\\\\.)*")')
        match_iterator = dq_pattern.globalMatch(text)
        while match_iterator.hasNext():
            match = match_iterator.next()
            if not self.isInsideFormatWithColor(match.capturedStart(1), match.capturedLength(1), COLOR_COMMENT):
                self.setFormat(match.capturedStart(1), match.capturedLength(1), self.string_format)

        # Строки (одинарные кавычки)
        sq_pattern = QRegularExpression("('[^']*')")
        match_iterator = sq_pattern.globalMatch(text)
        while match_iterator.hasNext():
            match = match_iterator.next()
            if not self.isInsideFormatWithColor(match.capturedStart(1), match.capturedLength(1), COLOR_COMMENT):
                self.setFormat(match.capturedStart(1), match.capturedLength(1), self.string_format)

        for pattern, fmt in self.highlighting_rules:
            if pattern.pattern() in [r"^\s*#.*", r"\s#+.*", r'"([^"\\]|\\.)*"', r"'[^']*'"]:
                if pattern.pattern() == r"^#!.*":
                    continue
                continue
            match_iterator = pattern.globalMatch(text)
            while match_iterator.hasNext():
                match = match_iterator.next()
                start = match.capturedStart()
                length = match.capturedLength()
                if not self.isInsideFormatWithColor(start, length, COLOR_COMMENT) and \
                   not self.isInsideFormatWithColor(start, length, COLOR_STRING):
                    self.setFormat(start, length, fmt)

        shebang_rule = self.highlighting_rules[-1]
        if text.startswith("#!"):
            match_iterator = shebang_rule[0].globalMatch(text)
            if match_iterator.hasNext():
                match = match_iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), shebang_rule[1])

    def isInsideFormatWithColor(self, start, length, color: QColor) -> bool:
        current_block = self.currentBlock()
        block_layout = current_block.layout()
        if not block_layout:
            return False
        formats = block_layout.formats()
        end = start + length - 1
        for fmt_range in formats:
            range_start = fmt_range.start
            range_end = fmt_range.start + fmt_range.length - 1
            if max(start, range_start) <= min(end, range_end):
                if fmt_range.format.foreground().color() == color:
                    if start >= range_start and start <= range_end:
                        return True
        return False

class JavaScriptHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.keyword_format = QTextCharFormat()
        self.keyword_format.setForeground(COLOR_KEYWORD)
        self.keyword_format.setFontWeight(QFont.Bold)
        self.keywords = [
            'break', 'case', 'catch', 'class', 'const', 'continue', 'debugger', 'default', 'delete',
            'do', 'else', 'export', 'extends', 'finally', 'for', 'function', 'if', 'import', 'in',
            'instanceof', 'let', 'new', 'return', 'super', 'switch', 'this', 'throw', 'try', 'typeof',
            'var', 'void', 'while', 'with', 'yield', 'true', 'false', 'null', 'undefined'
        ]
        self.comment_format = QTextCharFormat()
        self.comment_format.setForeground(COLOR_COMMENT)
        self.comment_format.setFontItalic(True)
        self.string_format = QTextCharFormat()
        self.string_format.setForeground(COLOR_STRING)
        self.number_format = QTextCharFormat()
        self.number_format.setForeground(COLOR_JSON_NUMBER)
        # Новый формат для переменных
        self.variable_format = QTextCharFormat()
        self.variable_format.setForeground(COLOR_VARIABLE)

    def highlightBlock(self, text):
        # Комментарии
        for match in re.finditer(r'//.*', text):
            self.setFormat(match.start(), len(match.group()), self.comment_format)
        for match in re.finditer(r'/\*.*?\*/', text):
            self.setFormat(match.start(), len(match.group()), self.comment_format)
        # Строки
        for match in re.finditer(r'"(?:[^"\\]|\\.)*"', text):
            self.setFormat(match.start(), len(match.group()), self.string_format)
        for match in re.finditer(r"'(?:[^'\\]|\\.)*'", text):
            self.setFormat(match.start(), len(match.group()), self.string_format)
        # Ключевые слова
        keyword_pattern = r'\b(' + '|'.join(re.escape(k) for k in self.keywords) + r')\b'
        for match in re.finditer(keyword_pattern, text):
            self.setFormat(match.start(), len(match.group()), self.keyword_format)
        # Числа
        for match in re.finditer(r'\b\d+(?:\.\d+)?\b', text):
            self.setFormat(match.start(), len(match.group()), self.number_format)
        # Переменные ($var, $var123) — часто встречаются в шаблонах, jQuery и т.п.
        for match in re.finditer(r'\$[a-zA-Z_][a-zA-Z0-9_]*', text):
            self.setFormat(match.start(), len(match.group()), self.variable_format)

class SqlHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.keyword_format = QTextCharFormat()
        self.keyword_format.setForeground(COLOR_KEYWORD)
        self.keyword_format.setFontWeight(QFont.Bold)
        self.keywords = [
            'SELECT', 'FROM', 'WHERE', 'INSERT', 'INTO', 'VALUES', 'UPDATE', 'SET', 'DELETE', 'CREATE',
            'TABLE', 'ALTER', 'DROP', 'JOIN', 'INNER', 'LEFT', 'RIGHT', 'FULL', 'ON', 'AS', 'AND', 'OR',
            'NOT', 'NULL', 'IS', 'IN', 'EXISTS', 'BETWEEN', 'LIKE', 'GROUP', 'BY', 'ORDER', 'HAVING',
            'DISTINCT', 'UNION', 'ALL', 'LIMIT', 'OFFSET', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'EXTRACT',
            'PRIMARY', 'KEY', 'FOREIGN', 'REFERENCES', 'CHECK', 'DEFAULT', 'CHECK', 'CHECK', 'CHECK', 'CHECK',
            'AUTOINCREMENT', 'UNIQUE', 'ASC', 'DESC', 'CURRENT_TIMESTAMP', 'INTEGER', 'TEXT', 'REAL', 'BLOB',
            'NULL'
        ]
        self.comment_format = QTextCharFormat()
        self.comment_format.setForeground(COLOR_COMMENT)
        self.comment_format.setFontItalic(True)
        self.string_format = QTextCharFormat()
        self.string_format.setForeground(COLOR_STRING)
        self.number_format = QTextCharFormat()
        self.number_format.setForeground(COLOR_JSON_NUMBER)
        self.variable_format = QTextCharFormat()
        self.variable_format.setForeground(COLOR_VARIABLE)
        self.identifier_format = QTextCharFormat()
        self.identifier_format.setForeground(COLOR_HTML_TAG)
        self.operator_format = QTextCharFormat()
        self.operator_format.setForeground(COLOR_COMMAND)

    def highlightBlock(self, text):
        # Комментарии
        for match in re.finditer(r'--.*', text):
            self.setFormat(match.start(), len(match.group()), self.comment_format)
        for match in re.finditer(r'/\*.*?\*/', text):
            self.setFormat(match.start(), len(match.group()), self.comment_format)
        # Строки (одинарные и двойные кавычки)
        for match in re.finditer(r"'(?:[^'\\]|\\.)*'", text):
            self.setFormat(match.start(), len(match.group()), self.string_format)
        for match in re.finditer(r'"(?:[^"\\]|\\.)*"', text):
            self.setFormat(match.start(), len(match.group()), self.string_format)
        # Переменные (@var, :param)
        for match in re.finditer(r'@[a-zA-Z_][a-zA-Z0-9_]*', text):
            self.setFormat(match.start(), len(match.group()), self.variable_format)
        for match in re.finditer(r':[a-zA-Z_][a-zA-Z0-9_]*', text):
            self.setFormat(match.start(), len(match.group()), self.variable_format)
        # Идентификаторы в кавычках ("table".column)
        for match in re.finditer(r'`[^`]+`', text):
            self.setFormat(match.start(), len(match.group()), self.identifier_format)
        # Ключевые слова (без учёта регистра)
        keyword_pattern = r'\b(' + '|'.join(self.keywords) + r')\b'
        for match in re.finditer(keyword_pattern, text, re.IGNORECASE):
            self.setFormat(match.start(), len(match.group()), self.keyword_format)
        # Числа (целые, вещественные, экспоненциальные)
        for match in re.finditer(r'\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b', text):
            self.setFormat(match.start(), len(match.group()), self.number_format)
        # Операторы
        for match in re.finditer(r'[=<>!~*%/+-]', text):
            self.setFormat(match.start(), len(match.group()), self.operator_format)

class IniHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.section_format = QTextCharFormat()
        self.section_format.setForeground(COLOR_KEYWORD)
        self.section_format.setFontWeight(QFont.Bold)
        self.key_format = QTextCharFormat()
        self.key_format.setForeground(COLOR_HTML_ATTR)
        self.value_format = QTextCharFormat()
        self.value_format.setForeground(COLOR_STRING)
        self.comment_format = QTextCharFormat()
        self.comment_format.setForeground(COLOR_COMMENT)
        self.comment_format.setFontItalic(True)

    def highlightBlock(self, text):
        # Секции
        for match in re.finditer(r'\[.*?\]', text):
            self.setFormat(match.start(), len(match.group()), self.section_format)
        # Ключи
        for match in re.finditer(r'^[ \t]*([\w.-]+)(?=\s*=)', text):
            self.setFormat(match.start(1), len(match.group(1)), self.key_format)
        # Значения
        for match in re.finditer(r'=\s*(.*)', text):
            self.setFormat(match.start(1), len(match.group(1)), self.value_format)
        # Комментарии
        for match in re.finditer(r'[;#].*', text):
            self.setFormat(match.start(), len(match.group()), self.comment_format)

ConfHighlighter = IniHighlighter  # Для .conf используем тот же класс, что и для .ini
EnvHighlighter = IniHighlighter

def get_syntax_highlighter(document, filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".sh":
        return BashSyntaxHighlighter(document)
    if ext == ".json":
        return JsonHighlighter(document)
    if ext == ".py":
        return PythonHighlighter(document)
    if ext == ".php":
        return PhpHighlighter(document)
    if ext in (".html", ".htm"):
        return HtmlHighlighter(document)
    if ext == ".css":
        return CssHighlighter(document)
    if ext in (".js", ".jsx"):
        return JavaScriptHighlighter(document)
    if ext in (".sql", ):
        return SqlHighlighter(document)
    if ext in (".ini", ):
        return IniHighlighter(document)
    if ext in (".conf", ):
        return ConfHighlighter(document)
    if filename in (".env", ):
        return EnvHighlighter(document)
    # Можно добавить другие расширения и классы
    return None
