import os
import shutil

src_usage = r"D:\Events\Sample_test\usage"
dst_usage = r"D:\Events\Sample_test\scribe\usage"
if os.path.isdir(src_usage):
    os.makedirs(dst_usage, exist_ok=True)
    for f in os.listdir(src_usage):
        s = os.path.join(src_usage, f)
        d = os.path.join(dst_usage, f)
        if os.path.isfile(s):
            shutil.copy2(s, d)
        elif os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
    shutil.rmtree(src_usage, ignore_errors=True)

src_ledger = r"D:\Events\Sample_test\state\usage"
dst_ledger = r"D:\Events\Sample_test\scribe\state\usage"
if os.path.isdir(src_ledger):
    os.makedirs(dst_ledger, exist_ok=True)
    for f in os.listdir(src_ledger):
        s = os.path.join(src_ledger, f)
        d = os.path.join(dst_ledger, f)
        if os.path.isfile(s):
            shutil.copy2(s, d)
    shutil.rmtree(src_ledger, ignore_errors=True)
    try:
        os.rmdir(r"D:\Events\Sample_test\state")
    except Exception:
        pass

print("Migration complete successfully.")
