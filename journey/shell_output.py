"""Recognize an explicitly printed status of the immediately preceding command."""
import re
import shlex


def strip_exit_echo(parts):
    if len(parts) >= 3 and parts[-3:-1] == [';', 'echo']:
        match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)=\$\?', parts[-1])
        if match:
            return parts[:-3], match.group(1)
    return parts, None


def split_reported_exit(stdout, label):
    if not isinstance(stdout, str) or not isinstance(label, str) or not label:
        return None, None
    match = re.search(r'(?:^|\n)' + re.escape(label) + r'=(\d+)\s*\Z', stdout)
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
        _, label = strip_exit_echo(list(lexer))
    except ValueError:
        return None
    if not label:
        return None
    _, status = split_reported_exit(output.get('stdout'), label)
    return status
