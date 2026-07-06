# Agent máy in FaxxMe (node trên Raspberry Pi)

> 🌐 Ngôn ngữ: [English](README.md) · **Tiếng Việt**

Biến bất kỳ Raspberry Pi (hoặc máy Linux) nào có gắn máy in nhiệt thành **node in** của bạn: chạy
agent nhỏ này và mọi bản fax gửi tới callsign của bạn sẽ in ra trên máy in *của bạn* — không cần mở
trình duyệt, và chạy được từ bất cứ đâu chiếc Pi có internet.

Nó đăng nhập bằng **callsign + một device token** (không bao giờ dùng mật khẩu của bạn), qua đúng cái
WebSocket mà web app dùng. Các bản fax đến trong lúc máy in đang bị rút ra sẽ được giữ lại và in đúng
lúc máy in trở lại.

```
 người khác ─▶ máy chủ FaxxMe ─(WebSocket, xác thực token)─▶ Pi của bạn (agent) ─▶ /dev/usb/lp0 🖨
```

## 1. Lấy một device token (một lần, trên web)

1. Mở website FaxxMe và đăng nhập.
2. Cuộn tới **`:: PRINTER NODE`** → bấm **GENERATE TOKEN**.
3. Sao chép token — nó chỉ hiện **một lần**. (Lỡ mất hay lộ token? Cứ **REGENERATE** — token cũ ngừng
   hoạt động ngay lập tức.)

## 2. Cài trên Pi

Cắm máy in nhiệt vào Pi, rồi:

```bash
git clone https://github.com/hieuha/LazyFaxxMee.git
cd LazyFaxxMee
sudo agent/install.sh
```

`install.sh` tạo một virtualenv, cài `websockets`, thiết lập udev rule máy in + nhóm `lp`, và cài một
dịch vụ systemd `faxxme-agent`.

## 3. Cấu hình

Đặt server, callsign và token của bạn vào file cấu hình:

```bash
sudoedit agent/faxxme-agent.env
```

```ini
FAXXME_SERVER=https://your-faxxme-server.example   # website bạn fax thông qua
FAXXME_AGENT_USER=your_callsign
FAXXME_AGENT_TOKEN=paste-the-token-from-step-1
FAXXME_PRINTER_DEV=/dev/usb/lp0
```

Rồi áp dụng:

```bash
sudo systemctl restart faxxme-agent
```

## Quản lý & xem log

```bash
systemctl status faxxme-agent
journalctl -u faxxme-agent -f          # trực tiếp: kết nối, fax đến, các lần in
sudo systemctl stop|start|restart faxxme-agent
```

Bạn sẽ thấy `connected … authenticated as <callsign>`. Tự gửi cho mình một bản fax từ web và nó in ra
trên chiếc Pi.

## Ghi chú

- **Token nằm ở đâu?** Chỉ trong `agent/faxxme-agent.env` (`FAXXME_AGENT_TOKEN=`), file mode `600`.
  Agent không bao giờ lưu mật khẩu của bạn.
- **Thu hồi:** tạo lại token trên web — agent đang chạy bị **ngắt kết nối ngay lập tức** và không thể
  kết nối lại cho tới khi bạn dán token mới vào rồi `sudo systemctl restart faxxme-agent`. (Trên web,
  máy in quay về OFFLINE và nút *Connect* WebUSB hiện lại.)
- **Máy in offline:** rút/cắm lại thoải mái — agent giữ các bản fax đang chờ và in chúng khi
  `FAXXME_PRINTER_DEV` lại ghi được (thử lại mỗi `FAXXME_PRINTER_POLL` giây).
- **HTTPS:** dùng `https://` cho `FAXXME_SERVER` khi đi qua internet (vd Tailscale / một reverse proxy
  có TLS). Plain `http://` là ổn trên một mạng LAN tin cậy.
- **Gửi fax** vẫn diễn ra trong web UI — agent chỉ *nhận và in*.
- **Hỗ trợ máy in & khổ giấy:** xem [../docs/vi/printers.md](../docs/vi/printers.md).

## Gỡ cài

```bash
sudo systemctl disable --now faxxme-agent
sudo rm /etc/systemd/system/faxxme-agent.service && sudo systemctl daemon-reload
```
