"""One-shot iLink login: prints a QR code, waits for the scan, saves credentials.

Scan with the phone that has the 微信ClawBot plugin enabled
(设置 → 插件 → 微信ClawBot). Run again anytime to re-pair.
"""

from weixin_ilink import login

from .config import CREDS


def main() -> None:
    CREDS.parent.mkdir(parents=True, exist_ok=True)
    login(save_to=CREDS)
    print(f"Login OK — credentials saved to {CREDS}")


if __name__ == "__main__":
    main()
