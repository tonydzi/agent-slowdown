"""PreToolUse guard: refuse a coding agent's broad filesystem searches before they start.

An agent that runs `find /`, `grep -r` over your home folder or `rg x /` starts a process
that walks everything, often outlives the session, and can freeze the machine. This hook
reads the command as text and never runs it.

It is a seatbelt against the accidental command, not a sandbox. It does not evaluate
variables, aliases, globs, eval, script files or nested `find -exec`.

Exit 0 = allow, exit 2 = block with the reason on stderr (the agent reads it and retries
with a narrower path).
"""
import json
import os
from pathlib import Path
import posixpath
import re
import sys

SHELL_TOOLS = {'bash', 'powershell', 'exec_command', 'shell'}
ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z_0-9]*=')
BREAKS = {';', '\n', '|', '||', '&&', '&', '(', ')', '{', '}'}


def _tokens(text, mark_quoted_tilde=False):
    """Return (text, syntax) tokens; quoted data never becomes shell syntax."""
    result, word = [], []
    active, quote, i = False, '', 0
    def flush():
        nonlocal active
        if active:
            result.append((''.join(word), False))
            word.clear()
            active = False
    while i < len(text):
        char = text[i]
        if char in {'\\', '`'} and i + 1 < len(text) and quote != "'":
            nxt = text[i + 1]
            if nxt == '\n':
                i += 2
                continue
            if nxt in ('\\"$`' if quote else " \\t\\\"'`;&|(){}<>"):
                word.append(nxt)
                active = True
                i += 2
                continue
        if quote:
            if char == quote:
                quote = ''
            else:
                if mark_quoted_tilde and char == '~' and not word:
                    word.append('\x00')  # quoted ~ is literal, not home expansion
                word.append(char)
            i += 1
            continue
        if char in {'"', "'"}:
            quote, active = char, True
        elif char == '#' and not active:
            i = text.find('\n', i)
            if i < 0:
                break
            continue
        elif char == '\n' or char in ';|&(){}<>':
            flush()
            op = char
            if text[i:i+3] == '<<-':
                op = '<<-'
                i += 2
            elif i + 1 < len(text) and text[i:i+2] in {'&&','||','>>','<<','>&','<&','|&'}:
                op = text[i:i+2]
                i += 1
            result.append((op, True))
        elif char.isspace():
            flush()
        else:
            word.append(char)
            active = True
        i += 1
    flush()
    return result


def _without_heredocs(text):
    # Ignore literal here-document bodies, which are data rather than commands.
    lines, output, pending = text.splitlines(keepends=True), [], []
    for line in lines:
        if pending:
            delimiter, strip_tabs = pending[0]
            candidate = line.rstrip('\r\n')
            if strip_tabs:
                candidate = candidate.lstrip('\t')
            if candidate == delimiter:
                pending.pop(0)
            output.append('\n')
            continue
        output.append(line)
        tokens = _tokens(line)
        for index, token in enumerate(tokens[:-1]):
            if token in {('<<', True), ('<<-', True)}:
                pending.append((tokens[index + 1][0], token[0] == '<<-'))
    return ''.join(output)


def _argv(tokens):
    argv, i = [], 0
    while i < len(tokens):
        value, syntax = tokens[i]
        if syntax and value in {'>', '>>', '<', '<<', '<<-', '>&', '<&'}:
            if argv and argv[-1].isdigit():
                argv.pop()
            i += 2  # discard redirection target, not an executable/argument
            continue
        argv.append(value)
        i += 1
    return argv


def _name(path):
    return path.replace('\\', '/').rsplit('/', 1)[-1].lower().removesuffix('.exe')


def _literal_path(value, cwd=None):
    """Resolve only literal Windows/MSYS paths and ~; unknown stays unknown."""
    if not isinstance(value, str) or not value or re.search(r'[$`*?{}\x00]', value):
        return None
    value = value.replace('\\', '/')
    if value == '~' or value.startswith('~/'):
        value = str(Path.home()).replace('\\', '/') + value[1:]
    elif value.startswith('~'):
        return None  # another user's home needs shell/account lookup
    if re.match(r'^/[a-zA-Z](?:/|$)', value):
        value = value[1] + ':/' + value[3:]
    if not re.match(r'^[a-zA-Z]:/', value) and not value.startswith('/'):
        base = _literal_path(cwd) if cwd else None
        if base is None:
            return None
        value = posixpath.join(base, value)
    return posixpath.normpath(value).casefold()


def _grep_unbounded(argv, cwd=None):
    """Separate GNU grep options/patterns from literal traversal roots."""
    recursive, explicit_pattern, options = False, False, True
    operands, i = [], 1
    long_values = {'--regexp','--file','--max-count','--after-context',
                   '--before-context','--context','--directories','--devices',
                   '--exclude','--exclude-from','--exclude-dir','--include',
                   '--label','--binary-files','--group-separator'}
    while i < len(argv):
        arg = argv[i]
        if options and arg == '--':
            options = False
        elif options and arg.startswith('--'):
            key, separator, value = arg.partition('=')
            if key in {'--help','--version'}:
                return None
            if key in long_values and not separator:
                i += 1
                value = argv[i] if i < len(argv) else ''
            if key in {'--recursive','--dereference-recursive'}:
                recursive = True
            elif key == '--directories':
                recursive = value == 'recurse'
            explicit_pattern |= key in {'--regexp','--file'}
        elif options and arg.startswith('-') and arg != '-':
            for offset, flag in enumerate(arg[1:], 2):
                if flag == 'V':
                    return None
                if flag in 'rR':
                    recursive = True
                if flag in 'efmABCdD':
                    value = arg[offset:]
                    if not value:
                        i += 1
                        value = argv[i] if i < len(argv) else ''
                    explicit_pattern |= flag in 'ef'
                    if flag == 'd':
                        recursive = value == 'recurse'
                    break  # remainder belongs to this option, not more flags
        else:
            operands.append(arg)
        i += 1
    roots = operands if explicit_pattern else operands[1:]
    if not recursive:
        return None
    home = _literal_path(str(Path.home()))
    blocked = {home, posixpath.join(home, '.claude'), posixpath.join(home, '.codex')}
    for root in roots or ['.']:  # GNU recursive grep defaults to cwd
        normalized = _literal_path(root, cwd)
        if normalized is not None and (not normalized.strip('/') or normalized in blocked):
            return 'recursive grep over the virtual root or whole user/agent profile'
    return None


def _inspect(argv, depth, shell_context, cwd=None):
    while argv and (ASSIGNMENT.match(argv[0]) or argv[0] in {'if','then','elif','else','do','!'}):
        argv = argv[1:]
    if not argv or depth > 8:
        return None
    name = _name(argv[0])
    if name in {'env', 'timeout', 'nice', 'command', 'exec'}:
        rest, i = argv[1:], 0
        if name == 'command' and any(x in {'-v','-V'} for x in rest):
            return None
        while i < len(rest):
            arg = rest[i]
            if name == 'env' and arg in {'-C', '--chdir'}:
                cwd = _literal_path(rest[i + 1], cwd) if i + 1 < len(rest) else None
                i += 2
            elif name == 'env' and arg.startswith('--chdir='):
                cwd = _literal_path(arg.partition('=')[2], cwd)
                i += 1
            elif ASSIGNMENT.match(arg) or arg == '--':
                i += 1
            elif arg.startswith('-'):
                i += 2 if arg in {'-u','--unset','-C','--chdir','-s','--signal','-k','--kill-after','-n','--adjustment','-a'} else 1
            else:
                break
        if name == 'timeout':
            i += 1  # duration
        return _inspect(rest[i:], depth + 1, shell_context, cwd)
    if name in {'bash','sh','zsh','dash','ksh','powershell','pwsh'}:
        for i, value in enumerate(argv[1:], 1):
            is_script = value.lower() in {'-command','-c','--command'}
            if name not in {'powershell','pwsh'}:
                is_script |= value.startswith('-') and not value.startswith('--') and 'c' in value[1:]
            if is_script and i + 1 < len(argv):
                child_context = 'native' if name in {'powershell','pwsh'} else 'msys'
                return _judge(argv[i + 1], depth + 1, child_context, cwd)
        return None
    if name == 'grep':
        return _grep_unbounded(argv, cwd)
    if name in {'rg', 'fd', 'fdfind'}:
        return _rg_unbounded(argv, cwd)
    if name not in {'find', 'bfs'}:
        return None
    exe_path = argv[0].replace('\\', '/').lower()
    if '/' not in exe_path and shell_context != 'msys':
        return None  # Windows PowerShell/default PATH resolves bare find to native find.exe
    if re.search(r'/(?:system32|syswow64|sysnative)/find\.exe$', exe_path):
        return None  # Windows text-search command, not GNU directory traversal
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in {'-L','-P','-H','--'} or re.fullmatch(r'-O[0-3]', arg):
            i += 1
            continue
        if arg == '-D':
            i += 2
            continue
        if name == 'bfs' and arg in {'-S', '-j'}:
            i += 2  # bfs strategy/threads options take a value: `bfs -S dfs / ...`
            continue
        if arg.startswith('-') or arg in {'!', '('}:
            break  # expression predicates and their operands are not roots
        if arg.startswith('/'):
            root = posixpath.normpath(arg)
            if root.strip('/') == '' or root == '/proc' or root.startswith('/proc/'):
                return 'MSYS virtual root' if os.name == 'nt' else 'filesystem root'
        if _literal_path(arg, cwd) == _home():
            return 'whole user home'
        i += 1
    return None


def _home():
    """Same normal form as _literal_path (normpath + casefold)."""
    return posixpath.normpath(str(Path.home()).replace('\\', '/')).casefold()


def _rg_unbounded(argv, cwd=None):
    """ripgrep with a literal operand equal to / or the whole home."""
    operands = [a for a in argv[1:] if not a.startswith('-')]
    home = _home()
    # With --files there is no pattern, so every operand is a path.
    paths = operands if '--files' in argv else operands[1:]
    for op in paths:
        normalized = _literal_path(op, cwd)
        if normalized is not None and (not normalized.strip('/') or normalized.rstrip('/') == home):
            return 'rg or fd over filesystem root or whole user home'
    return None


def _judge(command, depth=0, shell_context='msys', cwd=None):
    segment = []
    for token in _tokens(_without_heredocs(command), mark_quoted_tilde=True) + [(';', True)]:
        if token[1] and (token[0] in BREAKS or token[0] == '|&'):
            argv = _argv(segment)
            reason = _inspect(argv, depth, shell_context, cwd)
            if reason:
                return reason
            # Track only simple local cd sequences. Pipelines, background jobs,
            # groups and conditional recovery are not a shell interpreter.
            if argv and _name(argv[0]) in {'cd', 'chdir', 'set-location', 'pushd', 'popd'}:
                if _name(argv[0]) == 'cd' and token[0] in {';', '\n', '&&'}:
                    destination = argv[1:] or ['~']
                    if destination[:1] == ['--']:
                        destination = destination[1:]
                    cwd = _literal_path(destination[0], cwd) if len(destination) == 1 and destination[0] != '-' else None
                else:
                    cwd = None
            if token[0] in {'(', ')', '{', '}'}:
                cwd = None
            segment = []
        else:
            segment.append(token)
    return None


def judge(command, *, is_windows=None, shell_context='msys', cwd=None):
    if is_windows is None:
        is_windows = os.name == 'nt'
    if not isinstance(command, str):
        return None
    # On macOS/Linux a bare find/bfs is directory traversal, so judge it like bash/MSYS.
    return _judge(command, shell_context=shell_context if is_windows else 'msys', cwd=cwd)


def check_payload(payload, *, is_windows=None):
    if not isinstance(payload, dict):
        return None
    tool = str(payload.get('tool_name', '')).lower().split('__')[-1]
    data = payload.get('tool_input')
    if tool not in SHELL_TOOLS or not isinstance(data, dict):
        return None
    shell_name = _name(str(data.get('shell', '')))
    shell_context = 'msys' if tool == 'bash' or shell_name in {'bash','sh','zsh','dash','ksh'} else 'native'
    cwd = data.get('workdir') or data.get('cwd') or payload.get('cwd')
    for key in ('command', 'script', 'cmd'):
        reason = judge(data.get(key), is_windows=is_windows, shell_context=shell_context, cwd=cwd)
        if reason:
            return reason
    return None


def main(stdin=None, stderr=None, *, is_windows=None):
    stdin, stderr = stdin or sys.stdin, stderr or sys.stderr
    try:
        payload = json.loads(stdin.read().lstrip('\ufeff'))
    except (ValueError, TypeError):
        return 0
    reason = check_payload(payload, is_windows=is_windows)
    if not reason:
        return 0
    print('agent-slowdown guard: broad search blocked (' + reason + '). '
          'Limit find/bfs/grep -r/rg to a named subdirectory of the project. '
          'On macOS `mdfind -name <file>` finds a file by name without walking the disk.',
          file=stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
