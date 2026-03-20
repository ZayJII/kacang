# 🥜 Peanut Mining Agent (Proof of Peanuts)

A robust, resilient Python-based mining agent for the [Proof of Peanuts ($PEANUT)](https://www.minepeanut.com/) protocol. Designed for 24/7 VPS operation with built-in rate-limiting handling and auto-recovery.

## Features

- ✅ **Resilient Mining**: Handles Supabase Error 1015 (Rate Limiting) with exponential backoff.
- ✅ **VPS Ready**: Built-in support for nonstop operation using `screen` or `pm2`.
- ✅ **Observability**: Periodic network status and epoch monitoring.
- ✅ **Graceful Shutdown**: Responsive to `Ctrl+C` for clean exits.
- ✅ **Easy Setup**: Secure local key generation.

## Quick Start

### 1. Installation

Clone the repo and install dependencies:
```bash
pip install -r requirements.txt
```

### 2. Key Generation

Generate your ED25519 keys (this will create `keys.json`, keep it safe!):
```bash
python keygen.py
```

### 3. Configuration

Copy the example config and add your **Agent ID** (choose any name) and **ETH Wallet Address**:
```bash
cp config.example.json config.json
```
*Note: Make sure your `eth_wallet` is correct to receive $PEANUT airdrops.*

### 4. Start Mining

```bash
python miner.py
```

## Advanced Usage (VPS 24/7)

We recommend using `pm2` to keep the miner running forever:
```bash
pm2 start miner.py --name peanut-miner --interpreter python3
```

## Security

- Your private keys are stored locally in `keys.json`.
- **NEVER** push `keys.json` or `config.json` to GitHub (they are already in `.gitignore`).
- Use separate wallets for mining if you worry about security.

## License

MIT - Feel free to use and update! 🥜
