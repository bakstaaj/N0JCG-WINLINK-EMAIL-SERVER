# Operator authentication and software registration

## Operator authentication

The operator console is protected at the web-server boundary with Nginx Basic Auth. The password file is stored at `/etc/nginx/.htpasswd-n0jcg-winlink` and is never part of the repository or release archive.

On a first installation, the static UI installer automatically starts the
interactive operator setup after Nginx is configured. It asks for the username,
password, and password confirmation:

```bash
sudo apt-get install apache2-utils
sudo /path/to/N0JCG-WINLINK-EMAIL-SERVER/deploy/install_static_ui.sh
```

The setup requires a non-empty operator username and asks for the password twice
without echoing it. A mismatch stops the setup without changing the account. On
later upgrades, the installer detects the existing password file and preserves
the current operator account. To intentionally replace it, run
`sudo /opt/n0jcg-winlink/tools/setup_operator_auth.sh`.

Production deployment must complete this first-install setup before exposing
operator controls beyond the trusted setup network.

The initial protection boundary covers `/ui/`. Future configuration, diagnostics, registration, and RF-control API routes must use the same authenticated boundary and must not rely on browser-only hiding.

## Registration flow

The appliance-side registration foundation uses a stable product-scoped device identifier derived from the Pi machine ID. It does not expose the machine ID itself. The current flow is:

1. Display `Unregistered` in the operator console.
2. Run `python3 tools/registration.py id` to obtain the product device ID.
3. Run `python3 tools/registration.py create-request /path/registration-request.json`.
4. Submit the request through the future N0JCG registration service or approved offline process.
5. Import a signed activation response through a future authenticated management endpoint.
6. Show license ID, owner, activation date, expiry, and entitlements in the operator console.

Registration is an application entitlement only. It never proves DigiRig audio, PTT, radio connectivity, Packet RMS reachability, or RF safety, and it never enables transmission by itself.

The signature-verification boundary is intentionally not filled with an invented license key. The product owner must provide the authoritative registration service and signing public key before activation responses are accepted.
