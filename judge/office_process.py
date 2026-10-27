"""Keep Office tool crashes distinct from invalid agent deliverables."""


def require_success(result, operation):
    if result.returncode:
        output = (result.stdout + result.stderr)[-2000:]
        raise RuntimeError(operation + ':exit=' + str(result.returncode) + ':' + output)
