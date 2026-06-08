"""Runs inside the Docker sandbox. Reads Python code from stdin, executes it safely."""

import sys
import threading
import io
import traceback

BLOCKED_MODULES = {"os", "subprocess", "shutil", "socket", "http", "ctypes", "signal", "importlib"}
BLOCKED_BUILTINS = {"exec", "eval", "compile", "__import__", "open"}
ALLOWED_PATHS = ("/data/", "/output/")
MAX_OUTPUT_BYTES = 1 * 1024 * 1024  # 1MB
TIMEOUT_SECONDS = 60


def restricted_open(path, *args, **kwargs):
    if not any(path.startswith(p) for p in ALLOWED_PATHS):
        raise PermissionError(f"open() denied: {path} not in allowed paths")
    return builtins_open(path, *args, **kwargs)


def check_imports(code: str):
    import ast
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in BLOCKED_MODULES:
                    raise ImportError(f"Module '{alias.name}' is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in BLOCKED_MODULES:
                raise ImportError(f"Module '{node.module}' is not allowed")


def main():
    code = sys.stdin.read()
    if len(code) > 50 * 1024:
        print("Error: code too large (max 50KB)", file=sys.stderr)
        sys.exit(1)

    check_imports(code)

    # Use threading-based timeout (works on all platforms including Windows)
    result = {}
    def run_code():
        old_stdout, old_stderr = sys.stdout, sys.stderr
        captured_out = io.StringIO()
        captured_err = io.StringIO()
        sys.stdout = captured_out
        sys.stderr = captured_err

        import builtins
        global builtins_open
        builtins_open = builtins.open
        builtins.open = restricted_open
        for name in BLOCKED_BUILTINS:
            if hasattr(builtins, name):
                delattr(builtins, name)

        try:
            exec(code, {"__builtins__": builtins})
        except Exception:
            traceback.print_exc(file=captured_err)
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            result["out"] = captured_out.getvalue()
            result["err"] = captured_err.getvalue()

    thread = threading.Thread(target=run_code, daemon=True)
    thread.start()
    thread.join(timeout=TIMEOUT_SECONDS)
    if thread.is_alive():
        print("Error: Execution timed out (60s)", file=sys.stderr)
        sys.exit(1)

    out = result.get("out", "")
    err = result.get("err", "")

    if len(out) > MAX_OUTPUT_BYTES:
        out = out[:MAX_OUTPUT_BYTES] + "\n... [output truncated]"
    if len(err) > MAX_OUTPUT_BYTES:
        err = err[:MAX_OUTPUT_BYTES] + "\n... [error truncated]"

    if out:
        print(out, end="")
    if err:
        print(err, end="", file=sys.stderr)


if __name__ == "__main__":
    main()
