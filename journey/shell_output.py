"""Recognize an explicitly printed status of the immediately preceding command."""
import re
import shlex


def strip_exit_echo(parts):
    captured = parts[-3:] == [';', 'echo', 'EXIT=$?']
    return (parts[:-3], True) if captured else (parts, False)


def split_reported_exit(stdout):
    if not isinstance(stdout, str):
        return None, None
    match = re.search(r'(?:^|\n)EXIT=(\d+)\s*\Z', stdout)
    if not match:
        return None, None
    return stdout[:match.start()], int(match.group(1))


def reported_shell_exit(command, output):
    if (not isinstance(command, str) or not isinstance(output, dict) or
            type(output.get('exit_code')) is not int or output['exit_code'] != 0):
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';')
        lexer.whitespace_split = True
        lexer.commenters = ''
        _, captured = strip_exit_echo(list(lexer))
    except ValueError:
        return None
    if not captured:
        return None
    _, status = split_reported_exit(output.get('stdout'))
    return status
