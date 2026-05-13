import shutil, os
src = "/.files/34be0164-3b03-4ad4-b3b4-caf7b142e9f3/3053a621-7f8c-45f1-bb37-c9d9756ac226.docx"
dst = "/tmp/target_doc.docx"
shutil.copy2(src, dst)
print(f"Copied to {dst}, size={os.path.getsize(dst)}")
