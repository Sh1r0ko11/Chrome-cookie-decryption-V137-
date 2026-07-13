# Code released! (tested on 7/13/2026 and fully working latest chrome version)

what i noticed in older version like: 
    Flag 0x01 (v127+): AES-256-GCM a hardcoded key in elevation_service.exe
    Flag 0x02 (v133+): ChaCha20-Poly130 hardcoded key in elevation_service.exe
    Flag 0x03 (v137+): AES-256-GCM with CNG-encrypted key from DPAPI blob decrypted via Microsoft's Key Storage Provider, then XORed with a hardcoded key

The blob also has a proper length-prefixed structure not just raw bytes at the end like used in python codes from "https://gist.github.com/thewh1teagle/d0bbc6bc678812e39cba74e1d407e5c7#file-main-py" who was updated 2 years ago and exactly made for the "v20" encryption system or whatever you wanna call it.

# The Problem
The original code hardcoded full[0] != 1 it only handled flag 0x01 Chrome 137+ uses flag 0x03 which has a completely different key derivation path.

| Original Code                                | Fixed Code                                                                                                                        |
| -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Blindly took last 61 bytes of decrypted blob | Properly parses length-prefixed blob structure with `parse_key_blob()`                                                            |
| Only handled flag `0x01` (AES-GCM)           | Handles all three flags: `0x01`, `0x02`, `0x03`                                                                                   |
| Single hardcoded AES key                     | Three keys: AES key, ChaCha20 key, and XOR key for CNG path                                                                       |
| No CNG support                               | Added `decrypt_with_cng_via_psexec()` using `NCryptDecrypt` with "Google Chromekey1" from Microsoft Software Key Storage Provider |

# Chrome Version → Flag Mapping

| Chrome Version | Flag   | Algorithm         | Key Source                             |
| -------------- | ------ | ----------------- | -------------------------------------- |
| 127–132        | `0x01` | AES-256-GCM       | Hardcoded in `elevation_service.exe`   |
| 133–136        | `0x02` | ChaCha20-Poly1305 | Hardcoded in `elevation_service.exe`   |
| 137+           | `0x03` | AES-256-GCM       | CNG-decrypted + XOR with hardcoded key |

The flag 0x03 path is the trickiest: after double DPAPI, the blob contains a 32-byte AES key that's encrypted with CNG. 
You need to call NCryptDecrypt using the "Google Chromekey1" persistent key that Chrome installs in the Windows Key Storage Provider then XOR the result with a hardcoded key from elevation_service.exe



# password & cookies decryption

*technically* with this script password and else could be extracted and decrypted, yes that means malware and stealer could use this code too because it shows and uses automation of decryption and extraction of chrome masters keys and some algorithm



and no this code WASNT vibecodet or amde by fucking Ai, fuck ai everything by me is selfmade, self-codet self writen, and yes the code fully by me, i always provide helpfull code, good explained because i want you to understand my code not to only copy and ,,TRY,, to understand it, i WANT you to


# chrome_exe.py

Why I Rewrote This From Scratch for .exe
The original script is solid. It decrypts Chrome v20 cookies with app-bound protection and covers all three flags. But I rebuilt the entire SYSTEM escalation layer from scratch because the original only works as a raw .py file.
Try compiling it with PyInstaller and everything explodes. pypsexec needs SMB. win32crypt needs pywin32. Good luck explaining pip install to someone who just wants to run a tool.

So I rewrote everything. One .exe. One PsExec.exe next to it. Zero Python installs. Zero headaches(pyinstaller). Because if you're shipping a tool like this you want it to just run.
Google called this "app-bound encryption" like they invented something new. It's just DPAPI in a trenchcoat. Two layers of the same Windows API they already had. The keys are still hardcoded in elevation_service.exe. 
The CNG key still lives in the Microsoft Software Key Storage Provider where SYSTEM can grab it. It's security theater with a bigger marketing budget. The "app-bound" part just means they made you click twice instead of once.

**What Changed (Complete Rewrite)**
|                   | **Original**                          | **My Rewrite**                               |
| ----------------- | ------------------------------------- | -------------------------------------------- |
| SYSTEM escalation | `pypsexec` over SMB                   | `PsExec.exe` native (rewritten from scratch) |
| Dependencies      | `pypsexec`, `pywin32`, `pycryptodome` | `pycryptodome` only                          |
| Helper delivery   | Temp `.py` via SMB pipe               | `.exe` calls **itself** with CLI flags       |
| Data to SYSTEM    | Base64 stdout                         | Binary files in `C:\Windows\Temp`            |
| Errors            | Swallowed by pipe buffer              | Full `traceback` to `.err` files             |
| Chrome path       | Hardcoded `%USERPROFILE%`             | Registry scan + multi-user fallback          |
| Frozen `.exe`?    | No                                    | Yes – built explicitly for this              |
| Webhook           | Hardcoded Discord URL                 | Removed – local JSON only                    |

The Technical Rewrite
I threw out the entire pypsexec architecture and built a "self-invocation system" tqhe main binary detects --system-dpapi and --system-cng flags in sys.argv. When PsExec launches it as SYSTEM with those flags it runs the decryption natively and writes the result to a file. No external scripts. No pipe buffers. No SMB. No Python interpreter needed.
The payload gets written to disk because passing 640 bytes of Base64 through a CLI argument makes Windows throw The parameter is incorrect. Files don't have that problem.
Google's "unbreakable" app-bound protection breaks in about 30 seconds with a Microsoft-signed tool. That's not a security model. That's just sad.


# Usage

Right now: Works on Chrome 127+ including the latest stable builds that use flag 0x03.
6 months from now? Maybe, depends on whether Google ships a flag 0x04 or rotates keys.
Long term: This is a cat-and-mouse game the script is a snapshot of the current state of Chrome's app_bound encryption, not a permanent bypass.

# Often asked question:
Why Python and not C++ or Rust? Simple I wanted this to be usable, not just fast.

Sure, I could ve built some slick memory scanner, injected a DLL, attached a debugger, or played games with Chrome's debug ports like the old tools did. But then youre stuck compiling for x64, fighting Windows Defender every time you breathe on ReadProcessMemory, and rewriting struct offsets every Tuesday when Google pushes a new build. That's not hacking thats maintenance hell.

Python lets you just pip install and run. No binaries, no architecture matching, no "why does Chrome crash when I look at it funny?" We leave Chrome completely alone and just ask Windows nicely to decrypt its own stuff through DPAPI and CNG. The codes messier, the scripts slower, but it works and it keeps working.
Sometimes the best tool is the one that doesnt make you want to throw your laptop out the window, asap



