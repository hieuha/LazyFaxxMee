"""Run the FaxxMe server as a daemon:  python -m faxxme

Reads configuration from environment variables (see deploy/faxxme.env):
  FAXXME_HOST       bind address        (default 0.0.0.0)
  FAXXME_PORT       bind port           (default 8000)
  FAXXME_LOG_LEVEL  uvicorn log level   (default info)

Logs go to stdout/stderr so systemd/journald captures them
(`journalctl -u faxxme -f`).
"""
import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "faxxme.app:app",
        host=os.environ.get("FAXXME_HOST", "0.0.0.0"),
        port=int(os.environ.get("FAXXME_PORT", "8000")),
        log_level=os.environ.get("FAXXME_LOG_LEVEL", "info"),
        proxy_headers=True,           # honour X-Forwarded-* from tailscale serve / reverse proxies
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
