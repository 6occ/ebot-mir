#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys, html, logging

from reports import run as run_report

def main():
    try:
        from logging_config import setup_logging
        setup_logging()
    except Exception:
        pass
    logging.getLogger(__name__).info("report run daily")
    text = run_report(mode="daily")
    try:
        from notify import send_message
        send_message(text)
    except Exception:
        pass
    print(text)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        msg = f"[report] ERROR: {e}\n{traceback.format_exc()}"
        try:
            from notify import send_error
            send_error("report", html.escape(msg))
        except Exception:
            pass
        print(msg, file=sys.stderr)
        sys.exit(1)
