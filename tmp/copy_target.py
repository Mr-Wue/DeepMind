import shutil, os
src = "/.files/b95c0124-6e1e-4031-8d8f-61c7153098bd/5c450aac-2ee8-4ee0-abf5-82b6de2b5f66.docx"
dst = "/tmp/target_doc.docx"
shutil.copy2(src, dst)
print(f"Copied to {dst}, size={os.path.getsize(dst)}")
