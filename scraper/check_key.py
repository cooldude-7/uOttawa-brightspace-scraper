r"""
Checks the API key without revealing it, so the output is safe to paste.

    python check_key.py
"""

import os
from pathlib import Path

import paths

HERE = Path(__file__).parent
KEY_FILE = paths.API_KEY


def mask(key):
    """Show enough to identify which key it is, not enough to use it."""
    if len(key) < 24:
        return f"{key[:8]}... (only {len(key)} chars -- too short)"
    return f"{key[:18]}...{key[-4:]}"


def describe(label, raw):
    if raw is None:
        print(f"{label}: not set")
        return None
    key = "".join(raw.split())
    print(f"{label}:")
    print(f"   raw length     {len(raw)}")
    print(f"   after cleanup  {len(key)}   (expected around 108)")
    print(f"   looks like     {mask(key)}")
    print(f"   starts right   {key.startswith('sk-ant-')}")
    inner = len(raw.strip()) - len(key)
    if inner:
        print(f"   !! {inner} space(s) or line break(s) INSIDE the key -- it is corrupted")
    return key


print("=" * 60)
if KEY_FILE.exists():
    text = KEY_FILE.read_text(encoding="utf-8", errors="replace")
    print(f"api_key.txt found, {len(text)} characters on disk")
    if text.count("\n") > 1:
        print(f"   !! file has {text.count(chr(10))} line breaks -- should be one line")
    file_key = describe("From api_key.txt", text)
else:
    print(f"api_key.txt NOT FOUND at {KEY_FILE}")
    print("   Files that are here:")
    for f in sorted(paths.DATA.glob("api_key*")):
        print(f"     {f.name}")
    file_key = None

print()
env_key = describe("From ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY"))

if file_key and env_key:
    print(f"\nFile and environment {'MATCH' if file_key == env_key else 'DIFFER'}.")
    print("The file wins -- the environment one is ignored.")

key = file_key or env_key
if not key:
    raise SystemExit("\nNo key found anywhere. Create api_key.txt with the key in it.")

print("\n" + "=" * 60)
print("Asking Anthropic whether this key works...")
try:
    import anthropic

    models = anthropic.Anthropic(api_key=key).models.list(limit=3)
    print("\nKEY WORKS. Models it can reach:")
    for m in models.data:
        print(f"   {m.id}")
except Exception as e:
    print(f"\nREJECTED: {e}")
    print("\nIf the length above is not ~108, the key got cut off or mangled")
    print("when copied. If the length looks right, the key was probably")
    print("revoked or belongs to an account with no credit -- make a new one")
    print("at https://console.anthropic.com and check Billing.")
