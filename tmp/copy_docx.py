import shutil, os
src = "/docs/req/输入1-用户需求.docx"
dst = "/tmp/input1_user_requirements.docx"
shutil.copy2(src, dst)
print(f"Copied to {dst}, size={os.path.getsize(dst)}")
