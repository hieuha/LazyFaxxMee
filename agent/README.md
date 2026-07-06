# FaxxMe printer agent (Raspberry Pi node)

> 🌐 Language: **English** · [Tiếng Việt](README-vi.md)

Turn any Raspberry Pi (or Linux box) with a thermal printer into your **printer node**:
run this small agent and every fax sent to your callsign prints on *your* printer — you
don't need a browser open, and it works from anywhere the Pi has internet.

It signs in with your **callsign + a device token** (never your password), over the same
WebSocket the web app uses. Faxes that arrive while the printer is unplugged are held and
printed the moment it's back.

```
 others ─▶ FaxxMe server ─(WebSocket, token auth)─▶ your Pi (agent) ─▶ /dev/usb/lp0 🖨
```

## 1. Get a device token (once, on the web)

1. Open the FaxxMe website and log in.
2. Scroll to **`:: PRINTER NODE`** → click **GENERATE TOKEN**.
3. Copy the token — it's shown **once**. (Lost it or leaked it? Just **REGENERATE** — the old
   one stops working immediately.)

## 2. Install on the Pi

Plug the thermal printer into the Pi, then:

```bash
git clone https://github.com/hieuha/LazyFaxxMee.git
cd LazyFaxxMee
sudo agent/install.sh
```

`install.sh` creates a virtualenv, installs `websockets`, sets up the printer udev rule +
`lp` group, and installs a `faxxme-agent` systemd service.

## 3. Configure

Put your server, callsign and token into the config file:

```bash
sudoedit agent/faxxme-agent.env
```

```ini
FAXXME_SERVER=https://your-faxxme-server.example   # the site you fax through
FAXXME_AGENT_USER=your_callsign
FAXXME_AGENT_TOKEN=paste-the-token-from-step-1
FAXXME_PRINTER_DEV=/dev/usb/lp0
```

Then apply it:

```bash
sudo systemctl restart faxxme-agent
```

## Manage & logs

```bash
systemctl status faxxme-agent
journalctl -u faxxme-agent -f          # live: connections, incoming faxes, prints
sudo systemctl stop|start|restart faxxme-agent
```

You should see `connected … authenticated as <callsign>`. Send yourself a fax from the web
and it prints on the Pi.

## Notes

- **Where does the token go?** Only in `agent/faxxme-agent.env` (`FAXXME_AGENT_TOKEN=`),
  file mode `600`. The agent never stores your password.
- **Revoke:** regenerate the token on the web — the running agent is **disconnected
  immediately** and can't reconnect until you paste the new token and `sudo systemctl restart
  faxxme-agent`. (On the web the printer reverts to OFFLINE and the WebUSB *Connect* button
  reappears.)
- **Printer offline:** unplug/replug is fine — the agent keeps queued faxes and prints them
  when `FAXXME_PRINTER_DEV` is writable again (retries every `FAXXME_PRINTER_POLL` seconds).
- **HTTPS:** use `https://` for `FAXXME_SERVER` over the internet (e.g. Tailscale / a TLS
  reverse proxy). Plain `http://` is fine on a trusted LAN.
- **Sending faxes** still happens in the web UI — the agent only *receives and prints*.
- **Printer support & widths:** see [../docs/printers.md](../docs/printers.md).

## Uninstall

```bash
sudo systemctl disable --now faxxme-agent
sudo rm /etc/systemd/system/faxxme-agent.service && sudo systemctl daemon-reload
```
