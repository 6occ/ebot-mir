#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
import os
import sys
import py_compile
from typing import List, Tuple

EXCLUDE_DIRS = {".git", "__pycache__", "venv", "logs", "tmp"}

def should_skip_dir(dirname: str) -> bool:
    base = os.path.basename(dirname.rstrip(os.sep))
    return base in EXCLUDE_DIRS

def find_python_files(root: str) -> List[str]:
    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(os.path.join(dirpath, d))]
        for name in filenames:
            if name.endswith(".py"):
                files.append(os.path.join(dirpath, name))
    return sorted(files)

def compile_file(path: str) -> Tuple[bool, str]:
    try:
        py_compile.compile(path, doraise=True)
        return True, ""
    except py_compile.PyCompileError as e:
        return False, e.msg
    except SyntaxError as e:
        loc = f"{e.filename}:{e.lineno}:{e.offset}" if e.filename else ""
        msg = f"{loc} SyntaxError: {e.msg}"
        return False, msg
    except Exception as e:
        return False, f"{path} Exception: {repr(e)}"

def main():
    ap = argparse.ArgumentParser(description="Рекурсивная проверка синтаксиса всех .py (py_compile)")
    ap.add_argument("--root", default=".", help="Корень проекта (по умолчанию текущая директория)")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    files = find_python_files(root)
    total = len(files)
    ok_count = 0
    errors: List[Tuple[str, str]] = []

    print(f"[check_syntax] root={root}")
    print(f"[check_syntax] found .py files: {total}")

    for p in files:
        ok, err = compile_file(p)
        if ok:
            ok_count += 1
        else:
            errors.append((p, err))

    fail_count = len(errors)
    print("\n===== SYNTAX SUMMARY =====")
    print(f"Total: {total}")
    print(f"OK   : {ok_count}")
    print(f"FAIL : {fail_count}")

    if fail_count:
        print("\n----- Errors -----")
        for path, msg in errors:
            print(f"* {path}")
            if msg:
                print(f"  {msg}")
        print("------------------")
        sys.exit(1)
    else:
        print("All good.")
        sys.exit(0)

if __name__ == "__main__":
    main()
