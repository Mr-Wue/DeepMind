import os
print("CWD:", os.getcwd())
print("Files in /docs/req/:")
for f in os.listdir("/docs/req/"):
    fp = os.path.join("/docs/req/", f)
    print(f"  {f!r} -> exists={os.path.exists(fp)}, size={os.path.getsize(fp)}")
