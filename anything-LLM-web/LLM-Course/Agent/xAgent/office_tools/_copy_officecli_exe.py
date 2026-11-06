# -*- coding: utf-8 -*-
"""把 officecli-win-x64.exe 复制到技能目录与用户指定目录，并校验。"""
import hashlib
import os
import shutil

try:
    sys_encoding = None
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SRC = r"F:\xie_teacher_documents\anything-LLM-web\office_tools\officecli-win-x64.exe"
TARGETS = [
    r"F:\xie_teacher_documents\anything-LLM-web\LLM-Course\Agent\xAgent\.agents\skills\officecli\officecli-win-x64.exe",
    r"F:\xie_teacher_documents\anything-LLM-web\office_tools\OfficeCLI\officecli-win-x64.exe",
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


print("源文件:", SRC)
print("源文件大小:", os.path.getsize(SRC))
src_digest = sha256(SRC)
print("源文件 SHA256:", src_digest)

for target in TARGETS:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copy2(SRC, target)
    ok = os.path.getsize(target) == os.path.getsize(SRC) and sha256(target) == src_digest
    print("已复制 ->", target)
    print("  大小:", os.path.getsize(target), "字节；SHA256 一致:", ok)

print("原文件仍存在:", os.path.isfile(SRC))
