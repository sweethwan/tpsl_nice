import jwt
import uuid
import time
import hashlib
import requests
import json
import urllib.parse
import config_manager
import sys

# Bithumb API V2 Endpoint
API_URL = "https://api.bithumb.com"

def get_jwt_token(access_key, secret_key, query_params=None):
    # 1. Prepare Nonce & Timestamp
    nonce = str(uuid.uuid4())
    timestamp = int(time.time() * 1000)
    
    # 2. Prepare Query Hash
    # If there are params, urlencode them (sorted is usually best practice)
    # Bithumb V2 docs: SHA512 of query string
    q_str = ""
    if query_params:
        q_str = urllib.parse.urlencode(query_params)
    
    query_hash = hashlib.sha512(q_str.encode('utf-8')).hexdigest()
    
    # 3. Payload
    payload = {
        'access_key': access_key,
        'nonce': nonce,
        'timestamp': timestamp,
        'query_hash': query_hash,
        'query_hash_alg': 'SHA512'
    }
    
    # 4. Sign
    # secret_key is likely just string for HS256/512? 
    # Bithumb docs usually say "HS256" or similar.
    # Docs say: Sign using Secret Key.
    encoded_token = jwt.encode(payload, secret_key, algorithm='HS512')
    return encoded_token

def test_v2_auth():
    print("="*50)
    print(" BITHUMB API V2 (JWT) DEBUGGER")
    print("="*50)
    
    try:
        config = config_manager.load_config()
        access_key = config['exchanges']['bithumb']['api_key'].strip()
        secret_key = config['exchanges']['bithumb']['secret_key'].strip()
    except Exception as e:
        print(f"Config Error: {e}")
        return

    print(f"Key Lengths: {len(access_key)} / {len(secret_key)}")
    
    # --- Test 1: Balance (Private) ---
    print("\n[Test 1] Fetching Balance (v1/my_wallet_balance)...")
    # Endpoint: /v1/my_wallet_balance (or similar V2 equivalent? Wait)
    # Bithumb V2 refers to "Connect API". 
    # Let's try matching the endpoint to what CCXT or docs say.
    # Actually, "info/balance" is V1. 
    # Search results implied "Connect API" uses JWT.
    
    # Let's try "/v1/accounts" or equivalent if documented, 
    # but since I don't have the exact endpoint list handy from search,
    # I will try the standard endpoint with JWT header to see if it accepts it.
    # Commonly: /v1/accounts
    
    # Actually, let's try reading the user's intent: they want to use 48/84 keys.
    # These MUST work with the V2 system.
    
    # URL Attempt 1: /v1/accounts (Common V2 pattern)
    target_path = "/v1/accounts"
    full_url = API_URL + target_path
    
    # Create Token
    token = get_jwt_token(access_key, secret_key)
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    
    try:
        res = requests.get(full_url, headers=headers)
        print(f"Response Code: {res.status_code}")
        print(f"Response Body: {res.text[:200]}...")
        
        if res.status_code == 404:
            print("(!) Endpoint not found. Trying /info/balance with JWT...")
            # Some exchanges allow JWT on old endpoints during transition
            full_url = API_URL + "/info/balance"
            res = requests.post(full_url, headers=headers, data={'currency': 'ALL'}) 
            # Note: info/balance usually expects Form Data signature, not JWT.
            print(f"Retry Code: {res.status_code}")
            print(f"Retry Body: {res.text[:200]}...")

    except Exception as e:
        print(f"Request Error: {e}")

if __name__ == "__main__":
    # Ensure pyjwt is installed
    try:
        import jwt
        test_v2_auth()
    except ImportError:
        print("Please install pyjwt: `pip install PyJWT`")
