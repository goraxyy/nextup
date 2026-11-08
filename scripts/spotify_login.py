"""Sign a Spotify account in for the stall Pi's shower music (experimental, MUSIC=spotify-web).

Run it on a laptop with a browser (no dependencies beyond Python):

    python scripts/spotify_login.py <client id> [spotify-token.json]

1. Create an app at https://developer.spotify.com/dashboard (Web API), with the redirect URI
   http://127.0.0.1:8888/callback, and copy its Client ID.
2. Run this; your browser opens Spotify's sign-in page. Approve, and the token file is written.
3. Copy it to the Pi:  scp spotify-token.json pi@<pi>:/var/lib/nextup/spotify-token.json

It uses the PKCE flow, so there's no client secret anywhere. The Pi only gets a refresh token
for these scopes: read what's playing, and play/pause/skip. Revoke it any time at
https://www.spotify.com/account/apps/.
"""

import base64
import hashlib
import http.server
import json
import pathlib
import secrets
import sys
import time
import urllib.parse
import urllib.request
import webbrowser

REDIRECT = "http://127.0.0.1:8888/callback"
SCOPES = "user-read-currently-playing user-read-playback-state user-modify-playback-state"


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    client_id = sys.argv[1].strip()
    out = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "spotify-token.json")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT, "scope": SCOPES,
        "code_challenge_method": "S256", "code_challenge": challenge, "state": state})

    got: dict = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in query.items()})
            ok = "code" in got and got.get("state") == state
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<h2>Signed in. You can close this tab.</h2>" if ok
                              else f"<h2>Sign-in failed: {got.get('error', 'unexpected reply')}</h2>").encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 8888), Callback)
    print("Opening Spotify's sign-in page. If it doesn't open, visit:\n" + url)
    webbrowser.open(url)
    server.timeout = 300
    server.handle_request()
    if got.get("state") != state or "code" not in got:
        raise SystemExit(f"Sign-in failed: {got.get('error', 'no code came back')}")

    body = urllib.parse.urlencode({"grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT,
                                   "client_id": client_id, "code_verifier": verifier}).encode()
    request = urllib.request.Request("https://accounts.spotify.com/api/token", data=body,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=15) as response:
        token = json.load(response)
    out.write_text(json.dumps({"client_id": client_id, "refresh_token": token["refresh_token"],
                               "scope": token.get("scope"), "created_at": int(time.time())}))
    print(f"Saved {out}. Next: scp {out} pi@<pi>:/var/lib/nextup/spotify-token.json")


if __name__ == "__main__":
    main()
