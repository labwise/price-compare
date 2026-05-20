import os
import subprocess
import sys

port = os.environ.get("PORT", "8000")
subprocess.run([sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", port])
