# SOON UPDATES + Code release

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
