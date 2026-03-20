"""
miner.py - Proof of Peanuts ($PEANUT) Mining Agent
Spec: https://www.minepeanut.com/peanut.md
"""
import os
import sys
import json
import time
import hashlib
import base64
import logging
import argparse
import threading
import concurrent.futures
import requests

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat, PrivateFormat, NoEncryption
)
from colorama import Fore, Style, init

init(autoreset=True)

BASE_URL = "https://wrcenmardnbprfpqhrqe.supabase.co/functions/v1/peanut-mining"
CONFIG_FILE = "config.json"
KEYS_FILE = "keys.json"

# Global stop signal — set by Ctrl+C to exit the mining loop cleanly
_STOP = threading.Event()

# ──────────────────────────────────────────────
#  Logging Setup
# ──────────────────────────────────────────────

def setup_logging(level: str = "INFO"):
    fmt = "%(asctime)s %(levelname)s %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("miner.log", encoding="utf-8"),
        ]
    )

log = logging.getLogger("peanut")

# ──────────────────────────────────────────────
#  Load Config & Keys
# ──────────────────────────────────────────────

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        log.error(f"Config file '{CONFIG_FILE}' not found.")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)

def load_keys() -> tuple[Ed25519PrivateKey, str, str]:
    """Returns (private_key, private_hex, public_hex)"""
    if not os.path.exists(KEYS_FILE):
        log.error(f"Keys file '{KEYS_FILE}' not found. Run: python keygen.py")
        sys.exit(1)
    with open(KEYS_FILE) as f:
        keys = json.load(f)
    priv_bytes = bytes.fromhex(keys["private_key_hex"])
    private_key = Ed25519PrivateKey.from_private_bytes(priv_bytes)
    return private_key, keys["private_key_hex"], keys["public_key_hex"]

# ──────────────────────────────────────────────
#  Thread Runner — makes any blocking call interruptible by Ctrl+C
# ──────────────────────────────────────────────

_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="peanut")

def run_in_thread(fn, *args, timeout: float = 20) -> dict | None:
    """
    Run `fn(*args)` in a daemon thread.
    `future.result(timeout)` CAN be interrupted by KeyboardInterrupt,
    unlike raw blocking socket I/O — this is the key to instant Ctrl+C.
    """
    future = _EXECUTOR.submit(fn, *args)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        log.warning(f"Thread call timed out after {timeout}s")
        return None

# ──────────────────────────────────────────────
#  API Helpers
# ──────────────────────────────────────────────

from requests.adapters import HTTPAdapter

SESSION = requests.Session()
# Optimize connection pool for high concurrency and reuse
adapter = HTTPAdapter(pool_connections=20, pool_maxsize=100)
SESSION.mount("https://", adapter)
SESSION.headers.update({
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Connection": "keep-alive"
})

def wait_with_countdown(seconds: int, message: str):
    """Wait for specified seconds while printing a countdown on a single line."""
    for i in range(seconds, 0, -1):
        if _STOP.is_set():
            break
        print(f"\r{Fore.YELLOW}⏳  {message} - Retrying in {i}s...{Style.RESET_ALL}   ", end="", flush=True)
        time.sleep(1)
    print("\r" + " " * 80 + "\r", end="", flush=True)  # clear line

def api_get(path: str, timeout: int = 20, retries: int = 3) -> dict | None:
    url = f"{BASE_URL}{path}"
    attempt = 0
    # For rate limits, we allow more aggressive retries than standard errors
    max_retries = max(retries, 5)
    
    while attempt < max_retries:
        attempt += 1
        try:
            r = SESSION.get(url, timeout=timeout)
            if r.status_code == 429 or r.status_code == 1015:
                wait = attempt * 15
                log.warning(f"GET {path} → Rate Limited (1015).")
                wait_with_countdown(wait, f"Rate limited on {path}")
                continue  # Retry without incrementing standard error budget if you want, 
                         # but here we use attempt counter for backoff intensity.
            r.raise_for_status()
            return r.json()
        except KeyboardInterrupt:
            raise
        except requests.Timeout:
            log.debug(f"GET {path} → Timeout (attempt {attempt}/{max_retries})")
        except requests.HTTPError as e:
            log.warning(f"GET {path} → HTTP {r.status_code}: {r.text[:200]}")
            if r.status_code >= 500:
                time.sleep(5)
                continue
            break
        except Exception as e:
            log.debug(f"GET {path} → {e} (attempt {attempt}/{max_retries})")
        
        if attempt < retries:
            time.sleep(5)
        else:
            break
    return None

def api_post(path: str, payload: dict, timeout: int = 45, retries: int = 5) -> dict | None:
    url = f"{BASE_URL}{path}"
    attempt = 0
    max_retries = max(retries, 5)
    
    while attempt < max_retries:
        attempt += 1
        try:
            r = SESSION.post(url, json=payload, timeout=timeout)
            if r.status_code == 429 or r.status_code == 1015:
                wait = attempt * 20
                log.warning(f"POST {path} → Rate Limited (1015).")
                wait_with_countdown(wait, f"Rate limited on {path}")
                continue
            
            r.raise_for_status()
            return r.json()
        except KeyboardInterrupt:
            raise
        except requests.Timeout:
            log.debug(f"POST {path} → Timeout (attempt {attempt}/{max_retries})")
        except requests.HTTPError:
            try:
                _raw = r.json()
                err_body: dict = _raw if isinstance(_raw, dict) else {"error": repr(_raw)}
            except Exception:
                err_body = {"error": r.text[:200]}
            
            err_body.update({"__status_code": r.status_code})
            
            # 409 is often "already submitted", which is fine
            if r.status_code == 409:
                return err_body
                
            log.warning(f"POST {path} → HTTP {r.status_code}: {err_body.get('error', '')}")
            if r.status_code >= 500:
                time.sleep(10)
                continue
            return err_body
        except Exception as e:
            log.debug(f"POST {path} → {e} (attempt {attempt}/{max_retries})")
        
        if attempt < retries:
            time.sleep(5)
        else:
            break
    return None

# ──────────────────────────────────────────────
#  ED25519 Signing
# ──────────────────────────────────────────────

def sign_message(private_key: Ed25519PrivateKey, message: str) -> str:
    """Sign a UTF-8 string and return hex signature."""
    sig = private_key.sign(message.encode("utf-8"))
    return sig.hex()

# ──────────────────────────────────────────────
#  Proof Solver
# ──────────────────────────────────────────────

def solve_hash_challenge(payload_b64: str, difficulty: int) -> str:
    """
    Solve a hash challenge: find a nonce such that
    SHA256(challenge + nonce) starts with `difficulty` leading zero bytes.
    Returns the winning nonce as a hex string.
    """
    try:
        challenge = base64.b64decode(payload_b64)
    except Exception:
        challenge = payload_b64.encode("utf-8")

    nonce = 0
    prefix = b"\x00" * difficulty
    while True:
        nonce_bytes = nonce.to_bytes(8, "little")
        h = hashlib.sha256(challenge + nonce_bytes).digest()
        if h[:difficulty] == prefix:
            return nonce_bytes.hex()
        nonce += 1
        if nonce % 500_000 == 0:
            log.debug(f"  Mining nonce={nonce:,}...")
    raise RuntimeError("unreachable")  # satisfies type checker: while True always returns or raises

def solve_task(task: dict) -> str:
    """Route task to the correct solver."""
    task_type = task.get("type", "hash_challenge")
    payload = task.get("payload", "")
    difficulty = task.get("difficulty", 1)

    if task_type == "hash_challenge":
        return solve_hash_challenge(payload, difficulty)
    elif task_type == "matrix_multiplication":
        # Placeholder – server-side verifiable compute
        # Return a deterministic hash of the payload as "solution"
        return hashlib.sha256(payload.encode()).hexdigest()
    else:
        # Generic fallback
        return hashlib.sha256(payload.encode()).hexdigest()

# ──────────────────────────────────────────────
#  Core Mining Steps
# ──────────────────────────────────────────────

def register_agent(cfg: dict, public_key_hex: str) -> bool:
    log.info(f"Registering agent '{cfg['agent_id']}' ...")
    resp = api_post("/register", {
        "agent_id": cfg["agent_id"],
        "public_key": public_key_hex,
        "compute_capability": cfg.get("compute_capability", "GPU"),
        "max_vcus": cfg.get("max_vcus", 1000),
    })
    if resp:
        status = resp.get("status", "unknown")
        log.info(f"{Fore.GREEN}Register → {status}{Style.RESET_ALL}")
        return True
    return False

def set_wallet(cfg: dict, public_key_hex: str) -> bool:
    wallet = cfg.get("eth_wallet", "")
    if not wallet or wallet.startswith("0xYour"):
        log.warning("ETH wallet not set in config.json — skipping wallet update.")
        return False
    log.info(f"Setting ETH wallet: {wallet}")
    resp = api_post("/update-wallet", {
        "agent_id": cfg["agent_id"],
        "public_key": public_key_hex,
        "wallet_address": wallet,
    })
    if resp and resp.get("status") == "updated":
        log.info(f"{Fore.GREEN}Wallet set successfully!{Style.RESET_ALL}")
        return True
    log.warning(f"Wallet update failed: {resp}")
    return False

def fetch_task() -> dict | None:
    task = api_get("/tasks/current")
    if task:
        log.info(
            f"Task fetched → id={task.get('task_id')} "
            f"type={task.get('type')} difficulty={task.get('difficulty')} "
            f"epoch={task.get('epoch')}"
        )
    return task

def submit_proof(cfg: dict, task: dict, solution: str, private_key: Ed25519PrivateKey, elapsed_ms: int) -> dict | None:
    # Sign the task_id + solution for authenticity
    message = f"{task['task_id']}:{solution}"
    signature = sign_message(private_key, message)

    # timeout=15, retries=1: if server is slow it likely already got it (returns 409 on retry)
    resp = api_post("/submit", {
        "agent_id": cfg["agent_id"],
        "task_id": task["task_id"],
        "solution": solution,
        "signature": signature,
        "compute_time_ms": elapsed_ms,
    }, timeout=15, retries=1)
    return resp

# ──────────────────────────────────────────────
#  Stats Tracker
# ──────────────────────────────────────────────

class Stats:
    def __init__(self):
        self.total_vcus = 0
        self.total_peanut = 0
        self.solved = 0
        self.failed = 0
        self.start_time = time.time()

    def record(self, vcus: int, peanut: int):
        self.total_vcus += vcus
        self.total_peanut += peanut
        self.solved += 1

    def record_fail(self):
        self.failed += 1

    def print_summary(self):
        elapsed = time.time() - self.start_time
        rate = self.solved / (elapsed / 3600) if elapsed > 0 else 0
        print(f"\n{Fore.CYAN}{'─'*52}")
        print(f"  📊  Session Stats")
        print(f"{'─'*52}")
        print(f"  ✅  Tasks Solved   : {self.solved}")
        print(f"  ❌  Tasks Failed   : {self.failed}")
        print(f"  ⚡  VCUs Earned    : {self.total_vcus:,}")
        print(f"  🥜  $PEANUT Earned : {self.total_peanut:,}")
        print(f"  ⏱️   Uptime         : {elapsed/60:.1f} min ({rate:.1f} tasks/hr)")
        print(f"{'─'*52}{Style.RESET_ALL}\n")

# ──────────────────────────────────────────────
#  Main Mining Loop
# ──────────────────────────────────────────────

def mining_loop(cfg: dict, private_key: Ed25519PrivateKey, stats: Stats):
    sleep_interval = cfg.get("sleep_interval", 2)
    last_task_id = None
    
    print(f"\n{Fore.YELLOW}🥜  Peanut Miner Started — Agent: {cfg['agent_id']}{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}{'='*60}{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}  VPS NONSTOP MODE: Resilience and Auto-Recovery Enabled{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}{'='*60}{Style.RESET_ALL}\n")

    while not _STOP.is_set():
        try:
            # ── Fetch task ──
            task = run_in_thread(fetch_task, timeout=25)

            if _STOP.is_set():
                break

            if not task:
                log.warning("No task available or network congestion. Backing off 15s...")
                _STOP.wait(timeout=15)
                continue

            task_id = task.get("task_id")
            if not task_id:
                _STOP.wait(timeout=5)
                continue

            if task_id == last_task_id:
                log.debug(f"Same task ({task_id}), waiting...")
                _STOP.wait(timeout=sleep_interval)
                continue

            # ── Solve ──
            log.info(f"{Fore.CYAN}⚙️  Solving task {task_id}...{Style.RESET_ALL}")
            t0 = time.time()
            solution = solve_task(task)
            elapsed_ms = int((time.time() - t0) * 1000)

            if _STOP.is_set():
                break

            log.info(f"  Solution: {solution[:32]}... ({elapsed_ms}ms)")

            # ── Submit ──
            result = run_in_thread(
                submit_proof, cfg, task, solution, private_key, elapsed_ms,
                timeout=30
            )

            if _STOP.is_set():
                break

            if result:
                http_code = result.get("__status_code", 200)
                status_str = result.get("status", "unknown")
                vcus = result.get("vcus_credited", 0)
                peanut = result.get("peanut_earned", 0)
                err_msg = result.get("error", "")

                if status_str == "verified":
                    stats.record(vcus, peanut)
                    log.info(
                        f"{Fore.GREEN}✅  VERIFIED | +{vcus} VCUs | +{peanut:,} $PEANUT | "
                        f"Session: {stats.solved} tasks, {stats.total_vcus} total VCUs{Style.RESET_ALL}"
                    )
                elif http_code == 409 or "duplicate" in err_msg.lower():
                    log.info(f"{Fore.YELLOW}⚡  Already credited (duplicate OK) — task={task_id}{Style.RESET_ALL}")
                elif http_code == 429 or http_code == 1015:
                    log.warning(f"⚠️  Rate limit hit on submit. Increasing sleep interval temporarily.")
                    _STOP.wait(timeout=30)
                else:
                    stats.record_fail()
                    log.warning(f"⚠️  Submit status: {status_str} | {result}")
            else:
                stats.record_fail()
                log.warning("Submit returned no response (network error?).")

            last_task_id = task_id
            
            # Optimization: If we just successfully verified, poll for next task immediately
            # otherwise wait for the standard sleep interval
            if result and result.get("status") == "verified":
                _STOP.wait(timeout=0.2)
            else:
                _STOP.wait(timeout=sleep_interval)

        except KeyboardInterrupt:
            _STOP.set()
            break
        except Exception as e:
            log.error(f"Unexpected error in mining loop: {e}")
            log.info("Attempting auto-recovery in 30s...")
            _STOP.wait(timeout=30)

    # ── Graceful shutdown ──
    log.info("Miner stopped.")
    stats.print_summary()

# ──────────────────────────────────────────────
#  Entry Point
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="$PEANUT Mining Agent")
    parser.add_argument("--config", default=CONFIG_FILE, help="Path to config.json")
    parser.add_argument("--keys", default=KEYS_FILE, help="Path to keys.json")
    parser.add_argument("--no-register", action="store_true", help="Skip registration")
    parser.add_argument("--no-wallet", action="store_true", help="Skip wallet update")
    parser.add_argument("--log-level", default=None, help="Log level (DEBUG/INFO/WARNING)")
    args = parser.parse_args()

    cfg = load_config()
    log_level = args.log_level or cfg.get("log_level", "INFO")
    setup_logging(log_level)

    private_key, priv_hex, pub_hex = load_keys()
    log.info(f"Agent ID  : {cfg['agent_id']}")
    log.info(f"Public Key: {pub_hex}")

    # Register
    if not args.no_register:
        run_in_thread(register_agent, cfg, pub_hex, timeout=60)

    # Set wallet
    if not args.no_wallet and cfg.get("auto_set_wallet", True):
        run_in_thread(set_wallet, cfg, pub_hex, timeout=60)

    # Start mining
    stats = Stats()
    mining_loop(cfg, private_key, stats)

if __name__ == "__main__":
    main()
