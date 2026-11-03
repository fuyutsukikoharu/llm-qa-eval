import sys

from .cli import main

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # 老终端不支持 reconfigure 时忽略
        pass
    raise SystemExit(main())
