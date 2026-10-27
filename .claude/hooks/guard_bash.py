#!/usr/bin/env python3
"""
PreToolUse-хук для Bash: любая shell-команда, которая не является заведомо
"только чтение", требует подтверждения пользователя.

Зачем: правки кода должны идти через Edit/Write (там пользователь видит diff).
Shell-команда может менять файлы в обход диффа (python-скрипт, sed -i, > file,
mv, git checkout ...), поэтому всё, что не входит в белый список ниже,
показывается пользователю перед выполнением.

Список - белый, а не чёрный: неизвестная команда = спросить. При любой ошибке
разбора - тоже спросить.
"""
import json
import re
import sys

REASON = (
    "Shell-команда не из списка «только чтение» и может менять файлы. "
    "Код в этом проекте меняется только через Edit/Write, чтобы был виден diff."
)

# Команды, которые сами по себе ничего не пишут на диск
READ_ONLY = {
    "ls", "cat", "head", "tail", "grep", "rg", "wc", "sort", "uniq", "cut", "tr",
    "echo", "printf", "pwd", "which", "file", "stat", "diff", "tree", "true",
    "date", "jq", "cd", "nl", "basename", "dirname", "realpath",
}
GIT_READ_ONLY = {
    "status", "diff", "log", "show", "blame", "ls-files", "rev-parse",
    "check-ignore", "shortlog", "describe",
}
MANAGE_READ_ONLY = {"test", "check", "showmigrations", "sqlmigrate", "diffsettings"}

# Перенаправления, которые ничего не пишут в файлы проекта
HARMLESS_REDIRECTS = re.compile(r"\d?>&\d|&?\d?>\s*/dev/null")


def is_read_only_segment(segment: str) -> bool:
    words = segment.split()
    if not words:
        return True
    name, args = words[0], words[1:]

    if name in READ_ONLY:
        return True
    if name == "sed":
        return not any(a == "-i" or a.startswith("-i") or a.startswith("--in-place") for a in args)
    if name == "find":
        return not any(a in ("-delete", "-exec", "-execdir", "-ok", "-fprint", "-fprintf", "-fls") for a in args)
    if name == "git":
        return bool(args) and args[0] in GIT_READ_ONLY
    if name in ("python", "python3") and args and args[0] == "manage.py":
        if len(args) < 2:
            return False
        if args[1] in MANAGE_READ_ONLY:
            return True
        # makemigrations только в режиме проверки: без этих флагов он создаёт файлы
        return args[1] == "makemigrations" and "--check" in args and "--dry-run" in args
    if name == "pip":
        return bool(args) and args[0] in ("list", "show", "freeze")
    return False


def is_read_only(command: str) -> bool:
    cleaned = HARMLESS_REDIRECTS.sub(" ", command)
    # запись в файл, heredoc, подстановка команд - разбирать не пытаемся, сразу спрашиваем
    if any(token in cleaned for token in (">", "<<", "$(", "`")):
        return False
    segments = re.split(r"&&|\|\||[;|\n&]", cleaned)
    return all(is_read_only_segment(s.strip()) for s in segments)


def main() -> None:
    try:
        command = json.load(sys.stdin)["tool_input"]["command"]
        if is_read_only(command):
            return  # без решения - обычный порядок разрешений
    except Exception:
        pass
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": REASON,
        }
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()