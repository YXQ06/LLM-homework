# -*- coding: utf-8 -*-
"""仅重新复制第二个目标，并做大小+SHA256 校验（各算一次）。"""
import hashlib
import os
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = r"F:\xie_teacher_documents\anything-LLM-web\office_tools\officecli-win-x64.exe"
TARGET = r"F:\xie_teacher_documents\anything-LLM-web\office_tools\OfficeCLI\officecli-win-x64.exe"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


print("复制前目标大小:", os.path.getsize(TARGET) if os.path.isfile(TARGET) else "不存在", flush=True)
shutil.copyfile(SRC, TARGET)
print("复制后目标大小:", os.path.getsize(TARGET), "源文件大小:", os.path.getsize(SRC), flush=True)
print("目标 SHA256:", sha256(TARGET), flush=True)
print("源   SHA256:", sha256(SRC), flush=True)
print("一致:", sha256(TARGET) == sha256(SRC), flush=True)
