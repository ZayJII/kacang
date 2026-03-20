"""
keygen.py - Generate ED25519 keypair for your Peanut Mining Agent
"""
import os
import json
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat, PrivateFormat, NoEncryption
)

KEYS_FILE = "keys.json"

def generate_keys():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    priv_bytes = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_bytes = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)

    keys = {
        "private_key_hex": priv_bytes.hex(),
        "public_key_hex": pub_bytes.hex()
    }

    with open(KEYS_FILE, "w") as f:
        json.dump(keys, f, indent=2)

    print("=" * 50)
    print("✅  ED25519 Keypair Generated!")
    print(f"📁  Saved to: {KEYS_FILE}")
    print(f"🔑  Public Key  : {pub_bytes.hex()}")
    print(f"🔒  Private Key : {priv_bytes.hex()}")
    print("=" * 50)
    print("⚠️   Keep your private key safe. Never share it!")
    return keys

if __name__ == "__main__":
    if os.path.exists(KEYS_FILE):
        print(f"⚠️  {KEYS_FILE} already exists. Delete it first to regenerate.")
    else:
        generate_keys()
