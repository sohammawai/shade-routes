"""Phase 9: build the AWS Lambda package for the backend.

Run from the backend folder:
    python build_lambda.py              code only; data is read from S3
    python build_lambda.py --with-data  also packs ../data/web (works without S3)

Creates shade-routes-api.zip. Lambda runs Linux, so pip downloads the Linux
versions of every library, even on Windows.
"""
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
ZIP_PATH = os.path.join(HERE, "shade-routes-api.zip")
PYTHON_VERSION = "3.12"                     # must match the Lambda runtime you choose
APP_FILES = ["main.py", "engine.py", "chat.py", "region_engine.py", "region_api.py", "region_chat.py"]   # region data comes from S3 (REGION_DB_S3)


def main():
    shutil.rmtree(BUILD, ignore_errors=True)
    print("Downloading Linux libraries for Lambda (about a minute)...")
    subprocess.run([
        sys.executable, "-m", "pip", "install",
        "-r", os.path.join(HERE, "requirements-lambda.txt"),
        "--target", BUILD,
        "--platform", "manylinux2014_x86_64",
        "--python-version", PYTHON_VERSION,
        "--implementation", "cp",
        "--only-binary=:all:",
        "--upgrade", "--quiet",
    ], check=True)

    for name in APP_FILES:
        shutil.copy(os.path.join(HERE, name), BUILD)
    if "--with-data" in sys.argv:
        src = os.path.join(HERE, "..", "data", "web")
        if not os.path.exists(os.path.join(src, "areas_index.json")):
            sys.exit("No data/web/areas_index.json yet. Run priority.py first.")
        shutil.copytree(src, os.path.join(BUILD, "data", "web"))
        print("Packed the area data into the zip")

    count = 0
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(BUILD):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                path = os.path.join(root, f)
                z.write(path, os.path.relpath(path, BUILD))      # main.py sits at the top of the zip
                count += 1
    size = os.path.getsize(ZIP_PATH) / 1e6
    print(f"Created {ZIP_PATH}: {count} files, {size:.1f} MB")
    print("Under 50 MB: upload it directly in the Lambda console." if size < 50 else
          "Over 50 MB: upload it to S3 first, then point Lambda at it.")


if __name__ == "__main__":
    main()
