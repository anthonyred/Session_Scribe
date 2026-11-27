import os
import sys

def verify():
    lnk_path = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Session Scribe.lnk")
    if not os.path.exists(lnk_path):
        print(f"FAILED: {lnk_path} does not exist")
        return False
    print(f"PASS: {lnk_path} exists ({os.path.getsize(lnk_path)} bytes)")
    return True

if __name__ == "__main__":
    ok = verify()
    sys.exit(0 if ok else 1)

