"""Run ONCE on your own PC to get the 3 secrets for GitHub.
Needs client_secret.json (OAuth Desktop client) in the same folder.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(
    "client_secret.json",
    ["https://www.googleapis.com/auth/youtube.upload"],
)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

print("\nAdd these as GitHub Secrets:\n")
print("CLIENT_ID     =", creds.client_id)
print("CLIENT_SECRET =", creds.client_secret)
print("REFRESH_TOKEN =", creds.refresh_token)
